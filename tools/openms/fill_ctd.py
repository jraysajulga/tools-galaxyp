import collections
from functools import reduce  # forward compatibility for Python 3
import json
import operator
import os
import re
import subprocess
import sys

from CTDopts.CTDopts import (
    CTDModel,
    _Choices,
    _Null,
    _InFile,
    _NumericRange
)


def getFromDict(dataDict, mapList):
    return reduce(operator.getitem, mapList, dataDict)


def setInDict(dataDict, mapList, value):
    getFromDict(dataDict, mapList[:-1])[mapList[-1]] = value


def mergeDicts(d, e):
    """
    insert values from the dict e into dict d
    no values of d are overwritten
    """
    for k, v in e.items():
        if (k in d and isinstance(d[k], dict) and isinstance(e[k], collections.Mapping)):
            mergeDicts(d[k], e[k])
        elif k not in d and not isinstance(e[k], collections.Mapping):
            d[k] = e[k]
        else:
            sys.stderr.write("fill_ctd.py: could not merge key %s for %s in %s" % (k, d, e))
            sys.exit(1)


def _json_object_hook_noenvlookup(d):
    return _json_object_hook(d, envlookup=False)


def _json_object_hook(d, envlookup=True):
    """
    wee helper to transform the json written by galaxy
    while loading
    - True/False (bool objects) -> "true"/"false" (lowercase string)
    - data inputs with multiple and optional true give [None] if no file is given -> []
    - None -> "" (empty string)
    - replace bash expressions (if envlookup is True):
      - environment variables (need to consist capital letters and _) by their value
      - expressions
    """
    for k in d.keys():
        # if type(d[k]) is bool:
        #     d[k] = str(d[k]).lower()
        # else
        if type(d[k]) is list and len(d[k]) == 1 and d[k][0] is None:
            d[k] = []
        elif d[k] is None:
            d[k] = ""
        elif envlookup and type(d[k]) is str and d[k].startswith("$"):
            m = re.fullmatch(r"\$([A-Z_]+)", d[k])
            if m:
                d[k] = os.environ.get(m.group(1), "")
                continue
            m = re.fullmatch(r"\$(\{[A-Z_]+):-(.*)\}", d[k])
            if m:
                d[k] = os.environ.get(m.group(1), m.group(2))
                continue

            try:
                p = subprocess.run("echo %s" % d[k], shell=True, check=True, stdout=subprocess.PIPE, encoding="utf8")
                d[k] = p.stdout.strip()
            except subprocess.CalledProcessError:
                sys.stderr.write("fill_ctd error: Could not evaluate %s" % d[k])
                continue
    return d


def qstring2list(qs):
    """
    transform a space separated string that is quoted by " into a list
    """
    lst = list()
    qs = qs.split(" ")
    quoted = False
    for p in qs:
        if p == "":
            continue
        if p.startswith('"') and p.endswith('"'):
            lst.append(p[1:-1])
        elif p.startswith('"'):
            quoted = True
            lst.append(p[1:] + " ")
        elif p.endswith('"'):
            quoted = False
            lst[-1] += p[:-1]
        else:
            if quoted:
                lst[-1] += p + " "
            else:
                lst.append(p)
    return lst


input_ctd = sys.argv[1]

# load user specified parameters from json
with open(sys.argv[2]) as fh:
    args = json.load(fh, object_hook=_json_object_hook_noenvlookup)

# load hardcoded parameters from json
with open(sys.argv[3]) as fh:
    hc_args = json.load(fh, object_hook=_json_object_hook)

# insert the hc_args into the args
mergeDicts(args, hc_args)


if "adv_opts_cond" in args:
    args.update(args["adv_opts_cond"])
    del args["adv_opts_cond"]

model = CTDModel(from_file=input_ctd)

# transform values from json that correspond to
# - old style booleans (string + restrictions) -> transformed to a str
# - unrestricted ITEMLIST which are represented as strings
#   ("=quoted and space separated) in Galaxy -> transform to lists
# - optional data input parameters that have defaults and for which no
#   value is given -> overwritte with the default
for p in model.get_parameters():

    # check if the parameter is in the arguments from the galaxy tool
    # (from the json file(s)), since advanced parameters are absent
    # if the conditional is set to basic parameters
    try:
        getFromDict(args, p.get_lineage(name_only=True))
    except KeyError:
        # few tools use dashes in parameters which are automatically replaced
        # by underscores by Galaxy. in these cases the dictionary needs to be
        # updated
        # TODO might be removed later https://github.com/OpenMS/OpenMS/pull/4529
        try:
            lineage = [_.replace("-", "_") for _ in p.get_lineage(name_only=True)]
            val = getFromDict(args, lineage)
        except KeyError:
            continue
        else:
            setInDict(args, lineage, val)

    if p.type is str and type(p.restrictions) is _Choices and set(p.restrictions.choices) == set(["true", "false"]):
        v = getFromDict(args, p.get_lineage(name_only=True))
        setInDict(args, p.get_lineage(name_only=True), str(v).lower())

    elif p.is_list and (p.restrictions is None or type(p.restrictions) is _NumericRange):
        v = getFromDict(args, p.get_lineage(name_only=True))
        if type(v) is str:
            setInDict(args, p.get_lineage(name_only=True), qstring2list(v))
    elif p.type is _InFile and not (p.default is None or type(p.default) is _Null):
        v = getFromDict(args, p.get_lineage(name_only=True))
        if v in [[], ""]:
            setInDict(args, p.get_lineage(name_only=True), p.default)

model.write_ctd(input_ctd, arg_dict=args)

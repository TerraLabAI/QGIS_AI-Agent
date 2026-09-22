# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







































from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Callable

R = "R"
P = "P"
FP = "FP"
FW = "FW"
D = "D"
ASK = "ASK"
RANK = {R: 0, P: 1, FP: 2, FW: 3, D: 4, ASK: 5}

DANGER = {R: "read", P: "write", FP: "write", FW: "destructive", D: "destructive", ASK: "destructive"}


def higher(a: str, b: str) -> str:
    return a if RANK.get(a, RANK[ASK]) >= RANK.get(b, RANK[ASK]) else b





_R_MODULES = frozenset({
    "qgis", "qgis.core", "qgis.gui", "qgis.PyQt", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui",
    "collections", "datetime", "itertools", "json", "math", "re", "statistics", "random", "string",
    "functools", "csv", "decimal", "fractions", "time", "textwrap", "unicodedata", "copy", "heapq",
    "bisect", "numbers", "typing", "dataclasses", "enum", "uuid", "os.path", "numpy", "io",





    "shapely", "shapely.geometry", "shapely.ops", "shapely.affinity", "shapely.prepared",
    "shapely.strtree", "shapely.validation", "shapely.wkt", "shapely.wkb",
    "PyQt5.QtCore", "PyQt5.QtGui", "PyQt6.QtCore", "PyQt6.QtGui",
})
_P_MODULES = frozenset({"processing"})




_FILE_MODULES = frozenset({"os", "shutil", "pathlib", "glob", "tempfile", "zipfile", "osgeo", "osgeo.gdal",
                           "osgeo.ogr", "osgeo.osr", "sys",
                           "matplotlib", "matplotlib.pyplot", "matplotlib.figure", "matplotlib.colors",
                           "matplotlib.ticker", "matplotlib.dates", "matplotlib.patches", "matplotlib.cm",
                           "PIL", "PIL.Image", "PIL.ImageDraw", "PIL.ImageFont", "PIL.ImageOps",
                           "pypdf"})



_LIBRARY_MODULES = frozenset({"numpy", "matplotlib", "PIL", "pypdf", "shapely"})






_R_NAMES = frozenset({
    "abs", "all", "any", "bool", "bytes", "callable", "chr", "dict", "dir", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "hasattr", "hash", "hex", "id", "int", "isinstance", "issubclass", "iter",
    "len", "list", "map", "max", "min", "next", "oct", "ord", "pow", "print", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "type", "zip", "object", "super",
    "Exception", "ValueError", "RuntimeError", "KeyError", "TypeError", "IndexError", "StopIteration",
    "defaultdict", "Counter", "OrderedDict", "namedtuple", "deque",

    "layer", "layers", "read_text", "validate_path",
    "StringIO", "BytesIO",


    "Path",
})
_P_NAMES: frozenset = frozenset()

_FP_NAMES = frozenset({"QgsVectorFileWriter", "open"})


_R_ATTRS = frozenset({

    "add", "append", "clear", "copy", "discard", "extend", "insert", "pop", "popitem", "remove",
    "reverse", "setdefault", "sort", "update", "keys", "values", "items", "get", "count", "format", "title",
    "difference", "intersection", "endswith", "startswith",
    "find", "rfind", "index", "join", "lower", "upper", "capitalize", "casefold", "replace",
    "split", "rsplit", "splitlines", "strip", "lstrip", "rstrip", "zfill", "ljust", "rjust", "isdigit", "isalpha",
    "isalnum", "isnumeric", "encode", "decode", "most_common", "elements",
    "isoformat", "strftime", "strptime", "timestamp", "date", "total_seconds", "now", "today",
    "fromisoformat", "fromtimestamp", "group", "groups", "groupdict", "match", "search", "fullmatch",
    "findall", "finditer", "sub", "subn", "compile", "escape",
    "dumps", "loads", "load", "read", "readline", "readlines", "close", "seek", "tell", "getvalue",
    "DictReader", "reader", "Sniffer", "sniff", "has_header",
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "sinh", "cosh", "tanh", "radians", "degrees",
    "hypot", "sqrt", "exp", "log", "log10", "log2", "floor", "ceil", "fabs", "isfinite", "isnan", "isinf",
    "isclose", "prod", "fsum", "copysign", "trunc", "dist", "pow", "comb", "perm", "gcd",
    "mean", "median", "sum", "fmean", "pstdev", "stdev", "variance", "pvariance", "quantiles", "mode",
    "median_low", "median_high",
    "random", "randint", "uniform", "choice", "choices", "shuffle", "seed", "gauss", "randrange",
    "chain", "groupby", "product", "combinations", "permutations", "islice", "accumulate", "zip_longest",
    "cycle", "repeat", "starmap", "tee", "pairwise", "reduce", "partial", "lru_cache",
    "cmp_to_key", "deepcopy", "heappush", "heappop", "nlargest", "nsmallest", "bisect_left",
    "bisect_right", "uuid4", "hex",

    "array", "asarray", "zeros", "ones", "full", "arange", "linspace", "where", "nanmean", "nanmin",
    "nanmax", "nansum", "percentile", "histogram", "unique", "reshape", "astype", "norm", "min", "max",
    "std", "argmin", "argmax", "flatten", "ravel", "tolist", "any", "all", "abs",

    "exists", "isfile", "isdir", "getsize", "getmtime", "basename", "dirname", "splitext", "abspath",
    "normpath", "realpath", "relpath", "expanduser", "listdir", "scandir", "walk", "stat", "glob",
    "iglob", "is_file", "is_dir", "iterdir", "read_text", "read_bytes", "resolve", "with_suffix",
    "with_name", "suffix", "stem", "gettempdir", "namelist", "infolist", "getinfo",


    "open",

    "validate_path",
})
_P_ATTRS: frozenset = frozenset()

_FP_ATTRS = frozenset({
    "writeAsVectorFormat", "writeAsVectorFormatV2", "writeAsVectorFormatV3", "writeRaster", "create",
    "saveNamedStyle", "saveSldStyle", "saveAsImage", "exportToImage", "exportToPdf", "exportToPdfs",
    "exportToSvg", "print", "write", "writerow", "writerows", "writer", "makedirs", "mkdir", "copy",
    "copy2", "copyfile", "extract", "extractall", "writestr", "mkdtemp", "NamedTemporaryFile",
    "TemporaryDirectory", "savetxt", "savefig", "imsave", "write_text", "write_bytes",


    "Translate", "Warp", "VectorTranslate", "BuildVRT", "Create", "CreateCopy", "CreateDataSource",
})


_FW_ATTRS = frozenset({"save", "saveDefaultStyle", "saveStyleToDatabase", "writeLayerXml", "setFileName"})

_ROW_DELETES = frozenset({"deleteFeature", "deleteFeatures", "deleteSelectedFeatures", "deleteAttribute",
                          "deleteAttributes"})

_D_ATTRS = frozenset({"removeAllMapLayers", "deleteShapeFile", "deleteStyleFromDatabase", "clear_project"})

_UNPROVABLE = frozenset({
    "__import__", "breakpoint", "compile", "delattr", "eval", "exec", "getattr", "globals", "input",
    "locals", "memoryview", "setattr", "vars", "QTimer", "QThread", "QEventLoop", "threading",
    "QgsTask", "QgsApplication",
})

_DEFERRED_ATTRS = frozenset({"connect", "singleShot", "start", "exec", "exec_", "processEvents",
                             "addTask", "setInterval", "callLater"})





_NAME_DUNDERS = frozenset({"__name__", "__qualname__", "__module__", "__doc__", "__version__"})


SHIPPED_TABLES = {
    "r_modules": _R_MODULES, "p_modules": _P_MODULES, "file_modules": _FILE_MODULES,
    "library_modules": _LIBRARY_MODULES,
    "r_names": _R_NAMES, "p_names": _P_NAMES, "fp_names": _FP_NAMES,
    "r_attrs": _R_ATTRS, "p_attrs": _P_ATTRS, "fp_attrs": _FP_ATTRS, "fw_attrs": _FW_ATTRS, "d_attrs": _D_ATTRS,
    "row_deletes": _ROW_DELETES, "unprovable": _UNPROVABLE, "deferred_attrs": _DEFERRED_ATTRS,
}





_NAME_LADDER = {"r_names": 0, "p_names": 1, "fp_names": 2}
_ATTR_LADDER = {"r_attrs": 0, "p_attrs": 1, "fp_attrs": 2, "fw_attrs": 3, "d_attrs": 4}

_SERVED: list = [(), None]


def merged_tables(*served: dict) -> dict:

    sources = (SHIPPED_TABLES, *(doc for doc in served if isinstance(doc, dict)))
    out = {name: set(table) for name, table in SHIPPED_TABLES.items()}
    for ladder in (_NAME_LADDER, _ATTR_LADDER):
        rank: dict[str, int] = {}
        for source in sources:
            for table, level in ladder.items():
                for name in source.get(table) or ():
                    rank[name] = max(rank.get(name, level), level)
        for table in ladder:
            out[table] = set()
        by_level = {level: table for table, level in ladder.items()}
        for name, level in rank.items():
            out[by_level[level]].add(name)
    for table in SHIPPED_TABLES:
        if table not in _NAME_LADDER and table not in _ATTR_LADDER:
            for source in sources[1:]:
                out[table] |= set(source.get(table) or ())
    return {name: frozenset(table) for name, table in out.items()}


def tables() -> dict:





    try:
        from . import tuning
    except ImportError:
        docs: tuple = ()
    else:
        docs = (tuning.service_doc("code_tables"), tuning.service_doc("code_classes"))
    if _SERVED[1] is None or len(docs) != len(_SERVED[0]) or any(a is not b for a, b in zip(docs, _SERVED[0])):
        _SERVED[:] = [docs, merged_tables(*docs)]
    return _SERVED[1]


@dataclass
class Verdict:


    cls: str
    reasons: list = field(default_factory=list)
    unknown: list = field(default_factory=list)




    floor: str = R
    unprovable: bool = False

    @property
    def danger(self) -> str:
        return DANGER[self.cls]



def _words(name: str) -> str:


    name = name[3:] if name.startswith("Qgs") else name
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name.strip("_.")).replace("_", " ").lower()


def _callee(node: ast.Call) -> tuple[str, str]:

    func = node.func
    if isinstance(func, ast.Name):
        return "name", func.id
    if isinstance(func, ast.Attribute):
        return "attr", func.attr
    return "expr", ""


def _library_names(tree: ast.AST, libraries: frozenset) -> set:














    bound: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom):
                if str(node.module or "").split(".")[0] not in libraries:
                    continue
                bound.update(str(a.asname or a.name or "") for a in node.names)
                continue
            for alias in node.names:
                name = str(alias.name or "")
                if name.split(".")[0] in libraries:
                    bound.add(str(alias.asname or name.split(".")[0]))
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Call, ast.Subscript)):
                source, targets = node.value, node.targets
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                source, targets = node.iter, [node.target]
            else:
                continue
            if _receiver_root(source) not in bound:
                continue
            for target in _flatten(targets):
                if isinstance(target, ast.Name):
                    bound.add(target.id)
    bound.discard("")
    return bound


def _literal_lookup(node: ast.Call) -> str:

    args = node.args
    if (not isinstance(node.func, ast.Name) or node.func.id != "getattr" or node.keywords
            or len(args) not in (2, 3) or any(isinstance(a, ast.Starred) for a in args)):
        return ""
    name = args[1]
    if isinstance(name, ast.Constant) and isinstance(name.value, str) and name.value.isidentifier():
        return name.value
    return ""


def _receiver_root(node: ast.AST) -> str:
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else ""


def _string_constants(tree: ast.AST) -> list[str]:
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _open_writes(node: ast.Call) -> bool:

    mode = node.args[1] if len(node.args) > 1 else next((k.value for k in node.keywords if k.arg == "mode"), None)
    if mode is None:
        return False
    if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
        return True
    return any(flag in mode.value for flag in "wax+")


def _local_functions(tree: ast.AST) -> set[str]:

    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Lambda):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _local_classes_methods(tree: ast.AST) -> set[str]:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            names.update(item.name for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)))
    return names


def classify(code: object, tool_class: Callable[[str], str | None] | None = None,
             commit_class: Callable[[list, bool], str | tuple] | None = None,
             libraries: frozenset | set | tuple = ()) -> Verdict:












    text = str(code or "").strip()
    if not text:
        return Verdict(ASK, ["the code is empty"])
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return Verdict(ASK, ["the code has a syntax error"])
    level = R
    found: list[tuple[str, str]] = []
    unknown: list[str] = []
    doors = False
    t = tables()
    locals_ = _local_functions(tree) | _local_classes_methods(tree)
    libraries = _library_names(tree, frozenset(t.get("library_modules") or ()) | frozenset(libraries))
    commits = False


    deletes = False

    def reach(cls: str, reason: str) -> None:
        nonlocal level
        if RANK[cls] > RANK[level]:
            level = cls
        if cls != R and (cls, reason) not in found:
            found.append((cls, reason))

    def method(root: str, name: str, handed_on: bool) -> None:




        nonlocal commits, deletes, doors
        if root == "tools":
            cls = tool_class(name) if tool_class is not None else None
            if cls is None:
                unknown.append(f"tools.{name}")
            else:
                reach(cls, "runs another agent tool")
        elif root in t["unprovable"] or name in t["deferred_attrs"]:
            if not handed_on:
                unknown.append(f"{root + '.' if root else ''}{name}")
                doors = True
        elif root in libraries and name not in t["d_attrs"] and name not in t["fw_attrs"]:


            if name in t["fp_attrs"]:
                reach(FP, "writes a file")
        elif name in t["d_attrs"]:
            reach(D, "deletes data")
        elif name in t["fw_attrs"]:
            reach(FW, "writes over a file")
        elif name == "commitChanges":
            commits = True
        elif name in t["r_attrs"] or name in locals_:
            pass
        elif name in t["p_attrs"]:
            reach(P, _words(name))
            deletes = deletes or name in t["row_deletes"]
        elif handed_on:
            if name in t["fp_attrs"]:
                reach(FP, "writes a file")
        elif name.startswith("Qgs"):
            reach(P, f"creates a {_words(name)}")
        elif name in t["fp_attrs"]:
            reach(FP, "writes a file")
        else:
            unknown.append(name)


    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    lookups = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call) and _literal_lookup(n)}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.level:
                unknown.append("a relative import")
                continue
            modules = [str(node.module or "")] if isinstance(node, ast.ImportFrom) else [
                str(a.name or "") for a in node.names]
            for module in modules:
                if module in t["r_modules"] or module.split(".")[0] == "qgis":
                    continue
                if module in t["p_modules"] or module in t["file_modules"]:
                    continue
                unknown.append(f"import {module}")
        elif isinstance(node, ast.Call):
            kind, callee = _callee(node)
            if kind == "expr":

                unknown.append("a call through an expression")
            elif kind == "name":
                if id(node.func) in lookups:
                    name = _literal_lookup(node)
                    if name.startswith("__"):
                        unknown.append(f"getattr {name}")
                    else:
                        method(_receiver_root(node.args[0]), name, handed_on=True)
                elif callee in t["unprovable"]:
                    unknown.append(callee)
                    doors = True
                elif callee == "open":
                    if _open_writes(node):
                        reach(FP, "opens a file to write it")
                elif callee in t["r_names"] or callee in locals_:
                    pass
                elif callee in t["p_names"]:
                    reach(P, _words(callee))
                elif callee in t["fp_names"]:
                    reach(FP, "writes a file")
                elif callee in libraries:


                    if callee in t["fp_attrs"]:
                        reach(FP, "writes a file")
                elif callee.startswith("Qgs"):

                    reach(P, f"creates a {_words(callee)}")
                else:
                    unknown.append(callee)
            elif not callee.startswith("__"):
                method(_receiver_root(node.func.value), callee, handed_on=False)
        elif isinstance(node, ast.With):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and _callee(call) == ("name", "edit"):
                    commits = True
        elif isinstance(node, ast.Attribute):
            load = isinstance(node.ctx, ast.Load)
            if node.attr.startswith("__"):
                if node.attr not in _NAME_DUNDERS or not load or id(node) in called:
                    unknown.append(f".{node.attr}")
            elif load and id(node) not in called:
                method(_receiver_root(node.value), node.attr, handed_on=True)
        elif isinstance(node, ast.Name):
            if id(node) in lookups:
                pass
            elif node.id in t["unprovable"] and isinstance(node.ctx, ast.Load):

                if node.id not in unknown:
                    unknown.append(node.id)
                doors = True
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
            targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
            for target in _flatten(targets):
                if isinstance(target, ast.Attribute):
                    reach(P, f"sets {_words(target.attr)}")
        elif isinstance(node, (ast.Await, ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith,
                               ast.Global, ast.Nonlocal)):
            unknown.append(type(node).__name__.lower())
    if commits:
        answer = commit_class(_string_constants(tree), deletes) if commit_class is not None else FW
        cls, reason = answer if isinstance(answer, tuple) else (answer, "")
        if cls == FW and deletes:
            cls = D
        reach(cls, reason or {P: "commits an edit", D: "deletes features from a layer's file"}.get(
            cls, "saves an edit into a layer's file"))
    if unknown:
        seen: list[str] = []
        for item in unknown:
            if item not in seen:
                seen.append(item)

        return Verdict(ASK, ["may change things the plugin cannot preview"], seen, level, doors)

    return Verdict(level, [reason for cls, reason in found if cls == level])


def _flatten(targets) -> list:
    out = []
    for target in targets:
        if isinstance(target, (ast.Tuple, ast.List)):
            out.extend(_flatten(target.elts))
        elif isinstance(target, ast.Starred):
            out.extend(_flatten([target.value]))
        else:
            out.append(target)
    return out



# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











































from __future__ import annotations

import ast
import builtins as py_builtins
import contextlib
import ctypes
import errno
import functools
import io
import os
import re
import shutil
import threading
import time
import types
from typing import Any, Callable

from .code_guard_files import GUARDED_MODULES
from .serialization import coded_like

MAX_OUTPUT_CHARS = 200_000

DENIED_MODULES = frozenset({
    "subprocess", "ctypes", "socket", "multiprocessing", "importlib", "signal", "pty", "tty", "marshal",
    "_thread", "winreg", "keyring", "code", "pdb", "runpy", "smtplib", "ftplib", "telnetlib", "socketserver",
    "http.server", "xmlrpc", "ssl", "asyncio", "selectors", "select", "resource", "gc", "inspect", "dis",
    "webbrowser", "pickle", "shelve", "codecs", "zipimport", "pkgutil", "imp", "_io", "_socket",
    "_ctypes", "_posixsubprocess", "posix", "nt", "msvcrt", "fcntl", "mmap", "faulthandler", "sysconfig",
    "site", "setuptools", "pip", "ensurepip", "venv", "distutils", "requests", "urllib3", "httpx", "aiohttp",
    "http.client", "urllib.request", "fileinput", "linecache",





    "sqlite3", "_sqlite3", "dbm", "numpy.ctypeslib", "cffi", "_cffi_backend",


    "tarfile", "gzip", "bz2", "lzma", "zlib", "configparser", "plistlib",


    "threading", "concurrent", "concurrent.futures", "sched", "queue",








    "builtins", "operator", "types", "weakref", "copyreg", "_frozen_importlib", "_frozen_importlib_external",




    "_winapi", "_winreg", "_overlapped", "_wmi", "wmi", "winsound", "msilib", "_msi", "_multiprocessing",
    "pythoncom", "pywintypes", "pythonwin", "win32ui", "dde", "comtypes", "servicemanager", "mmapfile", "odbc",
    "perfmon", "timer", "adodbapi", "isapi", "pywin32_system32", "pywin32_bootstrap", "pywin32_postinstall",

    "_pyio",

    "httplib2", "owslib", "pyodbc", "psycopg", "psycopg2",



    "timeit", "cProfile", "profile", "trace", "doctest", "pydoc", "logging.config", "bdb", "cmd",


    "qgis.utils",


    "gdal", "ogr", "osr", "gdalconst", "gdal_array",
})
_DENIED_MODULE_PREFIXES = ("win32", "_win32")
DENIED_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "breakpoint", "input", "exit", "quit", "help", "vars", "memoryview",



    "globals", "locals",
    "QgsSettings", "QSettings", "QgsAuthMethodConfig", "QgsAuthManager", "QgsAuthConfigSslServer",
    "QgsNetworkAccessManager", "QNetworkAccessManager", "QNetworkRequest", "QProcess", "QFile", "QDir",
    "QSaveFile", "QTemporaryFile",



    "QgsBlockingNetworkRequest", "QgsNetworkContentFetcher", "QgsFileDownloader", "QgsFileDownloaderDialog",

    "QDesktopServices",



    "QgsProcessingModelAlgorithm", "QgsProcessingModelChildAlgorithm", "QgsProcessingRegistry",
    "QgsNativeAlgorithms", "Qgs3DAlgorithms", "QgsProcessingAlgRunnerTask", "QgsProcessingAlgorithm",
    "QgsProcessingProvider",


    "QDirIterator", "QFileSystemModel", "QFileSystemWatcher", "QgsDirectoryItem", "QgsDirectoryParamWidget",
    "QgsBrowserModel", "QgsBrowserGuiModel", "QgsBrowserProxyModel",
})
DENIED_ATTRS = frozenset({
    "authManager", "masterPasswordIsSet", "setMasterPassword", "authMethodConfig", "storeAuthenticationConfig",
    "loadAuthenticationConfig", "environ", "getenv", "putenv", "unsetenv", "system", "popen", "execv", "execve",
    "execl", "execlp", "execvp", "execvpe", "spawnl", "spawnv", "spawnve", "spawnlp", "spawnvp", "fork",
    "forkpty", "modules", "_getframe", "settrace", "setprofile", "b64decode", "fromhex", "decodebytes",
    "a85decode", "b32decode", "b16decode", "unlink", "rmdir", "removedirs", "rmtree", "fdopen",










    "setValue", "activation_key", "clear_activation_key", "set_activation_key",
    "load_module", "import_module", "startDetached", "excepthook", "displayhook", "gi_frame", "f_globals",
    "f_locals", "f_back", "tb_frame", "cr_frame", "ag_frame", "os", "genericpath",


    "sys", "builtins", "subprocess", "socket", "ctypes", "zipfile", "tempfile", "ctypeslib", "importlib", "CDLL",
    "WinDLL", "PyDLL",
    "OleDLL", "cdll", "windll", "pydll", "LibraryLoader", "load_extension", "enable_load_extension",
    "openUrl", "openURL", "startfile", "add_dll_directory", "FileIO", "CreateProcess", "ShellExecute",
    "ShellExecuteEx", "WinExec",

    "unsafe_load", "unsafe_load_all", "UnsafeLoader", "Loader",

    "from_file",

    "utils",


    "processingRegistry", "algorithmById", "createAlgorithmById",


    "dictConfig", "fileConfig",


    "dropVectorTable", "dropRasterTable", "renameVectorTable", "renameRasterTable", "createVectorTable",
    "truncate", "vacuum", "createSchema", "dropSchema", "renameSchema", "createSpatialIndex",
    "deleteSpatialIndex",



    "GetDriver", "GetDriverByIndex", "CopyDataSource",
    "createAlgorithmDialog",


    "entryList", "entryInfoList", "findFile",


    "Formatter",
})









_SQL_METHODS = frozenset({"executeSql", "execSql", "ExecuteSQL"})






_FORMAT_METHODS = frozenset({"format", "format_map"})






IN_PROCESS_ALGORITHMS = frozenset({
    "native:addfieldtoattributestable", "native:addxyfields", "native:aggregate", "native:aspect",
    "native:assignprojection", "native:buffer", "native:centroids", "native:checkvalidity",
    "native:clip", "native:cliprasterbyextent", "native:clipvectorbyextent", "native:collect",
    "native:countpointsinpolygon", "native:creategrid", "native:deleteduplicategeometries",
    "native:difference", "native:dissolve", "native:explodelines", "native:extenttolayer",
    "native:extractbyattribute", "native:extractbyexpression", "native:extractbyextent",
    "native:extractbylocation", "native:extractspecificvertices", "native:extractvertices",
    "native:extractwithindistance", "native:fieldcalculator", "native:fillnodata", "native:fixgeometries",
    "native:geometrybyexpression", "native:hillshade", "native:hublines", "native:intersection",
    "native:joinattributesbylocation", "native:joinattributestable", "native:joinbylocationsummary",
    "native:joinbynearest", "native:kmeansclustering", "native:linedensity", "native:mergevectorlayers",
    "native:minimumenclosingcircle", "native:multiparttosingleparts", "native:multiringconstantbuffer",
    "native:pixelstopolygons", "native:pointonsurface", "native:pointstopath", "native:polygonize",
    "native:polygonstolines", "native:rastercalc", "native:rasterize", "native:rastersampling",
    "native:rastersurfacevolume", "native:reclassifybytable", "native:refactorfields",
    "native:renametablefield", "native:reprojectlayer", "native:retainfields", "native:rotatefeatures",
    "native:selectwithindistance", "native:shortestpathpointtopoint", "native:serviceareafrompoint",
    "native:serviceareafromlayer", "native:simplifygeometries", "native:slope", "native:smoothgeometry",
    "native:snapgeometries", "native:splitwithlines", "native:union", "native:voronoipolygons",
    "native:zonalhistogram", "native:zonalstatistics", "native:zonalstatisticsfb",
    "qgis:distancetonearesthublinetohub", "qgis:distancetonearesthubpoints",
    "qgis:heatmapkerneldensityestimation", "qgis:idwinterpolation", "qgis:statisticsbycategories",
    "qgis:tininterpolation",
})


def in_process_algorithms() -> frozenset:

    from . import tuning

    doc = tuning.service_doc("code_classes") or {}
    return IN_PROCESS_ALGORITHMS - frozenset(doc.get("processing_refused") or ())


_PROCESSING_RUNNERS = ("run", "runAndLoadResults", "execAlgorithmDialog")

_MEMORY_OUTPUTS = ("TEMPORARY_OUTPUT", "memory:")

_DENIED_LITERALS = frozenset({
    "authManager", "environ", "getenv", "system", "popen", "modules", "_getframe", "b64decode", "fromhex",
    "subprocess", "ctypes", "socket", "importlib", "eval", "exec", "compile", "__import__", "rmtree", "unlink",
    "sys", "builtins", "CDLL", "WinDLL", "load_extension", "enable_load_extension", "sqlite3", "ctypeslib",
    "utils", "processingRegistry", "executeSql", "execSql", "ExecuteSQL",
})
DENIED_DUNDERS = frozenset({
    "__import__", "__builtins__", "__subclasses__", "__globals__", "__code__", "__closure__", "__loader__",
    "__spec__", "__dict__", "__bases__", "__mro__", "__class__", "__reduce__", "__reduce_ex__",
    "__getattribute__", "__func__", "__self__", "__wrapped__", "__traceback__", "__getattr__", "__setattr__",
    "__delattr__", "__build_class__", "__init_subclass__", "__base__",
})
_EMBEDDED_DUNDER_RE = re.compile(r"__[A-Za-z_]+__")
_DENIED_LITERAL_RE = re.compile(
    r"(?i)TerraLab/AIAgent|activation_key|authcfg_id|qgis-auth\.db|proxy/proxyPassword|\.ssh(?:[/\\]|$)|Keychains"
    r"|\.aws(?:[/\\]|$)|\.netrc|\.pgpass|id_rsa|id_ed25519|/etc/(?:passwd|shadow)|\.git-credentials"




    r"|169\.254\.\d"
)

_OS_ALLOWED = (
    "path", "sep", "altsep", "linesep", "pathsep", "name", "curdir", "pardir", "extsep", "devnull", "getcwd",
    "fspath", "fsencode", "fsdecode", "getpid", "cpu_count",
    "urandom", "PathLike", "strerror", "SEEK_SET", "SEEK_CUR", "SEEK_END", "DirEntry", "stat_result", "error",
)
_SHUTIL_ALLOWED = ("disk_usage", "which", "copyfileobj", "get_terminal_size")

_NO_FILE_CHANGE = "a snippet never removes, renames or overwrites a file, and writes a new file name instead."



_SYS_ALLOWED = (
    "version", "version_info", "platform", "maxsize", "byteorder", "getdefaultencoding",
    "getfilesystemencoding", "float_info", "int_info", "implementation", "api_version", "hexversion",
)



_IO_ALLOWED = ("StringIO", "BytesIO", "TextIOBase", "IOBase", "RawIOBase", "BufferedIOBase", "DEFAULT_BUFFER_SIZE")


class CodeTimeout(BaseException):
    pass







WAITING_ON_USER = threading.Event()


TASK_WAITS: list = []




CLOCK_OUT: list = []
CLOCK_LOCK = threading.Lock()


def clock_ran_out() -> bool:

    return bool(CLOCK_OUT) and CLOCK_OUT[-1].is_set()




def _processing_refusal(algorithm: object) -> str | None:

    if not isinstance(algorithm, str):
        return "execute_code runs Processing algorithms by id only."
    if algorithm.strip().lower() in in_process_algorithms():
        return None
    return (f"{algorithm} is not one of the algorithms a snippet runs inside QGIS (layers in, layers out); "
            "call run_processing for it.")


def _no_network(value: str) -> str | None:
    from .security import unwrap_vsi

    text = value.strip()
    if "://" in text.split("|", 1)[0] or (text.lower().startswith("/vsi") and unwrap_vsi(text)[0] == "remote"):
        return f"{text[:80]} is an address; a snippet reads layers and local files only."
    return None


_DATABASE_URI = "a snippet writes to memory or a file, never a database URI."


def _database_uri(value: str) -> bool:

    head = value.split("|", 1)[0]
    return ":" in head[2:] and not os.path.isabs(head)


def _parameters_refusal(algorithm: str, parameters: object) -> str | None:

    from .security import validate_path

    if not isinstance(parameters, dict):
        return None
    try:
        from qgis.core import QgsApplication

        definition = QgsApplication.processingRegistry().algorithmById(algorithm)
        outputs = {d.name() for d in definition.destinationParameterDefinitions()} if definition else set()
    except Exception:
        outputs = set()
    for key, value in parameters.items():
        sink = getattr(value, "sink", None)
        if sink is not None:
            try:
                value = sink.staticValue()
            except Exception:
                return f"{key}: execute_code cannot read this output definition; name a path or TEMPORARY_OUTPUT."
        if str(key) not in outputs:

            for text in _strings(value):
                problem = _no_network(text) or _input_scope(text)
                if problem:
                    return coded_like(f"{key}: {problem}", problem)
            continue
        if not isinstance(value, str):
            continue
        problem = _no_network(value)
        if problem:
            return coded_like(f"{key}: {problem}", problem)
        if not value.strip() or value.strip().startswith(_MEMORY_OUTPUTS):
            continue
        if _database_uri(value):
            return f"{key}: {_DATABASE_URI}"
        error = validate_path(value.split("|", 1)[0], write=True, overwrite=False)
        if error:
            return coded_like(f"{key}: {error}", error)
    return None


def _input_scope(value: str) -> str | None:

    from .security import local_part, validate_read

    head = value.split("|", 1)[0].strip()
    head = local_part(head) if head.lower().startswith(("file:", "/vsi")) else head
    if not head or not (os.path.isabs(os.path.expanduser(head)) or head.startswith("~")):
        return None
    return validate_read(head)


def _module_denied(name: str) -> bool:
    parts = name.split(".")
    if parts[0].lower().startswith(_DENIED_MODULE_PREFIXES):
        return True
    if parts[0] == "processing" and len(parts) > 1:


        return not hasattr(safe_processing_module(), parts[1])
    if parts[0] in GUARDED_MODULES and len(parts) > 1:


        return not hasattr(GUARDED_MODULES[parts[0]](), parts[1])
    return any(".".join(parts[:i]) in DENIED_MODULES for i in range(1, len(parts) + 1))


class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.names: list[str] = []
        self.checked_sql: set[int] = set()
        self.plain_getters: set[int] = set()


        self.dict_keys: set[int] = set()

    def _refuse(self, node: ast.AST, text: str, name: str = "") -> None:
        line = getattr(node, "lineno", 0)
        self.problems.append(f"line {line}: {text}")
        if name:
            self.names.append(name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _module_denied(alias.name):
                self._refuse(node, f"import of {alias.name} is not available in execute_code", alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            self._refuse(node, "relative imports are not available in execute_code")
        if _module_denied(module):
            self._refuse(node, f"import of {module} is not available in execute_code", module)
        for alias in node.names:
            if _module_denied(f"{module}.{alias.name}") or alias.name in DENIED_ATTRS or alias.name in DENIED_NAMES:
                self._refuse(node, f"{module}.{alias.name} is not available in execute_code", alias.name)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        self.dict_keys.update(id(key) for key in node.keys if isinstance(key, ast.Constant))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in DENIED_NAMES or node.id in DENIED_DUNDERS:
            self._refuse(node, f"{node.id} is not available in execute_code", node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr in DENIED_ATTRS or attr in DENIED_NAMES or attr in DENIED_DUNDERS:
            self._refuse(node, f".{attr} is not available in execute_code", attr)
        elif attr in _SQL_METHODS and id(node) not in self.checked_sql:
            self._refuse(node, f".{attr} runs only a plain query written out in the code, as its first argument")
        elif (attr in _FORMAT_METHODS and id(node) not in self.plain_getters
              and not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str))):
            self._refuse(node, f".{attr} runs only on a string written out in the code; an f-string or % "
                               "formats any value")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _FORMAT_METHODS and not node.args and not node.keywords:
            self.plain_getters.add(id(func))
        if isinstance(func, ast.Attribute) and func.attr in _SQL_METHODS and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                from ..tools.guards import sql_problem

                problem = sql_problem(first.value)
                if problem:
                    self._refuse(node, problem.replace("execute_sql", f".{func.attr}"))
                self.checked_sql.add(id(func))


        name = (func.attr if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id == "processing" else func.id if isinstance(func, ast.Name) else "")
        if name in _PROCESSING_RUNNERS and node.args:
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and ":" in first.value:
                refused = _processing_refusal(first.value)
                if refused:
                    self._refuse(node, refused)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            match = _DENIED_LITERAL_RE.search(node.value)
            if match:
                self._refuse(node, f"the string '{match.group(0)}' names a credential store or a guarded handler")
            elif node.value in DENIED_DUNDERS or (node.value in _DENIED_LITERALS
                                                  and id(node) not in self.dict_keys):
                self._refuse(node, f"the string literal '{node.value}' is the name of a guarded attribute")
            else:






                for embedded in _EMBEDDED_DUNDER_RE.finditer(node.value):
                    if embedded.group(0) in DENIED_DUNDERS:
                        self._refuse(node, f"the string contains '{embedded.group(0)}', a guarded attribute")
                        break
        self.generic_visit(node)


def validate_code(code: str) -> str | None:

    return _check(code)[0]


@functools.lru_cache(maxsize=4)
def _check(code: str) -> tuple[str | None, str]:








    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (line {exc.lineno})", ""
    checker = _Checker()
    checker.visit(tree)
    if not checker.problems:
        return None, ""
    return "Refused before running: " + "; ".join(checker.problems[:6]), (checker.names or [""])[0]







_NO_ROW = "execute_code refuses these names on this computer."


def refusal_for(code: str) -> dict | None:


















    refused, name = _check(code)
    if not refused:
        return None
    if refused.startswith("SyntaxError"):


        return dict(error=refused, code="INVALID_ARGS", suggestion="",  # noqa: C408
                    hint="snippet_syntax_error")

    return dict(error=refused, code="INVALID_ARGS", suggestion=_NO_ROW,  # noqa: C408
                hint="code_refused", variant=name)




def _trimmed_module(name: str, source: types.ModuleType, allowed: tuple, extra: dict | None = None,
                    missing: str = "") -> types.ModuleType:
    module = types.ModuleType(name, f"{name} as exposed to execute_code: a safe subset")
    for attr in allowed:
        if hasattr(source, attr):
            setattr(module, attr, getattr(source, attr))
    for key, value in (extra or {}).items():
        setattr(module, key, value)
    if missing:


        def _absent(attr):
            raise AttributeError(f"execute_code's {name} has no {attr}: {missing}")
        module.__getattr__ = _absent
    return module




INVALID_GEOMETRY: dict = {}


def _checked_runner(runner: Callable, runner_name: str = ""):
    def _run(algorithm, *args, **kwargs):
        refused = _processing_refusal(algorithm)
        if not refused:
            parameters = args[0] if args else kwargs.get("parameters")
            refused = _parameters_refusal(str(algorithm).strip().lower(), parameters)
        if refused:
            raise PermissionError(refused)
        from . import code_tripwire


        code_tripwire.judge_outputs(args[0] if args else kwargs.get("parameters"))
        INVALID_GEOMETRY.clear()
        try:
            return runner(algorithm, *args, **kwargs)
        except Exception as exc:
            from qgis.core import QgsProcessingException

            if isinstance(exc, QgsProcessingException) and runner_name == "run":
                from . import invalid_geometry

                context = kwargs.get("context") if "context" in kwargs else (args[3] if len(args) > 3 else None)
                INVALID_GEOMETRY.update(invalid_geometry.facts(args[0] if args else kwargs.get("parameters"), context))
            raise
    return _run


def safe_processing_module() -> types.ModuleType | None:




    try:
        import processing as source
    except ImportError:
        return None
    from .code_processing import threaded

    extra = {name: _checked_runner(threaded(source.run) if name == "run" else getattr(source, name), name)
             for name in _PROCESSING_RUNNERS if hasattr(source, name)}
    return _trimmed_module("processing", source, ("algorithmHelp",), extra)


def _guarded_makedirs(path, mode=0o777, exist_ok=False):
    from .security import anchor, validate_path

    path = anchor(path)
    error = validate_path(str(path), write=True)
    if error:
        raise PermissionError(error)
    return os.makedirs(path, mode, exist_ok=exist_ok)


def _guarded_copy(copier: Callable):
    def _copy(src, dst, *args, **kwargs):
        from .security import anchor, validate_path

        src, dst = anchor(src), anchor(dst)
        for candidate, writing in ((src, False), (dst, True)):

            remedy = "This call has no overwrite option; a new destination name avoids it." if writing else None
            error = validate_path(str(candidate), write=writing, overwrite=False if writing else None,
                                   overwrite_remedy=remedy, scoped=not writing)
            if error:
                raise PermissionError(error)
        note_written(dst)
        return copier(src, dst, *args, **kwargs)

    _copy.__name__ = copier.__name__
    return _copy








WRITTEN_FILES: list[str] = []


MAX_WRITTEN_FILES = 20


def note_written(path) -> None:

    try:
        text = os.fspath(path)
    except (TypeError, ValueError):
        return
    if (isinstance(text, str) and text and text not in WRITTEN_FILES
            and len(WRITTEN_FILES) < MAX_WRITTEN_FILES):
        WRITTEN_FILES.append(text)

    from .security import note_own_paths

    note_own_paths([text])


def take_written() -> list[str]:






    paths, WRITTEN_FILES[:] = list(WRITTEN_FILES), []
    out: list[str] = []
    for path in paths:
        try:
            if os.path.isfile(path):
                out.append(os.path.abspath(path))
        except (OSError, ValueError):
            continue
    return out


def read_refusal(path, error: str) -> OSError:








    from .security import validate_path

    text = os.fspath(path)
    if validate_path(str(text)) is None and not os.path.lexists(text):
        missing = FileNotFoundError(error)
        missing.errno = errno.ENOENT
        return missing
    return PermissionError(error)


@contextlib.contextmanager
def workspace_cwd():










    from .security import workspace_dir

    try:
        previous = os.getcwd()
    except OSError:
        previous = ""
    moved = False
    try:
        os.chdir(workspace_dir())
        moved = True
    except OSError:
        pass
    try:
        yield
    finally:
        if moved and previous:
            with contextlib.suppress(OSError):
                os.chdir(previous)


def _guarded_open(file, mode="r", *args, **kwargs):
    from .security import anchor, validate_path

    if isinstance(file, int):
        raise PermissionError("execute_code cannot open a file descriptor.")

    file = anchor(file)
    writing = any(flag in str(mode) for flag in "wax+")


    remedy = "This call has no overwrite option; a new file name avoids it." if writing else None

    error = validate_path(os.fspath(file), write=writing, overwrite=False if writing else None,
                           overwrite_remedy=remedy, scoped=not writing)
    if error:
        raise PermissionError(error) if writing else read_refusal(file, error)
    if writing:
        note_written(file)








    if "b" not in str(mode):
        new_csv = writing and os.path.splitext(os.fspath(file))[1].lower() == ".csv"
        if len(args) < 2:
            kwargs.setdefault("encoding", "utf-8-sig" if new_csv or not writing else "utf-8")
        if new_csv and len(args) < 4:
            kwargs.setdefault("newline", "")
    return py_builtins.open(file, mode, *args, **kwargs)


def _guarded_reader(reader: Callable, scoped: bool = True):





    def _read(path=".", *args, **kwargs):
        from .security import anchor, validate_path

        if isinstance(path, int):
            raise PermissionError("execute_code cannot use a file descriptor.")
        path = anchor(path)
        error = validate_path(os.fspath(path), scoped=scoped)
        if error:
            raise read_refusal(path, error)
        return reader(path, *args, **kwargs)

    _read.__name__ = reader.__name__
    return _read


def _guarded_walk(top=".", topdown=True, onerror=None, followlinks=False):







    from .security import anchor, validate_read

    top = anchor(top)
    error = validate_read(str(top))
    if error:
        refused = read_refusal(top, error)
        if not isinstance(refused, FileNotFoundError):
            raise refused

        if onerror is not None:
            onerror(refused)
        return iter(())

    def pruned():
        for root, dirs, files in os.walk(top, True, onerror, followlinks):
            dirs[:] = [d for d in dirs if validate_read(os.path.join(root, d)) is None]
            yield root, dirs, [f for f in files if validate_read(os.path.join(root, f)) is None]

    return pruned() if topdown else iter(list(pruned())[::-1])


def _safe_os() -> types.ModuleType:
    from .security import workspace_dir

    extra = {"makedirs": _guarded_makedirs, "mkdir": _guarded_makedirs, "walk": _guarded_walk,

             "getcwd": workspace_dir}
    for name in ("listdir", "scandir"):
        extra[name] = _guarded_reader(getattr(os, name))
    for name in ("stat", "lstat"):
        extra[name] = _guarded_reader(getattr(os, name), scoped=False)
    return _trimmed_module("os", os, _OS_ALLOWED, extra, _NO_FILE_CHANGE)


def _safe_shutil() -> types.ModuleType:
    extra = {name: _guarded_copy(getattr(shutil, name)) for name in ("copy", "copy2", "copyfile")}
    return _trimmed_module("shutil", shutil, _SHUTIL_ALLOWED, extra, _NO_FILE_CHANGE)


def _safe_sys() -> types.ModuleType:
    import sys as _sys

    return _trimmed_module("sys", _sys, _SYS_ALLOWED)


def _safe_io() -> types.ModuleType:
    return _trimmed_module("io", io, _IO_ALLOWED)

















_BASE64_ALLOWED = frozenset({"b64encode", "standard_b64encode", "urlsafe_b64encode", "b32encode",
                              "b16encode", "a85encode", "b85encode", "encodebytes"})


def _safe_base64() -> types.ModuleType:
    import base64 as _base64

    return _trimmed_module("base64", _base64, _BASE64_ALLOWED)



























_GDAL_ALLOWED = ("Info", "VersionInfo", "UseExceptions", "DontUseExceptions", "GetLastErrorMsg",
                 "GetDataTypeName", "GetDataTypeSize", "GA_ReadOnly", "OF_READONLY", "OF_RASTER", "OF_VECTOR",
                 "OF_VERBOSE_ERROR", "GetConfigOption", "GetColorInterpretationName")
_OGR_ALLOWED = ("CreateGeometryFromWkt", "CreateGeometryFromWkb", "CreateGeometryFromJson",
                "CreateGeometryFromGML", "Geometry", "UseExceptions", "DontUseExceptions", "GetFieldTypeName",
                "Feature", "FieldDefn", "GeomFieldDefn", "GeometryTypeToName")


_GDAL_CONSTANT_PREFIXES = ("GDT_", "GCI_", "wkb", "OFT", "OFST", "GRIORA_", "GMF_")



_GDAL_WRITERS = {"Translate": ("destName", "srcDS"), "Warp": ("destNameOrDestDS", "srcDSOrSrcDSTab"),
                 "VectorTranslate": ("destNameOrDestDS", "srcDS"), "BuildVRT": ("destName", "srcDSOrSrcDSTab")}
_GDAL_DRIVER_WRITERS = {"Create": ("utf8_path", ""), "CreateCopy": ("utf8_path", "src")}
_OGR_DRIVER_WRITERS = {"CreateDataSource": ("utf8_path", "")}


def _read_only_opener(opener: Callable, update_flag: int):
    def _open(path, *args, **kwargs):
        from .security import validate_path

        if not isinstance(path, (str, os.PathLike)):
            raise PermissionError("execute_code opens GDAL datasets by path only.")
        text = os.fspath(path)
        problem = _no_network(text) or validate_path(text, scoped=True)
        if problem:
            raise PermissionError(problem)
        mode = args[0] if args else kwargs.get("nOpenFlags", kwargs.get("update", 0))
        if isinstance(mode, int) and mode & update_flag:
            raise PermissionError("execute_code opens GDAL datasets read-only; write a new file with "
                                  "gdal.Translate or a driver's CreateCopy.")
        return opener(path, *args, **kwargs)

    _open.__name__ = opener.__name__
    return _open


def _strings(value, depth: int = 0):

    if isinstance(value, (str, os.PathLike)):
        text = os.fspath(value)
        yield text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    elif depth < 4 and isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _strings(item, depth + 1)
    elif depth < 4 and isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key, depth + 1)
            yield from _strings(item, depth + 1)


def _config_refusal(key, value) -> str | None:

    name = str(key or "").strip().upper()
    if name.startswith(("GDAL_HTTP_", "CPL_VSIL_")):



        return f"{key} is GDAL's network access; a snippet reads layers and local files only."


    if name.startswith("GDAL_PAM_"):
        return f"{key} is a run's GDAL safety option and stays as the guard set it."
    text = "" if value is None else str(value)
    if any(mark in text for mark in ("/", "\\", ":")):


        return (f"{key}={text[:60]} points GDAL at a path or an address; a snippet sets GDAL options "
                "to a flag or a number.")
    return None


def _options_refusal(values) -> str | None:

    words = [word.strip("'\"") for text in _strings(values) for word in text.split()]
    for index, word in enumerate(words):
        problem = _no_network(word) if word else None
        if not problem and word == "--config" and index + 1 < len(words):
            key, equals, value = words[index + 1].partition("=")
            if not equals:
                value = words[index + 2] if index + 2 < len(words) else ""
            problem = _config_refusal(key, value)
        if problem:
            return problem
    return None


def _gdal_destination(dest) -> tuple[object, str]:

    from osgeo import gdal, ogr

    from .security import anchor, validate_path

    if isinstance(dest, (gdal.Dataset, getattr(ogr, "DataSource", gdal.Dataset))):


        return dest, ""
    if not isinstance(dest, (str, os.PathLike)):
        raise PermissionError("execute_code writes a GDAL dataset to a file path, or into one it made.")
    text = next(_strings(dest))
    if not text.strip():

        return dest, ""
    problem = _no_network(text) or (_DATABASE_URI if _database_uri(text) else None)
    if problem:
        raise PermissionError(problem)

    target = anchor(text)
    error = validate_path(target, write=True)
    if error:
        raise PermissionError(error)
    return target, target


def _checked_writer(writer: Callable, names: tuple[str, str]) -> Callable:

    dest_name, source_name = names

    def _write(*args, **kwargs):
        from . import code_tripwire
        from .security import validate_path

        args = list(args)
        dest_key = dest_name if dest_name in kwargs else None
        if dest_key is None and not args:

            raise PermissionError(f"execute_code passes {getattr(writer, '__name__', 'a GDAL writer')} its "
                                  f"destination first, or as {dest_name}=.")
        rest = args if dest_key else args[1:]
        source_key = source_name if source_name and source_name in kwargs else None
        positional_source = bool(source_name and not source_key and rest)
        source = kwargs[source_key] if source_key else (rest[0] if positional_source else None)
        options = rest[1:] if positional_source else rest
        options = options + [value for key, value in kwargs.items() if key not in (dest_key, source_key)]
        problem = _options_refusal(options) or next(
            (found for found in (_no_network(text) or validate_path(text, scoped=True) for text in _strings(source))
             if found),
            None)
        if problem:
            raise PermissionError(problem)
        target, path = _gdal_destination(kwargs[dest_key] if dest_key else args[0])
        if path:



            code_tripwire.judge_write(path)
            note_written(path)
        if dest_key:
            kwargs[dest_key] = target
        else:
            args[0] = target
        return writer(*args, **kwargs)

    _write.__name__ = getattr(writer, "__name__", "write")
    return _write


def _checked_options(builder: Callable) -> Callable:

    def _build(*args, **kwargs):
        problem = _options_refusal([list(args), list(kwargs.values())])
        if problem:
            raise PermissionError(problem)
        return builder(*args, **kwargs)

    _build.__name__ = getattr(builder, "__name__", "options")
    return _build


def _driver_lookup(lookup: Callable, writers: dict) -> Callable:

    def GetDriverByName(name):  # noqa: N802
        driver = lookup(name)
        if driver is None:
            return None
        members = {attr: getattr(driver, attr) for attr in ("ShortName", "LongName", "name", "GetName",
                                                           "GetDescription", "GetMetadata", "GetMetadataItem")
                   if hasattr(driver, attr)}
        members.update({attr: _checked_writer(getattr(driver, attr), names) for attr, names in writers.items()
                        if hasattr(driver, attr)})
        return types.SimpleNamespace(**members)

    return GetDriverByName






_CONFIG_BEFORE: dict = {}








_NO_NETWORK_FILENAME = "terralab://execute-code-allows-no-network"





_HARDENED_CONFIG = (("CPL_VSIL_CURL_ALLOWED_FILENAME", _NO_NETWORK_FILENAME),
                    ("GDAL_PAM_ENABLED", "NO"))


def harden_gdal_config() -> None:






    try:
        from osgeo import gdal
    except Exception:  # noqa: BLE001
        return
    for key, value in _HARDENED_CONFIG:
        try:
            if key not in _CONFIG_BEFORE:
                _CONFIG_BEFORE[key] = gdal.GetConfigOption(key)
            gdal.SetConfigOption(key, value)
        except Exception as exc:  # noqa: BLE001
            from .logger import log_warning

            log_warning(f"code guard: a GDAL safety option was not set: {exc}")


def _set_config_option(key, value, *args, **kwargs):
    from osgeo import gdal

    problem = _config_refusal(key, value)
    if problem:
        raise PermissionError(problem)
    if str(key) not in _CONFIG_BEFORE:
        _CONFIG_BEFORE[str(key)] = gdal.GetConfigOption(str(key))
    return gdal.SetConfigOption(key, value, *args, **kwargs)


def restore_gdal_config() -> None:

    saved = dict(_CONFIG_BEFORE)
    _CONFIG_BEFORE.clear()
    if not saved:
        return
    try:
        from osgeo import gdal

        for key, value in saved.items():
            gdal.SetConfigOption(key, value)
    except Exception as exc:  # noqa: BLE001
        from .logger import log_warning

        log_warning(f"code guard: a GDAL option was not put back: {exc}")


def _safe_osgeo_module(sub: str) -> types.ModuleType:

    from osgeo import gdal, ogr, osr

    def gdal_extra() -> dict:
        extra = {"Open": _read_only_opener(gdal.Open, 1),
                 "OpenEx": _read_only_opener(gdal.OpenEx, gdal.OF_UPDATE),
                 "GetDriverByName": _driver_lookup(gdal.GetDriverByName, _GDAL_DRIVER_WRITERS),
                 "SetConfigOption": _set_config_option}
        for name, names in _GDAL_WRITERS.items():
            if hasattr(gdal, name):
                extra[name] = _checked_writer(getattr(gdal, name), names)
            if hasattr(gdal, name + "Options"):
                extra[name + "Options"] = _checked_options(getattr(gdal, name + "Options"))
        return extra

    modules = {
        "gdal": (gdal, _GDAL_ALLOWED, gdal_extra),
        "ogr": (ogr, _OGR_ALLOWED, lambda: {"Open": _read_only_opener(ogr.Open, 1),
                                            "GetDriverByName": _driver_lookup(ogr.GetDriverByName,
                                                                              _OGR_DRIVER_WRITERS)}),
    }
    if sub not in modules:
        return osr
    source, allowed, extra = modules[sub]
    constants = tuple(a for a in dir(source) if a.startswith(_GDAL_CONSTANT_PREFIXES))

    return _trimmed_module(f"osgeo.{sub}", source, allowed + constants, extra())


def _safe_osgeo(name: str, fromlist) -> types.ModuleType:
    parts = name.split(".")
    if len(parts) > 2 or (len(parts) == 2 and parts[1] not in ("gdal", "ogr", "osr")):
        raise ImportError(f"{name} is not available in execute_code")
    if len(parts) == 2 and fromlist:
        return _safe_osgeo_module(parts[1])
    package = types.ModuleType("osgeo", "osgeo as exposed to execute_code: gdal and ogr checked, osr")
    for sub in ("gdal", "ogr", "osr"):
        setattr(package, sub, _safe_osgeo_module(sub))
    return package

























_ATTR_GUARD_REASON = "{name} is not reachable from execute_code."


def _attr_allowed(name: object) -> str | None:

    if not isinstance(name, str):
        return _ATTR_GUARD_REASON.format(name="a non-string attribute name")
    if (name in DENIED_ATTRS or name in DENIED_NAMES or name in DENIED_DUNDERS or name in _SQL_METHODS
            or name in _FORMAT_METHODS):
        return _ATTR_GUARD_REASON.format(name=name)




    if len(name) > 4 and name.startswith("__") and name.endswith("__"):
        return f"execute_code does not look up {name} by name."
    if _DENIED_LITERAL_RE.search(name):
        return f"{name} names a credential store."
    return None


def _guarded_getattr(obj, name, *default):
    error = _attr_allowed(name)
    if error:
        raise PermissionError(error)
    return py_builtins.getattr(obj, name, *default)


def _guarded_setattr(obj, name, value):
    error = _attr_allowed(name)
    if error:
        raise PermissionError(error)
    return py_builtins.setattr(obj, name, value)


def _guarded_delattr(obj, name):
    error = _attr_allowed(name)
    if error:
        raise PermissionError(error)
    return py_builtins.delattr(obj, name)






_QT_BINDINGS = ("PyQt5", "PyQt6")


def _host_qt(name, fromlist):
    host = "qgis.PyQt" + name[name.index("."):] if "." in name else "qgis.PyQt"
    module = py_builtins.__import__(host, None, None, ["__name__"], 0)
    return module if fromlist else py_builtins.__import__("qgis.PyQt", None, None, ["__name__"], 0)


def _safe_processing() -> types.ModuleType:
    module = safe_processing_module()
    if module is None:
        raise ImportError("processing is not available in this QGIS")
    return module





_TRIMMED_MODULES = {
    "shutil": _safe_shutil,
    "sys": _safe_sys,
    "io": _safe_io,
    "base64": _safe_base64,
    "processing": _safe_processing,
}



_FROM_OS = {
    "os": lambda: _safe_os(),
    "os.path": lambda: os.path,
}


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002
    if level:
        raise ImportError("relative imports are not available in execute_code")
    if _module_denied(name):
        raise ImportError(f"{name} is not available in execute_code")
    top = name.split(".")[0]
    if top == "os":
        if not fromlist:
            return _safe_os()
        from_os = _FROM_OS.get(name)
        if from_os is not None:
            return from_os()
    trimmed = _TRIMMED_MODULES.get(name)
    if trimmed is not None:
        return trimmed()
    if name in GUARDED_MODULES:
        return GUARDED_MODULES[name]()
    if top == "osgeo":
        return _safe_osgeo(name, fromlist)
    try:
        module = py_builtins.__import__(name, globals, locals, fromlist, level)
    except ModuleNotFoundError:
        if top not in _QT_BINDINGS:
            raise
        module = _host_qt(name, fromlist)
    for attr in fromlist or ():
        if attr in DENIED_ATTRS or attr in DENIED_NAMES or _module_denied(f"{name}.{attr}"):
            raise ImportError(f"{name}.{attr} is not available in execute_code")
    return module


def build_safe_builtins() -> dict[str, Any]:

    safe = {k: v for k, v in py_builtins.__dict__.items() if k not in DENIED_NAMES and k not in DENIED_DUNDERS}
    safe["__import__"] = _guarded_import
    safe["open"] = _guarded_open
    safe["getattr"] = _guarded_getattr
    safe["setattr"] = _guarded_setattr
    safe["delattr"] = _guarded_delattr
    return safe




class CappedOutput(io.TextIOBase):


    def __init__(self, cap: int = MAX_OUTPUT_CHARS):
        self._cap = cap
        self._parts: list[str] = []
        self._kept = 0
        self.dropped = 0

    def write(self, text) -> int:
        text = str(text)
        room = self._cap - self._kept
        if room > 0:
            self._parts.append(text[:room])
            self._kept += min(len(text), room)
        self.dropped += max(0, len(text) - room)
        return len(text)

    def getvalue(self) -> str:
        out = "".join(self._parts)
        if self.dropped:
            out += f"\n\n[TRUNCATED: {self.dropped:,} more characters were printed, showing the first {self._cap:,}]"
        return out


def run_with_timeout(fn: Callable[[], Any], seconds: float) -> Any:





    thread_id = threading.get_ident()
    done = threading.Event()
    expired = threading.Event()

    def _watch() -> None:
        remaining = seconds
        while remaining > 0:
            started = time.monotonic()
            if done.wait(min(remaining, 0.5)):
                return
            if not WAITING_ON_USER.is_set():
                remaining -= time.monotonic() - started
        expired.set()



        while True:
            with CLOCK_LOCK:
                if done.is_set():
                    return
                if not TASK_WAITS:


                    armed = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id),
                                                                       ctypes.py_object(CodeTimeout))
                    if armed > 1:
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)
                    return
                TASK_WAITS[-1]()
            if done.wait(0.5):
                return

    with CLOCK_LOCK:
        CLOCK_OUT.append(expired)
    watchdog = threading.Thread(target=_watch, name="execute_code-watchdog", daemon=True)
    watchdog.start()
    try:
        return fn()
    finally:
        with CLOCK_LOCK:
            done.set()
            if expired in CLOCK_OUT:
                CLOCK_OUT.remove(expired)

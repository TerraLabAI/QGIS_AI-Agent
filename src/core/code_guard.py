# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Guard rails for model-written Python run by ``execute_code``."""































from __future__ import annotations

import ast
import builtins as py_builtins
import ctypes
import io
import os
import re
import shutil
import threading
import types
from typing import Any, Callable

DEFAULT_TIMEOUT_S = 120
MAX_OUTPUT_CHARS = 200_000

DENIED_MODULES = frozenset({
    "subprocess", "ctypes", "socket", "multiprocessing", "importlib", "signal", "pty", "tty", "marshal",
    "_thread", "winreg", "keyring", "code", "pdb", "runpy", "smtplib", "ftplib", "telnetlib", "socketserver",
    "http.server", "xmlrpc", "ssl", "asyncio", "selectors", "select", "resource", "gc", "inspect", "dis",
    "webbrowser", "pickle", "shelve", "base64", "codecs", "zipimport", "pkgutil", "imp", "_io", "_socket",
    "_ctypes", "_posixsubprocess", "posix", "nt", "msvcrt", "fcntl", "mmap", "faulthandler", "sysconfig",
    "site", "setuptools", "pip", "ensurepip", "venv", "distutils", "requests", "urllib3", "httpx", "aiohttp",
    "http.client", "urllib.request", "pathlib", "glob", "tempfile", "fileinput", "linecache",


    "sqlite3", "_sqlite3", "dbm", "numpy.ctypeslib", "cffi", "_cffi_backend",


    "zipfile", "tarfile", "gzip", "bz2", "lzma", "zlib", "configparser", "plistlib",


    "threading", "concurrent", "concurrent.futures", "sched", "queue",








    "builtins", "operator", "types", "weakref", "copyreg", "_frozen_importlib", "_frozen_importlib_external",
})
DENIED_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "breakpoint", "input", "exit", "quit", "help", "vars", "memoryview",



    "globals", "locals",
    "QgsSettings", "QSettings", "QgsAuthMethodConfig", "QgsAuthManager", "QgsAuthConfigSslServer",
    "QgsNetworkAccessManager", "QNetworkAccessManager", "QNetworkRequest", "QProcess", "QFile", "QDir",
    "QSaveFile", "QTemporaryFile",
})
DENIED_ATTRS = frozenset({
    "authManager", "masterPasswordIsSet", "setMasterPassword", "authMethodConfig", "storeAuthenticationConfig",
    "loadAuthenticationConfig", "environ", "getenv", "putenv", "unsetenv", "system", "popen", "execv", "execve",
    "execl", "execlp", "execvp", "execvpe", "spawnl", "spawnv", "spawnve", "spawnlp", "spawnvp", "fork",
    "forkpty", "modules", "_getframe", "settrace", "setprofile", "b64decode", "fromhex", "decodebytes",
    "a85decode", "b32decode", "b16decode", "unlink", "rmdir", "removedirs", "rmtree", "fdopen", "open",
    "write_text", "write_bytes", "setValue", "activation_key", "clear_activation_key", "set_activation_key",
    "load_module", "import_module", "startDetached", "excepthook", "displayhook", "gi_frame", "f_globals",
    "f_locals", "f_back", "tb_frame", "cr_frame", "ag_frame", "os", "genericpath",


    "sys", "builtins", "subprocess", "socket", "ctypes", "ctypeslib", "importlib", "CDLL", "WinDLL", "PyDLL",
    "OleDLL", "cdll", "windll", "pydll", "LibraryLoader", "load_extension", "enable_load_extension",
})

_DENIED_LITERALS = frozenset({
    "authManager", "environ", "getenv", "system", "popen", "modules", "_getframe", "b64decode", "fromhex",
    "subprocess", "ctypes", "socket", "importlib", "eval", "exec", "compile", "__import__", "rmtree", "unlink",
    "sys", "builtins", "CDLL", "WinDLL", "load_extension", "enable_load_extension", "sqlite3", "ctypeslib",
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


    r"|/vsicurl|/vsis3|/vsigs|/vsiaz|169\.254\.\d"
)

_OS_ALLOWED = (
    "path", "sep", "altsep", "linesep", "pathsep", "name", "curdir", "pardir", "extsep", "devnull", "getcwd",
    "fspath", "fsencode", "fsdecode", "getpid", "cpu_count",
    "urandom", "PathLike", "strerror", "SEEK_SET", "SEEK_CUR", "SEEK_END", "DirEntry", "stat_result", "error",
)
_SHUTIL_ALLOWED = ("disk_usage", "which", "copyfileobj", "get_terminal_size")



_SYS_ALLOWED = (
    "version", "version_info", "platform", "maxsize", "byteorder", "getdefaultencoding",
    "getfilesystemencoding", "float_info", "int_info", "implementation", "api_version", "hexversion",
)



_IO_ALLOWED = ("StringIO", "BytesIO", "TextIOBase", "IOBase", "RawIOBase", "BufferedIOBase", "DEFAULT_BUFFER_SIZE")


class CodeTimeout(BaseException):
    """Raised inside the running code once the budget is spent. BaseException so a bare except does not eat it."""


class CodeRefused(ValueError):
    """The code was refused before running."""




def _module_denied(name: str) -> bool:
    parts = name.split(".")
    return any(".".join(parts[:i]) in DENIED_MODULES for i in range(1, len(parts) + 1))


class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.problems: list[str] = []

    def _refuse(self, node: ast.AST, text: str) -> None:
        line = getattr(node, "lineno", 0)
        self.problems.append(f"line {line}: {text}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _module_denied(alias.name):
                self._refuse(node, f"import of {alias.name} is not available in execute_code")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            self._refuse(node, "relative imports are not available in execute_code")
        if _module_denied(module):
            self._refuse(node, f"import of {module} is not available in execute_code")
        for alias in node.names:
            if _module_denied(f"{module}.{alias.name}") or alias.name in DENIED_ATTRS or alias.name in DENIED_NAMES:
                self._refuse(node, f"{module}.{alias.name} is not available in execute_code")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in DENIED_NAMES or node.id in DENIED_DUNDERS:
            self._refuse(node, f"{node.id} is not available in execute_code")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if attr in DENIED_ATTRS or attr in DENIED_NAMES or attr in DENIED_DUNDERS:
            self._refuse(node, f".{attr} is not available in execute_code")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            match = _DENIED_LITERAL_RE.search(node.value)
            if match:
                self._refuse(node, f"the string '{match.group(0)}' names a credential store or a guarded handler")
            elif node.value in _DENIED_LITERALS or node.value in DENIED_DUNDERS:
                self._refuse(node, f"the string '{node.value}' names a guarded attribute")
            else:






                for embedded in _EMBEDDED_DUNDER_RE.finditer(node.value):
                    if embedded.group(0) in DENIED_DUNDERS:
                        self._refuse(node, f"the string contains '{embedded.group(0)}', a guarded attribute")
                        break
        self.generic_visit(node)


def validate_code(code: str) -> str | None:
    """None when the code may run, else the reasons it may not (one line each)."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"
    checker = _Checker()
    checker.visit(tree)
    if not checker.problems:
        return None
    return "Refused before running: " + "; ".join(checker.problems[:6])




_NETWORK_REFUSAL_RE = re.compile(r"\b(urllib\.request|urllib3|requests|httpx|aiohttp|http\.client|socket|urlopen)\b")




_ARCHIVE_REFUSAL_RE = re.compile(r"\b(zipfile|tarfile|shutil\.make_archive)\b")


def refusal_for(code: str) -> dict | None:
    """The tool error for code the guard will not run, or None when it will."""








    refused = validate_code(code)
    if not refused:
        return None
    if _ARCHIVE_REFUSAL_RE.search(refused):
        hint = ("package_project writes the zip: it copies the project and every local layer file into "
                "one archive. Call it instead of building the archive here.")
    elif _NETWORK_REFUSAL_RE.search(refused):
        hint = ("The snippet cannot use the network. Read a page or a CSV with fetch_text, "
                "load a file at a URL with add_data (source=<url>), then continue in execute_code.")
    else:
        hint = ("Use the dedicated tools (get_features, run_processing, export_layer) or rewrite "
                "the snippet without the refused names.")
    return {"error": refused, "code": "PERMISSION_DENIED", "suggestion": hint}




def _trimmed_module(name: str, source: types.ModuleType, allowed: tuple, extra: dict | None = None) -> types.ModuleType:
    module = types.ModuleType(name, f"{name} as exposed to execute_code: a safe subset")
    for attr in allowed:
        if hasattr(source, attr):
            setattr(module, attr, getattr(source, attr))
    for key, value in (extra or {}).items():
        setattr(module, key, value)
    return module


def _guarded_makedirs(path, mode=0o777, exist_ok=False):
    from .security import validate_path

    error = validate_path(str(path), write=True)
    if error:
        raise PermissionError(error)
    return os.makedirs(path, mode, exist_ok=exist_ok)


def _guarded_copy(copier: Callable):
    def _copy(src, dst, *args, **kwargs):
        from .security import validate_path

        for candidate, writing in ((src, False), (dst, True)):
            error = validate_path(str(candidate), write=writing, overwrite=False if writing else None)
            if error:
                raise PermissionError(error)
        return copier(src, dst, *args, **kwargs)

    _copy.__name__ = copier.__name__
    return _copy


def _guarded_open(file, mode="r", *args, **kwargs):
    from .security import validate_path

    if isinstance(file, int):
        raise PermissionError("execute_code cannot open a file descriptor.")
    writing = any(flag in str(mode) for flag in "wax+")
    error = validate_path(os.fspath(file), write=writing, overwrite=False if writing else None)
    if error:
        raise PermissionError(error)



    if "b" not in str(mode) and len(args) < 2:
        kwargs.setdefault("encoding", "utf-8")
    return py_builtins.open(file, mode, *args, **kwargs)


def _guarded_reader(reader: Callable):
    """listdir, walk, stat and friends behind the same read check as open()."""
    def _read(path=".", *args, **kwargs):
        from .security import validate_path

        if isinstance(path, int):
            raise PermissionError("execute_code cannot use a file descriptor.")
        error = validate_path(os.fspath(path))
        if error:
            raise PermissionError(error)
        return reader(path, *args, **kwargs)

    _read.__name__ = reader.__name__
    return _read


def _safe_os() -> types.ModuleType:
    extra = {"makedirs": _guarded_makedirs, "mkdir": _guarded_makedirs}
    for name in ("listdir", "scandir", "walk", "stat", "lstat"):
        extra[name] = _guarded_reader(getattr(os, name))
    return _trimmed_module("os", os, _OS_ALLOWED, extra)


def _safe_shutil() -> types.ModuleType:
    extra = {name: _guarded_copy(getattr(shutil, name)) for name in ("copy", "copy2", "copyfile")}
    return _trimmed_module("shutil", shutil, _SHUTIL_ALLOWED, extra)


def _safe_sys() -> types.ModuleType:
    import sys as _sys

    return _trimmed_module("sys", _sys, _SYS_ALLOWED)


def _safe_io() -> types.ModuleType:
    return _trimmed_module("io", io, _IO_ALLOWED)

























_ATTR_GUARD_REASON = "{name} is not reachable from execute_code."


def _attr_allowed(name: object) -> str | None:
    """None when this attribute name may be looked up, else why it may not."""
    if not isinstance(name, str):
        return _ATTR_GUARD_REASON.format(name="a non-string attribute name")
    if name in DENIED_ATTRS or name in DENIED_NAMES or name in DENIED_DUNDERS:
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


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: A002 - builtins signature
    if level:
        raise ImportError("relative imports are not available in execute_code")
    if _module_denied(name):
        raise ImportError(f"{name} is not available in execute_code")
    top = name.split(".")[0]
    if name == "os" or (top == "os" and not fromlist and name != "os.path"):
        return _safe_os()
    if name == "os.path":
        return os.path if fromlist else _safe_os()
    if name == "shutil":
        return _safe_shutil()
    if name == "sys":
        return _safe_sys()
    if name == "io":
        return _safe_io()
    module = py_builtins.__import__(name, globals, locals, fromlist, level)
    for attr in fromlist or ():
        if attr in DENIED_ATTRS or attr in DENIED_NAMES or _module_denied(f"{name}.{attr}"):
            raise ImportError(f"{name}.{attr} is not available in execute_code")
    return module


def build_safe_builtins() -> dict[str, Any]:
    """A copy of builtins without the interpreter escapes, with guarded import and open."""
    safe = {k: v for k, v in py_builtins.__dict__.items() if k not in DENIED_NAMES and k not in DENIED_DUNDERS}
    safe["__import__"] = _guarded_import
    safe["open"] = _guarded_open
    safe["getattr"] = _guarded_getattr
    safe["setattr"] = _guarded_setattr
    safe["delattr"] = _guarded_delattr
    return safe




class CappedOutput(io.TextIOBase):
    """A text sink that keeps the first ``cap`` characters and counts the rest."""

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


def run_with_timeout(fn: Callable[[], Any], seconds: float = DEFAULT_TIMEOUT_S) -> Any:
    """Run ``fn`` on this thread; a watchdog raises ``CodeTimeout`` in it after ``seconds``."""




    thread_id = threading.get_ident()
    lock = threading.Lock()
    done = threading.Event()

    def _watch() -> None:
        if done.wait(seconds):
            return
        with lock:
            if done.is_set():
                return


            armed = ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), ctypes.py_object(CodeTimeout))
            if armed > 1:
                ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(thread_id), None)

    watchdog = threading.Thread(target=_watch, name="execute_code-watchdog", daemon=True)
    watchdog.start()
    try:
        return fn()
    finally:
        with lock:
            done.set()

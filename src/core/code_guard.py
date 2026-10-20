# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















































from __future__ import annotations

import ast
import ctypes
import functools
import io
import re
import threading
import time
from typing import Any, Callable

from .code_guard_runtime import (  # noqa: F401
    _DENIED_LITERAL_RE,
    _DRIVER_READS,
    _FORMAT_METHODS,
    _NO_FILE_CHANGE,
    _PROCESSING_RUNNERS,
    _SQL_METHODS,
    DENIED_ATTRS,
    DENIED_DUNDERS,
    DENIED_MODULES,
    DENIED_NAMES,
    IN_PROCESS_ALGORITHMS,
    INVALID_GEOMETRY,
    MAX_WRITTEN_FILES,
    WRITTEN_FILES,
    _guarded_import,
    _guarded_open,
    _module_denied,
    _processing_refusal,
    bind_namespace_views,
    build_safe_builtins,
    harden_gdal_config,
    in_process_algorithms,
    note_written,
    read_refusal,
    restore_gdal_config,
    safe_processing_module,
    take_written,
    workspace_cwd,
)
from .code_owners import HELPER as _OWNED_CALL  # noqa: E402

MAX_OUTPUT_CHARS = 200_000


_DENIED_LITERALS = frozenset({
    "authManager", "environ", "getenv", "system", "popen", "modules", "_getframe", "b64decode", "fromhex",
    "subprocess", "ctypes", "socket", "importlib", "eval", "exec", "compile", "__import__", "rmtree", "unlink",
    "sys", "builtins", "CDLL", "WinDLL", "load_extension", "enable_load_extension", "sqlite3", "ctypeslib",
    "utils", "processingRegistry", "executeSql", "execSql", "ExecuteSQL",
})
_EMBEDDED_DUNDER_RE = re.compile(r"__[A-Za-z_]+__")


class CodeTimeout(BaseException):
    pass







WAITING_ON_USER = threading.Event()


TASK_WAITS: list = []




CLOCK_OUT: list = []
CLOCK_LOCK = threading.Lock()


def clock_ran_out() -> bool:

    return bool(CLOCK_OUT) and CLOCK_OUT[-1].is_set()




class _Checker(ast.NodeVisitor):
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.names: list[str] = []
        self.checked_sql: set[int] = set()
        self.plain_getters: set[int] = set()
        self.driver_reads: set[int] = set()


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



        if node.id in DENIED_NAMES or node.id in DENIED_DUNDERS or node.id == _OWNED_CALL:
            self._refuse(node, f"{node.id} is not available in execute_code", node.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        inner = node.value
        if (attr in _DRIVER_READS and isinstance(inner, ast.Call) and not inner.args and not inner.keywords
                and isinstance(inner.func, ast.Attribute) and inner.func.attr == "GetDriver"):


            self.driver_reads.add(id(inner.func))
        if attr == "GetDriver" and id(node) in self.driver_reads:
            pass
        elif attr in DENIED_ATTRS or attr in DENIED_NAMES or attr in DENIED_DUNDERS:
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

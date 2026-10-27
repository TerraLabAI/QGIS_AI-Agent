# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


































from __future__ import annotations

import ast
import builtins

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject

HELPER = "__terralab_owned_call__"
OWNER_ATTR = "_terralab_owner"
_HELPER_FILE = "<snippet-owner>"


_PYTHON_NAMES = frozenset(dir(builtins))


def _keeper(obj):

    if not isinstance(obj, sip.simplewrapper):
        return None
    try:
        if sip.ispyowned(obj):
            return obj
        return obj.__dict__.get(OWNER_ATTR)
    except (AttributeError, TypeError, RuntimeError):
        return None


def tie(owner, value) -> None:

    keeper = _keeper(owner)
    if keeper is None:
        return
    items = value if isinstance(value, (list, tuple)) else (value,)
    for item in items:
        if not isinstance(item, sip.simplewrapper) or item is owner or item is keeper:
            continue
        try:
            if not sip.ispyowned(item):
                setattr(item, OWNER_ATTR, keeper)
        except (AttributeError, TypeError, RuntimeError):
            continue


def _native(func) -> bool:


    if isinstance(func, sip.wrappertype):
        return True
    return (type(func).__name__ in ("builtin_function_or_method", "method")
            and isinstance(getattr(func, "__self__", None), (sip.simplewrapper, sip.wrappertype)))


def handed(arg, keeper):




    if not isinstance(arg, sip.simplewrapper) or isinstance(arg, QObject):
        return arg
    try:
        lender = arg.__dict__.get(OWNER_ATTR)
        if lender is None or lender is keeper or sip.ispyowned(arg) or not hasattr(arg, "clone"):
            return arg
        return arg.clone()
    except Exception:  # noqa: BLE001
        return arg


_SOURCE = f"""
def {HELPER}(owner, name, /, *args, **kwargs):
    if name is None:
        if native(owner):
            args = tuple([handed(arg, None) for arg in args])
            kwargs = {{key: handed(arg, None) for key, arg in kwargs.items()}}
        return owner(*args, **kwargs)
    func = getattr(owner, name)
    if not (args or kwargs):
        value = func()
    else:
        if native(func):
            keeper = keeper_of(owner)
            args = tuple([handed(arg, keeper) for arg in args])
            kwargs = {{key: handed(arg, keeper) for key, arg in kwargs.items()}}
        value = func(*args, **kwargs)
    if isinstance(value, lendable):
        tie(owner, value)
    return value
"""
_scope: dict = {"tie": tie, "handed": handed, "native": _native, "keeper_of": _keeper, "getattr": getattr,
                "tuple": tuple, "isinstance": isinstance, "lendable": (sip.simplewrapper, list, tuple)}
exec(compile(_SOURCE, _HELPER_FILE, "exec"), _scope)  # nosec B102
owned_call = _scope[HELPER]


class _Calls(ast.NodeTransformer):
    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if isinstance(func, ast.Attribute):
            target = [func.value, ast.Constant(value=func.attr)]
        elif (node.args or node.keywords) and not (isinstance(func, ast.Name) and func.id in _PYTHON_NAMES):
            target = [func, ast.Constant(value=None)]
        else:
            return node
        routed = ast.Call(func=ast.Name(id=HELPER, ctx=ast.Load()), args=[*target, *node.args],
                          keywords=node.keywords)
        return ast.copy_location(routed, node)


def compile_snippet(code: str, filename: str):




    tree = _Calls().visit(ast.parse(code, filename, "exec"))
    return compile(ast.fix_missing_locations(tree), filename, "exec")

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





































from __future__ import annotations

import ast
import builtins

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QByteArray, QObject

from .code_guard import OWNED_CALL as HELPER

OWNER_ATTR = "_terralab_owner"
HANDED_ATTR = "_terralab_handed"
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




            if not sip.ispyowned(item) or hasattr(type(item), "__next__") or isinstance(item, QByteArray):
                setattr(item, OWNER_ATTR, keeper)
        except (AttributeError, TypeError, RuntimeError):
            continue


def _native(func) -> bool:


    if isinstance(func, sip.wrappertype):
        return True
    return (type(func).__name__ in ("builtin_function_or_method", "method")
            and isinstance(getattr(func, "__self__", None), (sip.simplewrapper, sip.wrappertype)))


def handed(arg, keeper, clones):





    if not isinstance(arg, sip.simplewrapper) or isinstance(arg, QObject):
        return arg
    try:
        lender = arg.__dict__.get(OWNER_ATTR)
        if lender is None or lender is keeper or sip.ispyowned(arg) or not hasattr(arg, "clone"):
            return arg
        clone = arg.clone()
    except Exception:  # noqa: BLE001
        return arg
    clones.append(clone)
    return clone


def aliased(value, args):




    if not isinstance(value, sip.simplewrapper):
        return value
    try:
        if sip.ispyowned(value):
            return value
        address = sip.unwrapinstance(value)
    except (TypeError, RuntimeError):
        return value
    for arg in args:
        if arg is value or not isinstance(arg, sip.simplewrapper):
            continue
        try:
            if sip.unwrapinstance(arg) != address:
                continue
        except (TypeError, RuntimeError):
            continue
        keeper = _keeper(arg)
        if keeper is not None:
            try:
                setattr(value, OWNER_ATTR, keeper)
            except (AttributeError, TypeError, RuntimeError):
                pass
        break
    return value


def kept(value, clones):


    if clones and isinstance(value, sip.simplewrapper):
        try:
            setattr(value, HANDED_ATTR, clones)
        except (AttributeError, TypeError, RuntimeError):
            pass
    return value


_SOURCE = f"""
def {HELPER}(owner, name, /, *args, **kwargs):
    if name is None:
        if native(owner):
            clones = []
            args = tuple([handed(arg, None, clones) for arg in args])
            kwargs = {{key: handed(arg, None, clones) for key, arg in kwargs.items()}}
            return kept(owner(*args, **kwargs), clones)
        return aliased(owner(*args, **kwargs), args)
    func = getattr(owner, name)
    if not (args or kwargs):
        value = func()
    elif native(func):
        keeper = keeper_of(owner)
        clones = []
        args = tuple([handed(arg, keeper, clones) for arg in args])
        kwargs = {{key: handed(arg, keeper, clones) for key, arg in kwargs.items()}}
        value = kept(func(*args, **kwargs), clones)
    else:
        value = aliased(func(*args, **kwargs), args)
    if isinstance(value, lendable):
        tie(owner, value)
    return value
"""
_scope: dict = {"tie": tie, "handed": handed, "kept": kept, "aliased": aliased, "native": _native,
                "keeper_of": _keeper,
                "getattr": getattr, "tuple": tuple, "isinstance": isinstance,
                "lendable": (sip.simplewrapper, list, tuple)}
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

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

import ast

from qgis.PyQt import sip

HELPER = "__terralab_owned_call__"
OWNER_ATTR = "_terralab_owner"
_HELPER_FILE = "<snippet-owner>"


def tie(owner, value) -> None:

    if not isinstance(owner, sip.simplewrapper):
        return
    try:
        if not sip.ispyowned(owner):
            return
    except (TypeError, RuntimeError):
        return
    items = value if isinstance(value, (list, tuple)) else (value,)
    for item in items:
        if not isinstance(item, sip.simplewrapper) or item is owner:
            continue
        try:
            if not sip.ispyowned(item):
                setattr(item, OWNER_ATTR, owner)
        except (AttributeError, TypeError, RuntimeError):
            continue


_SOURCE = f"""
def {HELPER}(owner, name, /, *args, **kwargs):
    value = getattr(owner, name)(*args, **kwargs)
    tie(owner, value)
    return value
"""
_scope: dict = {"tie": tie, "getattr": getattr}
exec(compile(_SOURCE, _HELPER_FILE, "exec"), _scope)  # nosec B102
owned_call = _scope[HELPER]


class _Chains(ast.NodeTransformer):
    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        func = node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            chained = ast.Call(
                func=ast.Name(id=HELPER, ctx=ast.Load()),
                args=[func.value, ast.Constant(value=func.attr), *node.args],
                keywords=node.keywords,
            )
            return ast.copy_location(chained, node)
        return node


def compile_snippet(code: str, filename: str):




    tree = _Chains().visit(ast.parse(code, filename, "exec"))
    return compile(ast.fix_missing_locations(tree), filename, "exec")

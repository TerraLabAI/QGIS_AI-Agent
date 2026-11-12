# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import ast
import io
import tokenize

from .tool_describe import _algorithm, is_processing_call, source_text



_CONSOLE_NAMES = (
    ("iface", (), "from qgis.utils import iface"),
    ("project", (), "from qgis.core import QgsProject\nproject = QgsProject.instance()"),
    ("edit", (), "from qgis.core import edit"),
    ("canvas", ("iface",), "canvas = iface.mapCanvas()"),
    ("active", ("iface",), "active = iface.activeLayer()"),
    ("layers", ("project",),
     "def layers():\n"
     "    return [node.layer() for node in project.layerTreeRoot().findLayers() if node.layer()]"),
    ("layer", ("project",),
     "def layer(name_or_id):\n"
     "    found = project.mapLayer(name_or_id) or (project.mapLayersByName(name_or_id) or [None])[0]\n"
     "    if found is None:\n"
     '        raise LookupError(f"No layer is called {name_or_id!r}")\n'
     "    return found"),
    ("exports", ("project",),
     'import os\nexports = project.homePath() or os.path.expanduser("~")'),
)


def _bindings(code: str) -> list:

    try:
        needed = {node.id for node in ast.walk(ast.parse(code)) if isinstance(node, ast.Name)}
    except (SyntaxError, ValueError):
        return []
    for name, reads, _lines in reversed(_CONSOLE_NAMES):
        if name in needed:
            needed.update(reads)
    return [lines for name, _reads, lines in _CONSOLE_NAMES if name in needed]


def _console_safe(script: str) -> str:


    try:
        statements = ast.parse(script).body
        empty = {token.start[0] for token in tokenize.generate_tokens(io.StringIO(script).readline)
                 if token.type == tokenize.NL and not token.line.strip()}
    except (SyntaxError, ValueError, tokenize.TokenError):
        return script
    lines = script.splitlines()
    out, done = [], 0
    for statement in statements:
        first = min([statement.lineno, *(d.lineno for d in getattr(statement, "decorator_list", ()))])
        last = statement.end_lineno or first
        out += lines[done:first - 1]
        out += [lines[number - 1] for number in range(first, last + 1) if number not in empty]
        if hasattr(statement, "body"):
            out.append("")
        done = last
    out += lines[done:]
    return "\n".join(out).rstrip("\n") + "\n\n\n"


def _processing_run(name: str, args) -> str:



    args = args if isinstance(args, dict) else {}
    algorithm = str(args.get("algorithm_id") or args.get("algorithm") or "")
    params = args.get("parameters")
    if not (is_processing_call(name) and algorithm and isinstance(params, dict)):
        return ""
    loads = args.get("add_to_project", True) is not False
    found = _algorithm(args) if loads else None
    kept = dict(params)
    if found is not None:
        for key in params:
            definition = found.parameterDefinition(str(key))
            if definition is not None and definition.isDestination():
                kept[key] = "TEMPORARY_OUTPUT"
    body = "{\n" + "".join(f"    {key!r}: {value!r},\n" for key, value in kept.items()) + "}"
    run = "processing.runAndLoadResults" if loads else "processing.run"
    return f"import processing\n\n{run}({algorithm!r}, {body})"


def console_script(name: str, args) -> str:


    run = _processing_run(name, args)
    if run:
        return _console_safe(run)
    code, language = source_text(name, args)
    if language != "python":
        return ""
    head = "\n".join(_bindings(code))
    return _console_safe((head + "\n\n" if head else "") + code.rstrip())


__all__ = ["console_script"]

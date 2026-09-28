# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

import difflib
import os
import re

from qgis.core import QgsProject

from ..core.background import run_on_main_thread

_MAX_LAYERS = 5
_MAX_NEARBY = 5
_MAX_SCANNED = 500


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _file_of(layer) -> str:
    source = str(layer.source() or "").split("|", 1)[0]
    return source if os.path.isfile(source) else ""


def _project_layers(stem: str) -> list[dict]:
    wanted = _key(stem)
    if not wanted:
        return []
    found = []
    for layer in QgsProject.instance().mapLayers().values():
        path = _file_of(layer)
        if not path:
            continue
        own = os.path.splitext(os.path.basename(path))[0]
        if wanted in (_key(layer.name()), _key(own)):
            found.append({"name": layer.name(), "path": path})
            if len(found) >= _MAX_LAYERS:
                break
    return found


def _nearby(path: str) -> tuple[str, list[str]]:

    folder = os.path.dirname(path)
    while folder and not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            return "", []
        folder = parent
    if not folder:
        return "", []
    try:
        with os.scandir(folder) as entries:
            names = [entry.name for _, entry in zip(range(_MAX_SCANNED), entries)]
    except OSError:
        return folder, []

    ext = os.path.splitext(path)[1].lower()
    same = [name for name in names if os.path.splitext(name)[1].lower() == ext]
    close = difflib.get_close_matches(os.path.basename(path), same or names, n=_MAX_NEARBY, cutoff=0.6)
    return folder, [os.path.join(folder, name) for name in close]


def missing_file(path: str, message: str) -> dict:

    out: dict = {"_error": message, "code": "INVALID_ARGS"}
    stem = os.path.splitext(os.path.basename(path.rstrip("/\\")))[0]
    layers = run_on_main_thread(_project_layers, stem)
    folder, close = _nearby(path)
    if layers:
        out["project_layers"] = layers
    if close:
        out["closest_in_folder"] = close
    elif folder and folder != os.path.dirname(path):
        out["deepest_existing_folder"] = folder
    if layers:
        out["suggestion"] = ("A project layer carries this name; project_layers gives its real path, "
                             "which does not always match the layer name.")
    elif close:
        out["suggestion"] = "closest_in_folder lists close matches in that folder."
    else:
        out["suggestion"] = "No project layer or nearby file matches this path or layer name."
    return out

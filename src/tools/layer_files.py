# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import os
import time
import uuid

from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

from ..core import security
from ..core.host_platform import release_pooled_handles, remove_quietly, retry_file_op
from ..core.layer_release import remove_layers
from ..core.tool_registry import tool_error


def same_file(a: str, b: str) -> bool:


    try:
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            return True

        return ("~" in a or "~" in b) and os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except Exception:
        return False


def release_layers_at_path(path: str, skip_ids=None, delete_existing: bool = False) -> list:











    if not os.path.exists(path):
        return []
    removed = []
    skip_ids = set(skip_ids or ())
    project = QgsProject.instance()
    for layer_id, layer in list(project.mapLayers().items()):
        if layer_id in skip_ids:
            continue
        source = layer.source() or ""

        file_part = source.split("|", 1)[0]
        if not file_part or not same_file(file_part, path):
            continue



        release_pooled_handles(layer)
        removed += remove_layers([layer_id])

    if not delete_existing:
        return removed







    still_loaded = any(
        (lyr.source() or "").split("|", 1)[0]
        and same_file((lyr.source() or "").split("|", 1)[0], path)
        for lyr in project.mapLayers().values()
    )
    if not still_loaded:
        delete_with_retry(path)
    return removed


def staging_path(path: str) -> str:






    directory = os.path.dirname(path) or "."
    stem, ext = os.path.splitext(os.path.basename(path))
    tag = uuid.uuid4().hex[:8]
    staging = os.path.join(directory, f".{stem}.ai-agent-{tag}.partial{ext}")





    if len(staging) + 8 >= security.MAX_PATH and not security.long_paths_ok():
        staging = os.path.join(directory, f".ai-{tag}.partial{ext}")
    return staging


def _staged_files(staging: str) -> list:

    directory = os.path.dirname(staging) or "."
    stem = os.path.splitext(os.path.basename(staging))[0]
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names
            if os.path.splitext(name)[0] == stem and os.path.isfile(os.path.join(directory, name))]


def publish_staged_write(staging: str, path: str) -> str:






    produced = _staged_files(staging)
    if not os.path.isfile(staging):
        return f"the writer produced no file at {os.path.basename(staging)}"
    target_stem = os.path.splitext(path)[0]
    try:


        for produced_path in sorted(produced, key=lambda f: f == staging):
            extension = os.path.splitext(produced_path)[1]




            retry_file_op(os.replace, produced_path, f"{target_stem}{extension}")
    except OSError as exc:
        return str(exc)
    return ""


def detach_vector_layers_at_path(path: str) -> tuple[list[dict], dict]:








    from qgis.core import QgsMapLayerStyle

    matches = []
    for layer in QgsProject.instance().mapLayers().values():
        source = layer.source() or ""
        file_part = source.split("|", 1)[0]
        if not file_part or not same_file(file_part, path):
            continue
        if not isinstance(layer, QgsVectorLayer):
            return [], tool_error(f"A non-vector layer named '{layer.name()}' is using {os.path.basename(path)}.",
                                  hint="export_target_non_vector_layer", layer=layer.name(),
                                  file=os.path.basename(path))
        if layer.isEditable() or layer.isModified():
            return [], tool_error(f"Layer '{layer.name()}' has unsaved edits in {os.path.basename(path)}.",
                                  hint="export_target_unsaved_edits", layer=layer.name(),
                                  file=os.path.basename(path))
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        matches.append({
            "layer": layer,
            "source": source,
            "name": layer.name(),
            "provider": layer.providerType(),
            "style": style,
            "selection": list(layer.selectedFeatureIds()),
            "subset": layer.subsetString(),
        })

    detached = []
    for state in matches:
        layer = state["layer"]
        geometry = QgsWkbTypes.displayString(layer.wkbType()) or "None"
        crs = layer.crs().authid()
        temporary = geometry + (f"?crs={crs}" if crs else "")
        detached.append(state)


        release_pooled_handles(layer)
        layer.setDataSource(temporary, state["name"], "memory")
        if not layer.isValid() or layer.providerType() != "memory":
            restore_detached_layers(detached)
            return [], tool_error(f"Could not release layer '{state['name']}' before replacing "
                                  f"{os.path.basename(path)}.")
    return detached, {}


def restore_detached_layers(states: list[dict]) -> list[str]:

    failed = []
    for state in states:
        layer = state["layer"]
        layer.setDataSource(state["source"], state["name"], state["provider"])
        if not layer.isValid():
            failed.append(state["name"])
            continue
        if state["subset"]:
            layer.setSubsetString(state["subset"])
        state["style"].writeToLayer(layer)
        if state["selection"]:
            layer.selectByIds(state["selection"])
        layer.triggerRepaint()
    return failed


def discard_staged_write(staging: str) -> None:

    for produced_path in _staged_files(staging):
        remove_quietly(produced_path)


def delete_with_retry(path: str, attempts: int = 8, pause: float = 0.02) -> bool:



















    max_pause = 0.15


    try:
        from osgeo import gdal
    except Exception:
        gdal = None

    for attempt in range(attempts):
        if not os.path.exists(path):
            return True
        if gdal is not None:
            try:
                gdal.Unlink(path)
            except Exception:  # nosec B110
                pass
            if not os.path.exists(path):
                return True
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(min(pause * (2 ** attempt), max_pause))
    return False

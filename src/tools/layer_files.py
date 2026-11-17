# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import errno
import json
import os
import time
import uuid

from qgis.core import QgsProject, QgsVectorLayer, QgsWkbTypes

from ..core import security
from ..core.attribute_tables import close_tables, reopen_tables
from ..core.host_platform import IS_WINDOWS, file_holders, release_pooled_handles, remove_quietly, retry_file_op
from ..core.layer_release import remove_layers
from ..core.logger import log_warning
from ..core.snapshot_files import _sidecars
from ..core.tool_registry import tool_error


def same_file(a: str, b: str) -> bool:


    try:
        if os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b)):
            return True

        return ("~" in a or "~" in b) and os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)
    except Exception:
        return False




_READ_WITH_THE_FILE = ("-wal", "-shm", "-journal", ".aux.xml", ".ovr", ".msk")


def companions(path: str) -> list:









    return _sidecars(path)[1:] + [path + tail for tail in _READ_WITH_THE_FILE if os.path.isfile(path + tail)]


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
        left = companions(path)
        if delete_with_retry(path):
            for part in left:
                remove_quietly(part)
    return removed


def staging_path(path: str) -> str:






    directory = os.path.dirname(path) or "."
    finish_interrupted(directory)
    stem, ext = os.path.splitext(os.path.basename(path))
    tag = uuid.uuid4().hex[:8]
    staging = os.path.join(directory, f".{stem}.ai-agent-{tag}.partial{ext}")





    if security.fits_path(staging, 8):
        return staging
    staging = os.path.join(directory, f".ai-{tag}.partial{ext}")
    if security.fits_path(staging, 8):
        return staging




    room = security.MAX_PATH - 1 - len(directory) - 1 - len(ext)


    short = _spare_stem(directory, room - 8 if room > 8 else min(len(stem), room))
    return os.path.join(directory, short + ext) if short else staging


def _spare_stem(directory: str, length: int) -> str:


    try:
        taken = {os.path.splitext(name)[0].casefold() for name in os.listdir(directory)}
    except OSError:
        taken = set()
    for _ in range(64):
        fresh = uuid.uuid4().hex
        stem = ("~" + fresh)[:min(length, 9)] if length > 1 else fresh[:max(length, 0)]
        if stem and stem.casefold() not in taken:
            return stem
    return ""


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
    moves = [(part, f"{target_stem}{os.path.splitext(part)[1]}") for part in produced]
    written = {os.path.normcase(final) for _part, final in moves}


    return swap_in(moves, [part for part in companions(path) if os.path.normcase(part) not in written])


def _refusal(exc: OSError) -> str:

    holders = file_holders([exc.filename, exc.filename2]) if isinstance(exc, PermissionError) else []
    return f"{exc}; the file is open in {', '.join(holders)}" if holders else str(exc)


def swap_in(moves: list, stale: list = ()) -> str:








    try:
        if len(moves) == 1 and not stale:


            retry_file_op(os.replace, moves[0][0], moves[0][1])
            return ""
    except OSError as exc:
        return _refusal(exc)
    parked: list[tuple[str, str]] = []
    placed: list[str] = []
    tag = uuid.uuid4().hex[:8]
    journal = ""
    try:
        leaving = [final for _new, final in moves if os.path.lexists(final)] + list(stale)
        for final in leaving:


            if IS_WINDOWS and not os.access(final, os.W_OK):
                raise PermissionError(errno.EACCES, "The file is read-only", final)
        asides = []
        for number, final in enumerate(leaving):
            folder = os.path.dirname(final) or "."
            aside = os.path.join(folder, f".ai-{tag}-{number}.old")
            if not security.fits_path(aside):

                aside = os.path.join(folder, _spare_stem(folder, len(os.path.basename(final))))
            asides.append((final, aside))



        journal = os.path.join(os.path.dirname(moves[0][1]) or ".", f".ai-{tag}.swap")
        if asides and security.fits_path(journal):
            were = {os.path.normcase(final) for final in leaving}
            with open(journal, "w", encoding="utf-8") as handle:
                json.dump({"back": [[aside, final] for final, aside in asides],
                           "new": [final for _new, final in moves if os.path.normcase(final) not in were]}, handle)
        else:
            journal = ""
        for final, aside in asides:
            retry_file_op(os.replace, final, aside)
            parked.append((final, aside))
        for new, final in moves:
            retry_file_op(os.replace, new, final)
            placed.append(final)
    except OSError as exc:
        kept = {final for final, _aside in parked}
        for final in placed:
            if final not in kept:
                remove_quietly(final)
        for final, aside in reversed(parked):
            try:
                retry_file_op(os.replace, aside, final)
            except OSError as back:
                log_warning(f"{os.path.basename(final)} left as {aside}: it could not take its name back: {back}")
                journal = ""
        if journal:
            remove_quietly(journal)
        return _refusal(exc)
    if journal:
        remove_quietly(journal)
    for _final, aside in parked:
        remove_quietly(aside)
    return ""




_SWAP_ABANDONED_S = 60
_STAGED_ABANDONED_S = 600


def finish_interrupted(directory: str) -> None:







    try:
        names = os.listdir(directory)
    except OSError:
        return
    now = time.time()
    for name in names:
        if not name.startswith("."):
            continue
        full = os.path.join(directory, name)
        try:
            age = now - os.path.getmtime(full)
        except OSError:
            continue
        if name.startswith(".ai-") and name.endswith(".swap") and age > _SWAP_ABANDONED_S:
            try:
                with open(full, encoding="utf-8") as handle:
                    note = json.load(handle)
                for aside, final in note["back"]:
                    if os.path.lexists(aside):
                        retry_file_op(os.replace, aside, final)
                for final in note["new"]:
                    remove_quietly(final)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                log_warning(f"{name} in {directory}: an interrupted export could not be put back: {exc}")
                continue
            remove_quietly(full)
        elif ".partial" in name and (name.startswith(".ai-") or ".ai-agent-" in name) and age > _STAGED_ABANDONED_S:
            remove_quietly(full)


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
        state["tables"] = close_tables(layer)
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
        reopen_tables(layer, state.get("tables") or [])
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

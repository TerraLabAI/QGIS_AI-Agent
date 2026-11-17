# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















from __future__ import annotations

import contextlib

from qgis.core import QgsDatumTransform, QgsProject, QgsSettings

from .logger import log_warning

PROMPT_KEY = "projections/promptWhenMultipleTransformsExist"


HELD_KEY = "TerraLab/AIAgent/datum_prompt_held"

_depth = 0


_choosing = 0
_turned_off = False
_connected = False
_chosen: list[dict] = []


def _prompting(settings: QgsSettings) -> bool:
    value = settings.value(PROMPT_KEY, False, section=QgsSettings.Section.App)
    return str(value).lower() in ("true", "1")


def _put_back(settings: QgsSettings) -> None:

    settings.remove(PROMPT_KEY, QgsSettings.Section.App)
    if not _prompting(settings):
        settings.setValue(PROMPT_KEY, True, QgsSettings.Section.App)
    settings.remove(HELD_KEY)


def restore_after_crash() -> None:

    with contextlib.suppress(Exception):
        settings = QgsSettings()
        if settings.value(HELD_KEY, False, type=bool):
            _put_back(settings)


def _on_crs_changed() -> None:
    if _choosing <= 0:
        return
    project = QgsProject.instance()
    target = project.crs()
    if not target.isValid():
        return
    context = project.transformContext()
    seen: set[str] = set()
    added = []
    for layer in project.mapLayers().values():
        source = layer.crs()
        key = source.toWkt()
        if not source.isValid() or source == target or key in seen:
            continue
        seen.add(key)
        if context.hasTransform(source, target):
            continue
        operations = QgsDatumTransform.operations(source, target)

        if len(operations) < 2:
            continue
        chosen = next((op for op in operations if op.isAvailable), None)
        if chosen is None:
            continue
        context.addCoordinateOperation(source, target, chosen.proj, True)
        added.append({
            "layer": layer.name(),
            "from": source.authid() or source.description(),
            "from_name": source.description(),
            "to": target.authid() or target.description(),
            "to_name": target.description(),
            "operation": chosen.name,
            "accuracy_m": chosen.accuracy if chosen.accuracy >= 0 else None,
            "candidates": len(operations),
            "best_accuracy_m": min((op.accuracy for op in operations if op.accuracy >= 0), default=None),
        })
    if added:
        project.setTransformContext(context)
        _chosen.extend(added)


def _begin() -> None:
    global _turned_off, _connected
    settings = QgsSettings()
    if _prompting(settings):
        settings.setValue(HELD_KEY, True)
        settings.setValue(PROMPT_KEY, False, QgsSettings.Section.App)
        _turned_off = True
    QgsProject.instance().crsChanged.connect(_on_crs_changed)
    _connected = True


def _end() -> None:
    global _turned_off, _connected
    if _connected:
        with contextlib.suppress(TypeError, RuntimeError):
            QgsProject.instance().crsChanged.disconnect(_on_crs_changed)
        _connected = False
    if _turned_off:
        _turned_off = False
        _put_back(QgsSettings())


@contextlib.contextmanager
def held(choose: bool = True):

    global _depth, _choosing
    if _depth == 0:
        try:
            _begin()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Datum transformation question not held: {exc}")
    _depth += 1
    _choosing += 1 if choose else 0
    try:
        yield
    finally:
        _depth -= 1
        _choosing -= 1 if choose else 0
        if _depth == 0:
            try:
                _end()
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Datum transformation question not given back: {exc}")


def take() -> list[dict]:

    out = list(_chosen)
    _chosen.clear()
    return out

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import copy
import os
import shutil

from qgis.core import QgsMapLayer, QgsProject, QgsVectorLayer

from .logger import log_warning
from .qt_compat import qt_deleted


_PROJECT_SIGNALS = (
    "dirtySet", "isDirtyChanged", "layersAdded", "layersWillBeRemoved", "readProject", "cleared",
    "fileNameChanged", "projectSaved", "homePathChanged", "crsChanged", "ellipsoidChanged",
    "transformContextChanged", "customVariablesChanged", "labelingEngineSettingsChanged",
    "snappingConfigChanged", "avoidIntersectionsLayersChanged", "avoidIntersectionsModeChanged",
    "topologicalEditingChanged", "metadataChanged", "mapThemeCollectionChanged", "projectColorsChanged",
    "backgroundColorChanged", "selectionColorChanged", "distanceUnitsChanged", "areaUnitsChanged",
    "transactionModeChanged", "elevationShadingRendererChanged", "readMapLayer",
)
_TREE_SIGNALS = (
    "addedChildren", "removedChildren", "visibilityChanged", "nameChanged", "customPropertyChanged",
    "expandedChanged", "layerOrderChanged", "hasCustomLayerOrderChanged", "customLayerOrderChanged",
)




_LAYER_SIGNALS = (
    "dataChanged", "styleChanged", "rendererChanged", "configChanged", "nameChanged", "crsChanged",
    "customPropertyChanged", "editingStarted", "afterCommitChanges", "afterRollBack", "subsetStringChanged",
    "updatedFields", "opacityChanged", "blendModeChanged", "dataSourceChanged", "metadataChanged",
    "flagsChanged", "autoRefreshIntervalChanged", "mapTipTemplateChanged", "displayExpressionChanged",
    "readOnlyChanged", "repaintRequested", "legendChanged", "editFormConfigChanged",
)

_LAYER_PARTS = ("elevationProperties", "temporalProperties")
_MANAGER_SIGNALS = {
    "layoutManager": ("layoutAdded", "layoutRemoved", "layoutRenamed"),
    "bookmarkManager": ("bookmarkAdded", "bookmarkRemoved", "bookmarkChanged"),
    "mapThemeCollection": ("mapThemesChanged", "mapThemeChanged", "mapThemeRenamed"),
    "annotationManager": ("annotationAdded", "annotationRemoved", "annotationAboutToBeRemoved"),
    "relationManager": ("changed", "relationsLoaded"),
    "viewSettings": ("mapScalesChanged", "presetFullExtentChanged"),
    "timeSettings": ("temporalRangeChanged",),
    "displaySettings": ("bearingFormatChanged", "coordinateCrsChanged", "coordinateTypeChanged"),
}



COPY_PARTS = ("project.qgs", "project.qgd", "project_attachments.zip", "sections.xml")


LAYER_SECTIONS = "layer_sections.xml"

_count = 0




_watched: dict = {}

_connected: list = []


_last: tuple | None = None

_repaint: tuple | None = None


def _bump(*_args) -> None:
    global _count, _last
    _count += 1
    _last = None


def _repainted(key: int):
    def slot(*_args) -> None:
        global _repaint
        before = (key, _count, _last)
        _bump()
        _repaint = before
    return slot


def _selected(key: int):





    def slot(*_args) -> None:
        global _count, _last, _repaint
        if _repaint is not None and _repaint[0] == key and _repaint[1] + 1 == _count:
            _, _count, _last = _repaint
        _repaint = None
    return slot


def _connect(obj, names) -> None:
    key = id(obj)
    if key in _watched:
        return
    _watched[key] = obj
    vector = isinstance(obj, QgsVectorLayer)
    for name in names:

        signal = getattr(obj, name, None)
        if signal is not None:
            slot = _repainted(key) if vector and name == "repaintRequested" else _bump
            signal.connect(slot)
            _connected.append((obj, name, slot))
    selection = getattr(obj, "selectionChanged", None) if vector else None
    if selection is not None:
        slot = _selected(key)
        selection.connect(slot)
        _connected.append((obj, "selectionChanged", slot))


def watch(project=None) -> None:





    project = project or QgsProject.instance()
    layers = project.mapLayers()
    _drop_gone(layers)
    _connect(project, _PROJECT_SIGNALS)
    _connect(project.layerTreeRoot(), _TREE_SIGNALS)
    managers = {}
    for getter, names in _MANAGER_SIGNALS.items():
        manager = getattr(project, getter, None)
        if manager is not None:
            managers[getter] = manager()
            _connect(managers[getter], names)
    if managers.get("layoutManager") is not None:
        for layout in managers["layoutManager"].layouts():
            _connect(layout, ("changed", "nameChanged"))
    main_annotations = getattr(project, "mainAnnotationLayer", None)
    for layer in [*layers.values(), *([main_annotations()] if main_annotations else [])]:
        _connect(layer, _LAYER_SIGNALS)
        for part in _LAYER_PARTS:
            getter = getattr(layer, part, None)
            if getter is not None and getter() is not None:
                _connect(getter(), ("changed",))


def _drop_gone(layers: dict) -> None:



    held = {id(layer) for layer in layers.values()}
    main_annotations = getattr(QgsProject.instance(), "mainAnnotationLayer", None)
    if main_annotations is not None:
        held.add(id(main_annotations()))
    gone, released = set(), set()
    for key, obj in list(_watched.items()):
        if qt_deleted(obj):
            gone.add(key)
        elif isinstance(obj, QgsMapLayer) and key not in held:
            gone.add(key)
            released.add(key)
    for obj, name, slot in _connected:
        if id(obj) in released:
            try:
                getattr(obj, name).disconnect(slot)
            except TypeError:  # nosec B112
                continue
    for key in gone:
        del _watched[key]
    _connected[:] = [entry for entry in _connected if id(entry[0]) in _watched]


def remember(folder: str, quiet: bool, own_paths: str, layer_ids, project_state) -> None:

    global _last
    parts = tuple(name for name in COPY_PARTS if os.path.isfile(os.path.join(folder, name)))
    _last = (_count, folder, bool(quiet), str(own_paths or ""), frozenset(layer_ids), copy.deepcopy(project_state),
             parts)


def forget(folder: str | None = None) -> None:

    global _last
    if _last is not None and (folder is None or os.path.normcase(_last[1]) == os.path.normcase(folder)):
        _last = None


def reusable() -> tuple | None:



    if _last is None or _last[0] != _count or not _last[2]:
        return None
    if os.path.exists(os.path.join(_last[1], LAYER_SECTIONS)):
        return None
    return _last[1:]


def remove_parts(folder: str) -> None:

    for name in (*COPY_PARTS, LAYER_SECTIONS):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:  # nosec B112
            continue


def copy_parts(source: str, target: str, parts) -> bool:


    try:
        for name in parts:
            src = os.path.join(source, name)
            dst = os.path.join(target, name)
            shutil.copyfile(src, dst)
            if os.path.getsize(dst) != os.path.getsize(src):
                raise OSError(f"{name} copied short")
        return True
    except OSError as exc:
        log_warning(f"Snapshot copy not reused, written instead: {exc}")
        remove_parts(target)
        return False


def shutdown() -> None:

    global _last, _repaint
    for obj, name, slot in _connected:
        if qt_deleted(obj):
            continue
        try:
            getattr(obj, name).disconnect(slot)
        except Exception:  # nosec B112
            continue
    _connected.clear()
    _watched.clear()
    _last = None
    _repaint = None

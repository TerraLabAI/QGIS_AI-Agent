# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

























from __future__ import annotations

import hashlib
import os
import re
import time
import zipfile

from qgis.core import QgsProject, QgsVectorLayer

from .host_platform import release_pooled_handles
from .layer_order import read_back
from .logger import log, log_warning
from .snapshot_features import held_features, refill_from_copy
from .snapshot_files import restore_group
from .snapshot_paths import _canvas_held, _refresh_canvas, layer_file_path
from .snapshot_project import (
    LAYER_SECTIONS_FILE,
    SECTIONS_FILE,
    _core_sections,
    _dom,
    _layer_sections_by_id,
    _paths_absolute,
    _put_layer_sections,
    _signals_blocked,
)


_VOLATILE_ROOT_ATTRIBUTES = ("saveDateTime", "saveUser", "saveUserFull")
_FOLLOWED_GPS_ATTRIBUTES = ("destinationLayer", "destinationLayerName", "destinationLayerSource",
                            "destinationLayerProvider")
_MADE = "terralab/in_place_read"
_NO_ACTION = "{00000000-0000-0000-0000-000000000000}"
_MEMORY_UID = re.compile(r"&uid=\{[^}]*\}")


_LAYER_SECTIONS = ("projectlayers", "layer-tree-group", "layerorder", "snapping-settings",
                   "visibility-presets", "legend")
_NOW_FILE = "in_place_now.qgs"


def _document(path: str):
    if path.lower().endswith(".qgz"):
        with zipfile.ZipFile(path) as archive:
            name = next((m for m in archive.namelist() if m.lower().endswith(".qgs")), "")
            if not name:
                raise ValueError("no project document in the snapshot")
            data = archive.read(name)
    else:
        with open(path, "rb") as fh:
            data = fh.read()
    return _dom(data)


def _write_now(project, folder: str, absolute: bool):

    path = os.path.join(folder, _NOW_FILE)
    file_name, dirty = project.fileName(), project.isDirty()
    with _signals_blocked(project):
        try:
            with _paths_absolute(project, absolute):
                ok = bool(project.write(path))
        finally:
            project.setFileName(file_name)
            project.setDirty(dirty)
    try:
        if not ok:
            raise OSError(project.error() or "QGIS gave no reason")
        return _normalised(_document(path))
    finally:
        for leftover in (path, os.path.splitext(path)[0] + ".qgd"):
            try:
                if os.path.exists(leftover):
                    os.remove(leftover)
            except OSError as exc:
                log_warning(f"In-place roll back left {os.path.basename(leftover)}: {exc}")


def _text(element) -> str:
    from qgis.PyQt.QtXml import QDomDocument

    holder = QDomDocument()
    holder.appendChild(holder.importNode(element, True))
    return holder.toString(-1)


def _layer_elements(doc) -> dict:

    found = {}
    holder = doc.documentElement().firstChildElement("projectlayers")
    element = holder.firstChildElement("maplayer")
    while not element.isNull():
        found[element.firstChildElement("id").text()] = element
        element = element.nextSiblingElement("maplayer")
    return found


def _rest(doc, leave_out=()) -> str:

    from qgis.PyQt.QtXml import QDomDocument

    copy = QDomDocument()
    root = copy.importNode(doc.documentElement(), True).toElement()
    copy.appendChild(root)
    for name in leave_out:
        section = root.firstChildElement(name)
        while not section.isNull():
            following = section.nextSiblingElement(name)
            root.removeChild(section)
            section = following
    return copy.toString(-1)


def _normalised(doc):







    root = doc.documentElement()
    for name in _VOLATILE_ROOT_ATTRIBUTES:
        root.removeAttribute(name)
    gps = root.firstChildElement("ProjectGpsSettings")
    if not gps.isNull() and gps.attribute("destinationFollowsActiveLayer") == "1":
        for name in _FOLLOWED_GPS_ATTRIBUTES:
            gps.removeAttribute(name)
    settings = root.firstChildElement("snapping-settings").firstChildElement("individual-layer-settings")
    if not settings.isNull():
        children = []
        child = settings.firstChildElement()
        while not child.isNull():
            children.append(child)
            child = child.nextSiblingElement()
        for child in sorted(children, key=lambda element: element.attribute("id")):
            settings.appendChild(child)
    for tag, attribute in (("layer-tree-layer", "source"), ("datasource", "")):
        nodes = doc.elementsByTagName(tag)
        for i in range(nodes.count()):
            element = nodes.at(i).toElement()
            if attribute:
                if "uid={" in element.attribute(attribute):
                    element.setAttribute(attribute, _MEMORY_UID.sub("", element.attribute(attribute)))
            elif "uid={" in element.text():
                text = element.firstChild().toText()
                if not text.isNull():
                    text.setData(_MEMORY_UID.sub("", text.data()))
    actions = doc.elementsByTagName("defaultAction")
    for i in reversed(range(actions.count())):
        element = actions.at(i).toElement()
        if element.attribute("value") == _NO_ACTION:
            element.parentNode().removeChild(element)
    return doc


def _tree_places(doc) -> dict:

    places: dict = {}

    def walk(group, path):
        index = 0
        child = group.firstChildElement()
        while not child.isNull():
            tag = child.tagName()
            if tag == "layer-tree-layer":
                places[child.attribute("id")] = (path, index, child)
                index += 1
            elif tag == "layer-tree-group":
                walk(child, path + [child.attribute("name")])
                index += 1
            child = child.nextSiblingElement()

    root = doc.documentElement().firstChildElement("layer-tree-group")
    if not root.isNull():
        walk(root, [])
    return places


def _group_at(root, path: list):
    from qgis.core import QgsLayerTreeGroup

    group = root
    for name in path:
        group = next((node for node in group.children()
                      if isinstance(node, QgsLayerTreeGroup) and node.name() == name), None)
        if group is None:
            return None
    return group


def _memory_same(snapshot, layer) -> bool:

    record = snapshot.layers.get(layer.id()) or {}
    captured = snapshot.memory_features.get(layer.id())
    if captured is None:
        return record.get("feature_count") == 0 and held_features(layer) == 0
    if layer.subsetString() or held_features(layer) != len(captured):
        return False
    return _values_digest(layer.getFeatures()) == _values_digest(captured)


def _values_digest(features) -> bytes:

    digest = hashlib.blake2b(digest_size=20)
    for feature in features:
        digest.update(repr(list(feature.attributes())).encode("utf-8", "replace"))
        digest.update(bytes(feature.geometry().asWkb()) if feature.hasGeometry() else b"\0")
    return digest.digest()


def _needs_whole_read(element) -> bool:




    return (element.attribute("embedded") == "1"
            or element.firstChildElement("auxiliaryLayer").hasAttribute("key"))


def _captured_sections(snapshot) -> bytes:
    path = os.path.join(snapshot.dir, SECTIONS_FILE)
    if not os.path.isfile(path):
        return b""
    with open(path, "rb") as fh:
        return fh.read()


def _source_document(snapshot):

    doc = _document(snapshot.project_path)
    path = os.path.join(snapshot.dir, LAYER_SECTIONS_FILE)
    if os.path.isfile(path):
        with open(path, "rb") as fh:
            sections = _layer_sections_by_id(fh.read())
        if sections:
            _put_layer_sections(doc, doc.documentElement(), sections)
    return doc


def _read_layers(project, snapshot, back: list, source) -> list:

    elements = _layer_elements(source)
    file_name = project.fileName()
    failed = []
    with _signals_blocked(project):
        project.setFileName(snapshot.project_path)
    try:
        with read_back():
            for lid in back:
                layer = None
                try:
                    if project.readLayer(elements[lid]):
                        layer = project.mapLayer(lid)
                except Exception as exc:  # noqa: BLE001
                    log_warning(f"In-place roll back could not read layer {lid}: {exc}")
                if layer is None or not layer.isValid():
                    failed.append(lid)
    finally:
        with _signals_blocked(project):
            project.setFileName(file_name)
    return failed


def _put_nodes(project, back: list, places: dict) -> bool:







    from qgis.core import QgsLayerTreeLayer, QgsReadWriteContext

    root = project.layerTreeRoot()
    context = QgsReadWriteContext()
    context.setPathResolver(project.pathResolver())
    made = [node for node in root.findLayers() if node.layerId() in set(back)]
    for node in made:
        node.setCustomProperty(_MADE, True)
    try:

        for lid in sorted((lid for lid in back if lid in places), key=lambda lid: _order(places, lid)):
            path, index, element = places[lid]
            group = _group_at(root, path)
            if group is None:
                return False
            node = QgsLayerTreeLayer.readXml(element, context)
            if node is None:
                return False
            node.resolveReferences(project)
            group.insertChildNode(_position(group, index), node)
    finally:
        for node in made:
            node.parent().removeChildNode(node)
    return True


def _position(group, index: int) -> int:

    seen = 0
    for position, child in enumerate(group.children()):
        if child.customProperty(_MADE):
            continue
        if seen == index:
            return position
        seen += 1
    return len(group.children())


def _order(places: dict, lid: str) -> tuple:
    path, index, _element = places[lid]
    return (len(path), tuple(path), index)


def _refill(snapshot, project, back: list) -> list:

    short = []
    for lid in back:
        layer = project.mapLayer(lid)
        if not (isinstance(layer, QgsVectorLayer) and layer.providerType() == "memory"):
            continue
        record = snapshot.layers.get(lid) or {}
        captured = snapshot.memory_features.get(lid)
        copy = snapshot.memory_files.get(lid)
        if captured is not None:
            expected = len(captured)
            if captured:
                layer.dataProvider().addFeatures(captured)
        elif copy is not None:
            expected = int(copy[1])
            refill_from_copy(layer, copy[0], *copy[2:3])
        else:
            expected = int(record.get("feature_count") or 0)
        layer.updateExtents()
        if held_features(layer) < expected:
            short.append(str(layer.name()))
            continue
        subset = record.get("subset") or ""
        if subset and layer.setSubsetString(subset) is False:
            short.append(str(layer.name()))
    return short


def _project_settings_back(project, source, back: list) -> None:

    if not back:
        return
    from qgis.core import QgsSnappingConfig

    root = source.documentElement()
    if not root.firstChildElement("snapping-settings").isNull():
        config = QgsSnappingConfig(project)
        config.readProject(source)
        project.setSnappingConfig(config)
    if not root.firstChildElement("visibility-presets").isNull():
        project.mapThemeCollection().readXml(source)
    tree = root.firstChildElement("layer-tree-group")
    order = tree.firstChildElement("custom-order")
    if not order.isNull():
        layers = []
        item = order.firstChildElement("item")
        while not item.isNull():
            layer = project.mapLayer(item.text())
            if layer is not None:
                layers.append(layer)
            item = item.nextSiblingElement("item")
        project.layerTreeRoot().setCustomLayerOrder(layers)


def put_back_in_place(snapshot) -> dict | None:





    if not snapshot.captured or not snapshot.quiet or not os.path.isfile(snapshot.project_path):
        return None
    project = QgsProject.instance()
    started = time.monotonic()
    try:
        return _put_back(snapshot, project, started)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"In-place roll back gave way to a whole restore: {exc}")
        return None


def _put_back(snapshot, project, started: float) -> dict | None:
    if any(isinstance(layer, QgsVectorLayer) and layer.isEditable() for layer in project.mapLayers().values()):
        return _refuse("an edit session is open")
    captured_sections = _captured_sections(snapshot)
    if _core_sections(project) != captured_sections:
        return _refuse("the relations changed")
    then = _normalised(_document(snapshot.project_path))
    now = _write_now(project, snapshot.dir, bool(snapshot.own_paths))
    if _rest(then, _LAYER_SECTIONS) != _rest(now, _LAYER_SECTIONS):
        return _refuse("a project setting changed")
    then_layers, now_layers = _layer_elements(then), _layer_elements(now)
    added = [lid for lid in now_layers if lid not in then_layers]
    changed = {lid for lid in then_layers
               if lid in now_layers and _text(then_layers[lid]) != _text(now_layers[lid])}
    groups = snapshot._groups_to_put_back()
    if groups:
        keys = {snapshot._path_key(src) for group in groups for src, _dst in group}
        for lid, layer in project.mapLayers().items():
            path = layer_file_path(layer)
            if lid in then_layers and path and snapshot._path_key(path) in keys:
                changed.add(lid)
    snapshot.wait_for_features(2.0)
    for lid, layer in project.mapLayers().items():
        if (lid in then_layers and lid not in changed and isinstance(layer, QgsVectorLayer)
                and layer.providerType() == "memory" and not _memory_same(snapshot, layer)):
            changed.add(lid)
    back = [lid for lid in then_layers if lid not in now_layers or lid in changed]
    if not added and not back and not groups:
        if _rest(then) != _rest(now):
            return _refuse("the layer tree or a layer setting of the project changed")
        log(f"Roll back of run {snapshot.run_id[:8]}: nothing differs from the restore point "
            f"({time.monotonic() - started:.2f} s, no layer read)")
        project.setDirty(not snapshot._same_as_saved(project.fileName()))
        return _outcome(0, 0, [], [], "Nothing to put back.")
    if any(_needs_whole_read(then_layers[lid]) for lid in back):
        return _refuse("a layer is embedded or has auxiliary storage")
    if captured_sections and any(lid.encode("utf-8") in captured_sections for lid in back):
        return _refuse("a layer to read back is in a relation")
    pending = set(getattr(snapshot._pass, "memory_planned", None) or {})
    if any(lid in pending and lid not in snapshot.memory_features for lid in back):
        return _refuse("a memory layer's copy is still being written")

    files_put_back: list = []
    with _canvas_held():
        leaving = [lid for lid in added + sorted(changed) if project.mapLayer(lid) is not None]
        for lid in leaving:
            release_pooled_handles(project.mapLayer(lid))
        if leaving:
            project.removeMapLayers(leaving)
        for group in groups:
            pairs = [pair for pair in group if not pair[0].lower().endswith((".qgs", ".qgz"))]
            if pairs:
                restore_group(pairs)
                files_put_back.extend(src for src, _dst in pairs)
        source = _source_document(snapshot)
        failed = _read_layers(project, snapshot, back, source)
        if failed:
            return _refuse(f"{len(failed)} layer(s) could not be read back")
        if not _put_nodes(project, back, _tree_places(source)):
            return _refuse("a layer tree group is gone")
        short = _refill(snapshot, project, back)
        if short:
            return _refuse("memory features did not all come back: " + ", ".join(short[:3]))
        _project_settings_back(project, source, back)
    _refresh_canvas()
    if files_put_back:
        snapshot._restamp()


    proof = _write_now(project, snapshot.dir, bool(snapshot.own_paths))
    if _rest(proof) != _rest(then):
        return _refuse("the project still differs after the layers came back: "
                       + _first_difference(_rest(then), _rest(proof)))
    if snapshot._groups_to_put_back() or _core_sections(project) != captured_sections:
        return _refuse("a file or a relation still differs")
    for lid in back:
        layer = project.mapLayer(lid)
        if (lid in snapshot.memory_features and isinstance(layer, QgsVectorLayer)
                and not layer.subsetString() and not _memory_same(snapshot, layer)):
            return _refuse(f"memory layer {layer.name()} differs after its refill")
    project.setDirty(not snapshot._same_as_saved(project.fileName()))
    log(f"Roll back of run {snapshot.run_id[:8]} in place: {len(added)} layer(s) removed, {len(back)} read back, "
        f"{len(files_put_back)} file(s) put back, {time.monotonic() - started:.2f} s")
    return _outcome(len(added), len(back), files_put_back, back, "Project restored.")


def _first_difference(a: str, b: str) -> str:

    at = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"copy ...{a[max(0, at - 80):at + 80]!r} / now ...{b[max(0, at - 80):at + 80]!r}"


def _refuse(why: str) -> None:
    log(f"In-place roll back not possible ({why}): the whole project is read back")
    return


def _outcome(removed: int, read: int, files: list, back: list, message: str) -> dict:
    project = QgsProject.instance()
    return {"ok": True, "project_read": True, "in_place": True, "layers_removed": removed,
            "layers_read_back": read, "files_restored": list(files), "files_put_back": list(files),
            "layers": len(project.mapLayers()), "file_restore_errors": [],
            "memory_layers_refilled": 0, "memory_layers_incomplete": [], "message": message}

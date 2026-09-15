# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The project sections a quiet capture keeps apart and a restore puts back."""






from __future__ import annotations

import contextlib


@contextlib.contextmanager
def _signals_blocked(project):
    """The project says nothing while the block runs."""









    block = getattr(project, "blockSignals", None)
    previous = block(True) if callable(block) else False
    try:
        yield
    finally:
        if callable(block):
            block(bool(previous))


def _enum_int(value) -> int:
    return int(getattr(value, "value", value))


def _core_sections(project) -> bytes:
    """The project's relations, as QGIS writes them into a saved project."""








    manager = project.relationManager()
    relations = dict(manager.relations() or {})
    polymorphic = dict(manager.polymorphicRelations() or {})
    if not relations and not polymorphic:
        return b""
    from qgis.core import Qgis
    from qgis.PyQt.QtXml import QDomDocument

    from .qt_compat import enum_member

    generated = _enum_int(enum_member(Qgis, "RelationshipType", "Generated"))
    doc = QDomDocument()
    root = doc.createElement("qgis")
    doc.appendChild(root)
    node = doc.createElement("relations")
    root.appendChild(node)
    for relation in relations.values():

        if _enum_int(relation.type()) != generated:
            relation.writeXml(node, doc)
    node = doc.createElement("polymorphicRelations")
    root.appendChild(node)
    for relation in polymorphic.values():
        relation.writeXml(node, doc)
    return bytes(doc.toByteArray())




_CORE_SECTIONS = ("relations", "polymorphicRelations")
SECTIONS_FILE = "sections.xml"

RESTORE_FILE = "restore.qgz"


def _children(element) -> list:
    nodes = element.childNodes()
    return [nodes.at(i) for i in range(nodes.count())]


def _dom(data: bytes):
    from qgis.PyQt.QtXml import QDomDocument

    doc = QDomDocument()
    doc.setContent(data)
    root = doc.documentElement()
    if root.isNull() or root.tagName() != "qgis":
        raise ValueError("not a project document")
    return doc


def _handler_sections(project):
    """What every writeProject handler adds to a project now, in a document of its own."""






    signal = getattr(project, "writeProject", None)
    if signal is None or not hasattr(signal, "emit"):
        return None
    from qgis.PyQt.QtXml import QDomDocument

    doc = QDomDocument()
    doc.appendChild(doc.createElement("qgis"))
    signal.emit(doc)
    return doc




LAYER_SECTIONS_FILE = "layer_sections.xml"


def _layer_handler_sections(project) -> bytes:
    """What every writeMapLayer handler adds to each layer now, in a document of its own."""







    signal = getattr(project, "writeMapLayer", None)
    if signal is None or not hasattr(signal, "emit"):
        return b""
    try:
        if project.receivers(signal) <= 0:
            return b""
    except Exception:  # nosec B110 - a count that cannot be read: ask the handlers
        pass
    from qgis.PyQt.QtXml import QDomDocument

    doc = QDomDocument()
    root = doc.createElement("qgis")
    doc.appendChild(root)
    holder = doc.createElement("layerSections")
    root.appendChild(holder)
    kept = False
    for lid, layer in project.mapLayers().items():
        element = doc.createElement("maplayer")
        signal.emit(layer, element, doc)
        if element.hasChildNodes() or element.attributes().count():
            wrapper = doc.createElement("layer")
            wrapper.setAttribute("id", lid)
            wrapper.appendChild(element)
            holder.appendChild(wrapper)
            kept = True
    return bytes(doc.toByteArray()) if kept else b""


def _layer_sections_by_id(data: bytes) -> dict:
    """Layer id to the handler element a capture kept for it."""
    found = {}
    for holder in _children(_dom(data).documentElement()):
        if holder.nodeName() != "layerSections":
            continue
        for node in _children(holder):
            wrapper = node.toElement()
            inner = wrapper.firstChildElement("maplayer") if not wrapper.isNull() else None
            if inner is not None and not inner.isNull() and wrapper.attribute("id"):
                found[wrapper.attribute("id")] = inner
    return found


def _put_layer_sections(doc, root, sections: dict) -> bool:
    """Each captured layer's handler XML, added to that layer's element of the copy."""
    added = False
    layers = root.firstChildElement("projectlayers")
    for node in ([] if layers.isNull() else _children(layers)):
        element = node.toElement()
        if element.isNull() or element.tagName() != "maplayer":
            continue
        captured = sections.get(element.firstChildElement("id").text())
        if captured is None:
            continue
        attributes = captured.attributes()
        for i in range(attributes.count()):
            attribute = attributes.item(i).toAttr()
            if not element.hasAttribute(attribute.name()):
                element.setAttribute(attribute.name(), attribute.value())
                added = True
        for child in _children(captured):
            element.appendChild(doc.importNode(child, True))
            added = True
    return added


def _merged_copy(project_path: str, target: str, captured: bytes | None, current,
                 layers: bytes | None = None) -> bool:
    """Write ``target``: the snapshot project with the sections its capture left out."""








    import zipfile

    additions = []
    if captured:
        additions = [(node, True) for node in _children(_dom(captured).documentElement())
                     if node.nodeName() in _CORE_SECTIONS]
    if current is not None:
        additions += [(node, False) for node in _children(current.documentElement())
                      if node.nodeName() not in _CORE_SECTIONS]
    layer_sections = _layer_sections_by_id(layers) if layers else {}
    if not additions and not layer_sections:
        return False
    with zipfile.ZipFile(project_path) as archive:
        members = archive.namelist()
        name = next((m for m in members if m.lower().endswith(".qgs")), "")
        if not name:
            raise ValueError("no project document in the snapshot")
        doc = _dom(archive.read(name))
        root = doc.documentElement()
        present = {node.nodeName() for node in _children(root)}
        added = False
        for node, replace in additions:
            section = node.nodeName()
            if replace:
                for old in _children(root):
                    if old.nodeName() == section:
                        root.removeChild(old)
            elif section in present:
                continue
            root.appendChild(doc.importNode(node, True))
            added = True
        if layer_sections and _put_layer_sections(doc, root, layer_sections):
            added = True
        if not added:
            return False
        with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as out:
            for member in members:
                out.writestr(member, bytes(doc.toByteArray()) if member == name else archive.read(member))
    return True

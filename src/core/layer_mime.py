# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read the layers a drag from the QGIS Layers panel carries."""











from __future__ import annotations

import xml.etree.ElementTree as ET  # nosec B405 - parsed through _fromstring below

try:





    from defusedxml.ElementTree import fromstring as _fromstring
except ImportError:
    _fromstring = ET.fromstring  # nosec B314 - no defusedxml on this host; payload is QGIS's own

from qgis.PyQt.QtCore import QCoreApplication

LAYER_TREE_MIME = "application/qgis.layertreemodeldata"
LAYER_URI_MIME = "application/x-vnd.qgis.qgis.uri"
LAYER_MIME_TYPES = (LAYER_TREE_MIME, LAYER_URI_MIME)

_URI_LAYER_ID_FIELD = 6
_URI_NAME_FIELD = 2


_CHEAP_COUNT_PROVIDERS = ("ogr", "memory", "spatialite", "delimitedtext", "gpkg", "virtual")


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentLayerMime", text)


def _as_bytes(data) -> bytes:
    if data is None:
        return b""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("utf-8")
    try:
        return bytes(data)
    except (TypeError, ValueError):
        return b""


def layer_ids_from_tree_xml(payload) -> list[str]:
    """The ``id`` of every ``layer-tree-layer`` in the XML, in document order."""
    raw = _as_bytes(payload).strip()
    if not raw:
        return []
    try:
        root = _fromstring(raw)
    except Exception:  # noqa: BLE001 - a malformed or refused payload is simply not a layer drag
        return []
    ids: list[str] = []
    for element in root.iter("layer-tree-layer"):
        layer_id = str(element.get("id") or "").strip()
        if layer_id and layer_id not in ids:
            ids.append(layer_id)
    return ids


def decode_uri_fields(line: str) -> list[str]:
    """``QgsMimeDataUtils.decode``: colon separated fields, ``\\`` escapes."""
    fields: list[str] = []
    item = ""
    escaped = False
    for ch in line:
        if ch == "\\" and not escaped:
            escaped = True
        elif ch == ":" and not escaped:
            fields.append(item)
            item = ""
        else:
            item += ch
            escaped = False
    fields.append(item)
    return fields


def uri_entries(payload) -> list[dict]:
    """``[{layer_id, name, provider, uri}]`` for each line of a uri list."""
    text = _as_bytes(payload).decode("utf-8", errors="replace")
    out: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = decode_uri_fields(line)
        if len(fields) < 4:
            continue
        layer_id = fields[_URI_LAYER_ID_FIELD] if len(fields) > _URI_LAYER_ID_FIELD else ""
        out.append({"layer_id": layer_id.strip(), "name": fields[_URI_NAME_FIELD],
                    "provider": fields[1], "uri": fields[3], "type": fields[0]})
    return out


def layer_ids_from_uri_list(payload) -> list[str]:
    ids: list[str] = []
    for entry in uri_entries(payload):
        layer_id = entry.get("layer_id") or ""
        if layer_id and layer_id not in ids:
            ids.append(layer_id)
    return ids


def mime_has_layers(mime) -> bool:
    """True when the drag carries one of the Layers panel formats."""
    if mime is None:
        return False
    try:
        return any(mime.hasFormat(fmt) for fmt in LAYER_MIME_TYPES)
    except (AttributeError, TypeError):
        return False


def layer_ids_from_mime(mime) -> list[str]:
    """Every layer id the drag names, the tree XML first, the uri list as a fallback for the ids the XML lacks."""

    if mime is None:
        return []
    ids: list[str] = []
    for fmt, parser in ((LAYER_TREE_MIME, layer_ids_from_tree_xml),
                        (LAYER_URI_MIME, layer_ids_from_uri_list)):
        try:
            if not mime.hasFormat(fmt):
                continue
            found = parser(mime.data(fmt))
        except (AttributeError, TypeError, ValueError):
            continue
        for layer_id in found:
            if layer_id not in ids:
                ids.append(layer_id)
    return ids




def mention_span(before: str) -> tuple[int, int] | None:
    """``(start, end)`` in ``before`` (the block text up to the caret) of the ``@word`` under the caret plus the one space before it, or None when."""


    at = before.rfind("@")
    if at < 0:
        return None
    if at > 0 and not before[at - 1].isspace():
        return None
    word = before[at + 1:]
    if any(ch.isspace() for ch in word):
        return None
    start = at - 1 if at > 0 and before[at - 1] == " " else at
    return start, len(before)




def layer_chip(layer) -> dict | None:
    """The context chip a layer becomes: ``{kind: "layer", label, value}``."""
    if layer is None:
        return None
    try:
        label = str(layer.name() or "")
        value = str(layer.id() or "")
    except (AttributeError, TypeError, RuntimeError):
        return None
    if not value:
        return None
    return {"kind": "layer", "label": label or value, "value": value}


def cheap_feature_count(layer) -> int | None:
    """The feature count when the provider answers from its header; None when it would scan the data or when the layer has no features."""

    try:
        provider = str(layer.providerType() or "").lower()
        if provider not in _CHEAP_COUNT_PROVIDERS:
            return None
        count = int(layer.featureCount())
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return None
    return count if count >= 0 else None


def card_kind_line(geometry: str, count: int | None = None) -> str:
    """``Polygon layer``, ``Raster layer``, ``Layer``, with the count when known."""
    geometry = (geometry or "").strip()
    if geometry:
        kind = tr("{geometry} layer").format(geometry=geometry)
    else:
        kind = tr("Layer")
    if count is None:
        return kind
    if count == 1:
        return tr("{kind}, 1 feature").format(kind=kind)
    return tr("{kind}, {count} features").format(kind=kind, count=f"{count:,}".replace(",", " "))


class ChipRow:
    """The cards above the composer, as data: one message's worth."""






    def __init__(self):
        self._chips: list[dict] = []

    def chips(self) -> list[dict]:
        return [dict(c) for c in self._chips]

    def __len__(self) -> int:
        return len(self._chips)

    @staticmethod
    def key(chip: dict) -> tuple[str, str]:
        return str(chip.get("kind") or ""), str(chip.get("value") or "")

    def add(self, chip: dict) -> bool:
        """True when the chip is new; a chip already in the row is left alone."""
        if not isinstance(chip, dict) or not chip.get("value"):
            return False
        key = self.key(chip)
        if any(self.key(c) == key for c in self._chips):
            return False
        self._chips.append(dict(chip))
        return True

    def remove(self, kind: str, value: str) -> bool:
        before = len(self._chips)
        self._chips = [c for c in self._chips if self.key(c) != (str(kind), str(value))]
        return len(self._chips) != before

    def clear(self) -> None:
        self._chips = []

    def take(self) -> list[dict]:
        """The chips for the message being sent; the row is empty afterwards."""
        out = self.chips()
        self._chips = []
        return out

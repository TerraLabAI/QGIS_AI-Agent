# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Licence and attribution written on every layer a tool just added."""






from __future__ import annotations

from . import catalog
from .logger import log_warning

UNKNOWN = "unknown"
_ABSTRACT_MARK = "Licence:"


def credit_layer(layer, licence: str = "", attribution: str = "") -> dict:
    """Write what is known on the layer and say what it now carries."""







    licence = (licence or "").strip()[:300]
    attribution = (attribution or "").strip()[:500]
    metadata = layer.metadata()
    if not licence:
        licence = next((str(text).strip() for text in metadata.licenses() if str(text).strip()), "")
    layer_attribution = str(layer.attribution() or "").strip()
    if not attribution:



        attribution = layer_attribution or next(
            (str(text).strip()[:500] for text in metadata.rights() if str(text).strip()), "")
    changed = False
    licences = list(metadata.licenses() or [])
    if licence and licence not in licences:
        metadata.setLicenses(licences + [licence])
        changed = True
    if attribution and not metadata.rights():
        metadata.setRights([attribution])
        changed = True
    bits = []
    if licence:
        bits.append(f"{_ABSTRACT_MARK} {licence}.")
    if attribution:
        bits.append(f"Attribution: {attribution}.")
    if bits:
        line = " ".join(bits)
        abstract = str(metadata.abstract() or "")
        if line not in abstract:
            metadata.setAbstract(f"{abstract.rstrip()}\n\n{line}" if abstract.strip() else line)
            changed = True
    if changed:
        layer.setMetadata(metadata)
    if attribution and not layer_attribution:
        layer.setAttribution(attribution)
    out = {"licence": licence or UNKNOWN}
    if attribution:
        out["attribution"] = attribution
    return out


def literal_label_text(text) -> str:
    """The text to give a layout label so it prints ``text`` as written."""







    text = str(text or "")
    text = text.replace("[%", "[% '[%' %]")
    return text.replace("$CURRENT_DATE", "$[% 'CURRENT_DATE' %]")


def source_of(layer) -> tuple[str, str]:
    """The address a layer reads and the sublayer inside it, main thread only."""







    try:
        from qgis.core import QgsDataSourceUri, QgsProviderRegistry

        source = str(layer.source() or "")
        parts = QgsProviderRegistry.instance().decodeUri(layer.providerType(), source) or {}
        url = str(parts.get("url") or parts.get("path") or "").strip()
        sublayer = (parts.get("layers") or parts.get("typename")
                    or parts.get("layerName") or parts.get("layer") or "")
        if not url or not sublayer:


            params = QgsDataSourceUri(source)
            if not url:
                url = str(params.param("url") or "").strip()
            if not sublayer:
                sublayer = params.param("typename") or params.param("layers") or ""
        if not url and source.startswith(("http", "/vsicurl/")):
            url = source
        if url.startswith("/vsicurl/"):
            url = url[len("/vsicurl/"):]
        if isinstance(sublayer, (list, tuple)):
            sublayer = ",".join(str(item) for item in sublayer)
        return url, str(sublayer or "")
    except Exception:  # noqa: BLE001 - a source that will not decode names no address
        return "", ""


def _entry_for(result, layer_id: str, name: str) -> dict | None:
    """The result entry that names this layer, by id first, then by name."""
    if not isinstance(result, dict):
        return None
    candidates = [result]
    rows = result.get("layers")
    if isinstance(rows, list):
        candidates.extend(row for row in rows if isinstance(row, dict))
    for entry in candidates:
        if entry.get("layer_id") == layer_id:
            return entry
    for entry in candidates:
        if entry.get("layer_name") == name or entry.get("name") == name:
            return entry
    return None


def credit_added(layers, result) -> None:
    """Credit every layer one tool call added, main thread only."""









    for layer in layers or ():
        if layer is None:
            continue
        try:
            layer_id = layer.id()
            name = layer.name()
            entry = _entry_for(result, layer_id, name)
            licence = ""
            attribution = ""
            if entry is not None:
                licence = str(entry.get("licence") or entry.get("license") or "").strip()
                attribution = str(entry.get("attribution") or "").strip()
                if entry is not result and not licence and not attribution:
                    licence = str(result.get("licence") or result.get("license") or "").strip()
                    attribution = str(result.get("attribution") or "").strip()
            if not licence and not attribution:
                url, sublayer = source_of(layer)
                row = catalog.source_licence(url, sublayer) if url else None
                if row is None:
                    collection = ""
                    for holder in (entry, result if isinstance(result, dict) else None):
                        if isinstance(holder, dict) and holder.get("collection"):
                            collection = str(holder.get("collection")).strip()
                            break
                    if collection:
                        row = catalog.source_licence("stac:" + collection)
                if row is not None:
                    licence = str(row.get("licence") or "").strip()
                    attribution = str(row.get("attribution") or "").strip()
            credit = credit_layer(layer, licence, attribution)
            if entry is not None and isinstance(result, dict) and "_error" not in result:
                entry.setdefault("licence", credit["licence"])
                if credit.get("attribution"):
                    entry.setdefault("attribution", credit["attribution"])
        except Exception as exc:  # noqa: BLE001 - a layer that is added is added, credited or not
            log_warning(f"Licence and attribution not written on a layer: {exc}")

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import re
from urllib.parse import urlsplit

from qgis.PyQt.QtCore import QCoreApplication

from . import catalog, data_date
from .logger import log_warning
from .provenance import plain_source

UNKNOWN = "unknown"
_ABSTRACT_MARK = "Licence:"
_SEP = " \u00b7 "


LOADER_TOOLS = frozenset({
    "add_data", "add_vector_layer", "add_raster_layer", "add_vector_from_url", "add_cog_layer",
    "add_wfs_layer", "add_wms_layer", "add_xyz_layer", "add_pmtiles_layer", "add_arcgis_rest_layer",
    "add_layer_from_connection",
})


_TOOLTIP_READS_METADATA = 34000
_CREDIT_PREFIX = re.compile(r"^\s*(?:\u00a9|\(c\)|copyright|source\s*:|data\s*:)\s*", re.IGNORECASE)


def tr(text: str) -> str:
    return QCoreApplication.translate("AIAgentLicence", text)


def _attribution_holder(layer):






    properties = layer.serverProperties() if hasattr(layer, "serverProperties") else None
    if properties is not None and hasattr(properties, "attribution") and hasattr(properties, "setAttribution"):
        return properties
    return layer


def layer_attribution(layer) -> str:

    return str(_attribution_holder(layer).attribution() or "").strip()


def set_layer_attribution(layer, text: str) -> None:
    _attribution_holder(layer).setAttribution(text)


def credit_layer(layer, licence="", attribution="") -> dict:












    def _texts(value, limit: int) -> list:
        items = value if isinstance(value, (list, tuple)) else [value]
        out = []
        for item in items:
            text = str(item or "").strip()[:limit]
            if text and text not in out:
                out.append(text)
        return out

    wanted = _texts(licence, 300)
    credits = _texts(attribution, 500)
    metadata = layer.metadata()
    licences = [str(text).strip() for text in metadata.licenses() if str(text).strip()]
    if not wanted:
        wanted = licences[:1]
    current = layer_attribution(layer)
    if not credits:



        credits = [current] if current else _texts(list(metadata.rights())[:1], 500)
    changed = False
    missing = [text for text in wanted if text not in licences]
    if missing:
        metadata.setLicenses(list(metadata.licenses() or []) + missing)
        changed = True
    if credits and not metadata.rights():
        metadata.setRights(credits)
        changed = True
    licence = "; ".join(wanted)
    attribution = "; ".join(credits)[:500]
    bits = []

    if licence:
        bits.append(f"{_ABSTRACT_MARK} {licence.rstrip('.')}.")
    if attribution:
        bits.append(f"Attribution: {attribution.rstrip('.')}.")
    if bits:
        line = " ".join(bits)
        abstract = str(metadata.abstract() or "")
        if line not in abstract:
            metadata.setAbstract(f"{abstract.rstrip()}\n\n{line}" if abstract.strip() else line)
            changed = True
    if changed:
        layer.setMetadata(metadata)
    if attribution and not current:
        set_layer_attribution(layer, attribution)
    out = {"licence": licence or UNKNOWN}
    if attribution:
        out["attribution"] = attribution
    return out


def _locale_date(first, pattern: str) -> str:

    try:
        from qgis.PyQt.QtCore import QDate

        from .i18n import words_locale

        text = words_locale().toString(QDate(first.year, first.month, first.day), pattern)
        if isinstance(text, str) and text:
            return text
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    return first.isoformat() if pattern != "MMMM yyyy" else first.isoformat()[:7]


def date_text(value) -> str:

    from .i18n import day_pattern

    parsed = data_date.parse(value)
    if parsed is None:
        return tr("Date unknown")
    kind, first, last = parsed
    if kind == data_date.LIVE:
        return tr("Live, loaded {day}").format(day=_locale_date(first, day_pattern()))
    if kind == "year":
        return str(first.year)
    if kind == "span":
        return f"{first.year}-{last.year}"
    if kind == "month":
        return _locale_date(first, "MMMM yyyy")
    return _locale_date(first, day_pattern())


def provider_of(attribution: str, page_url: str = "") -> str:

    first = re.split(r"[,;|]", str(attribution or ""), maxsplit=1)[0]
    first = _CREDIT_PREFIX.sub("", first).strip(" .\u00a9")
    if first and len(first) <= 60:
        return first
    host = (urlsplit(page_url).hostname or "") if page_url else ""
    return host[4:] if host.startswith("www.") else host


def _prepend_head(metadata, head: str) -> bool:

    abstract = str(metadata.abstract() or "")
    if not head or abstract.startswith(head):
        return False
    metadata.setAbstract(f"{head}\n\n{abstract.strip()}" if abstract.strip() else head)
    return True


def _tooltip_on_older_qgis(layer, head: str) -> None:

    try:
        from qgis.core import Qgis

        if int(Qgis.QGIS_VERSION_INT) >= _TOOLTIP_READS_METADATA:
            return
        properties = layer.serverProperties()
        if not str(properties.abstract() or "").strip():
            properties.setAbstract(head)
    except Exception:  # noqa: BLE001  # nosec B110
        pass


def write_source_sheet(layer, facts: dict, licence: str, attribution: str) -> None:








    from qgis.core import QgsAbstractMetadataBase, QgsDateTimeRange
    from qgis.PyQt.QtCore import QDate, QDateTime, QTime

    metadata = layer.metadata()
    changed = False
    title = str(facts.get("title") or "").strip()
    page_url = str(facts.get("page_url") or "").strip()
    if title and not str(metadata.title() or "").strip():
        metadata.setTitle(title)
        changed = True
    provider = provider_of(attribution, page_url)
    if provider:
        contacts = list(metadata.contacts() or [])
        if not any(str(contact.organization or "") == provider for contact in contacts):
            contact = QgsAbstractMetadataBase.Contact()
            contact.organization = provider
            contact.role = "distributor"
            metadata.setContacts(contacts + [contact])
            changed = True
    parsed = data_date.parse(facts.get("data_date"))
    if parsed is not None:
        extent = metadata.extent()
        if not extent.temporalExtents():
            _, first, last = parsed
            extent.setTemporalExtents([QgsDateTimeRange(
                QDateTime(QDate(first.year, first.month, first.day), QTime(0, 0, 0)),
                QDateTime(QDate(last.year, last.month, last.day), QTime(23, 59, 59)))])
            metadata.setExtent(extent)
            changed = True
    if page_url:
        links = list(metadata.links() or [])
        if not any(str(link.url or "") == page_url for link in links):
            link = QgsAbstractMetadataBase.Link()
            link.name = tr("Dataset page and licence terms")
            link.type = "WWW:LINK"
            link.url = page_url
            metadata.setLinks(links + [link])
            changed = True
    shown_licence = licence if licence and licence != UNKNOWN else ""
    head = _SEP.join(part for part in (provider, title, date_text(facts.get("data_date")), shown_licence) if part)
    changed = _prepend_head(metadata, head) or changed
    if changed:
        layer.setMetadata(metadata)
    _tooltip_on_older_qgis(layer, head)


def write_own_source(layer, address: str) -> None:

    host = ""
    if address.startswith(("http://", "https://")):
        host = urlsplit(address).hostname or ""
    head = tr("Your own source") + _SEP + host if host else tr("Your own file") if address else tr("Your own source")
    metadata = layer.metadata()
    if _prepend_head(metadata, head):
        layer.setMetadata(metadata)
    _tooltip_on_older_qgis(layer, head)


def dated_name(layer, value) -> str:

    label = data_date.name_label(value)
    name = str(layer.name() or "")
    if not label or not name.strip() or all(year in name for year in label.split("-")):
        return ""
    renamed = f"{name} ({label})"
    layer.setName(renamed)
    return renamed


def literal_label_text(text) -> str:








    text = str(text or "")
    text = text.replace("[%", "[% '[%' %]")
    return text.replace("$CURRENT_DATE", "$[% 'CURRENT_DATE' %]")


def source_of(layer) -> tuple[str, str]:








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
    except Exception:  # noqa: BLE001
        return "", ""


def _entry_for(result, layer_id: str, name: str) -> dict | None:

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


def _address_of(holders, url: str) -> str:






    for holder in holders:
        for key in ("url", "source"):
            value = str(holder.get(key) or "").strip()
            if value.startswith(("http://", "https://")):
                return value
    return url


def _file_metadata(layer) -> None:







    try:
        import os

        from qgis.core import QgsProviderRegistry

        metadata = layer.metadata()
        if list(metadata.history()) or list(metadata.licenses()):
            return
        path = str((QgsProviderRegistry.instance().decodeUri(layer.providerType(), layer.source()) or {})
                   .get("path") or "")
        if path and os.path.isfile(path) and os.path.isfile(layer.metadataUri()):
            layer.loadDefaultMetadata()
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Metadata beside the layer's file not read: {exc}")


def credit_added(layers, result, tool: str = "", args=None) -> None:












    for layer in layers or ():
        if layer is None:
            continue
        try:
            _file_metadata(layer)
            layer_id = layer.id()
            name = layer.name()
            entry = _entry_for(result, layer_id, name)
            holders = [holder for holder in (entry, result) if isinstance(holder, dict)]
            licence = ""
            attribution = ""
            if entry is not None:
                licence = str(entry.get("licence") or entry.get("license") or "").strip()
                attribution = str(entry.get("attribution") or "").strip()
                if entry is not result and not licence and not attribution:
                    licence = str(result.get("licence") or result.get("license") or "").strip()
                    attribution = str(result.get("attribution") or "").strip()


            url, sublayer = source_of(layer)
            if not url.startswith(("http://", "https://")):




                url = _address_of([*holders, args] if isinstance(args, dict) else holders, url)
                sublayer = next((str(h["typename"]) for h in holders if h.get("typename")), sublayer)
            row = catalog.source_licence(url, sublayer) if url else None
            for key, prefix in (("collection", "stac:"), ("theme", "overture:")):
                if row is not None:
                    break
                value = next((str(h.get(key)).strip() for h in holders if h.get(key)), "")
                if value:
                    row = (catalog.source_licence(prefix + value) if prefix == "stac:"
                           else catalog.theme_licence(prefix + value))
            if row is not None:
                row_licence = str(row.get("licence") or "").strip()
                if not licence and not attribution:
                    licence = row_licence
                    attribution = str(row.get("attribution") or "").strip()
                elif (not licence or licence == UNKNOWN) and row_licence and row_licence != UNKNOWN:



                    licence = row_licence
                    attribution = attribution or str(row.get("attribution") or "").strip()
            credit = credit_layer(layer, licence, attribution)
            failed = not isinstance(result, dict) or "_error" in result
            if entry is not None and not failed:
                entry.setdefault("licence", credit["licence"])
                if credit.get("attribution"):
                    entry.setdefault("attribution", credit["attribution"])
            if failed and result is not None:
                continue




            own = holders if entry is not None else []
            _sheet_and_name(layer, name, row, own, credit, tool, _address_of(holders, url))
            write_origin(layer, tool, args)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Licence and attribution not written on a layer: {exc}")


def _sheet_and_name(layer, name: str, row, holders: list, credit: dict, tool: str, address: str) -> None:

    try:
        row = row or {}
        loaded = data_date.clean(next((h.get("data_date") for h in holders if h.get("data_date")), ""))
        facts = {"title": str(row.get("title") or ""), "data_date": loaded or str(row.get("data_date") or ""),
                 "page_url": str(row.get("page_url") or "")}
        if any(facts.values()):
            write_source_sheet(layer, facts, credit["licence"], credit.get("attribution", ""))
            renamed = dated_name(layer, facts["data_date"])
            if renamed:

                for holder in holders:
                    for key in ("layer_name", "name", "layer"):
                        if holder.get(key) == name:
                            holder[key] = renamed
            if holders:
                holders[0].setdefault("data_date", facts["data_date"] or UNKNOWN)
        elif tool in LOADER_TOOLS and not row and credit["licence"] == UNKNOWN and not credit.get("attribution"):
            write_own_source(layer, address)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Source sheet not written on a layer: {exc}")




_ORIGIN_SKIP = frozenset({"code", "prompt", "quote"})
_ORIGIN_MAX = 1500


ORIGIN_PROPERTY = "terralab/origin_written"


def _origin_value(value, names: dict):

    if isinstance(value, str):
        return names.get(value) or plain_source(value)[:200]
    if isinstance(value, dict):
        return {str(k): _origin_value(v, names) for k, v in list(value.items())[:40] if str(k) not in _ORIGIN_SKIP}
    if isinstance(value, (list, tuple)):
        return [_origin_value(v, names) for v in list(value)[:40]]
    return value


def _layer_parameter(key: str, tool: str) -> bool:







    from ..tools._layers import LAYER_LIST_KEYS, OUTPUT_LAYER_KEYS, PINNED_LAYER_KEYS

    key = str(key)
    if key in OUTPUT_LAYER_KEYS.get(tool, ()):
        return False
    return (key in PINNED_LAYER_KEYS or key in LAYER_LIST_KEYS
            or key.endswith(("_layer", "_layers")))


def _inputs_of(args, tool: str, names: dict) -> list:

    known = set(names.values())
    found: list = []
    for key, value in (args or {}).items() if isinstance(args, dict) else ():
        if not _layer_parameter(key, tool):
            continue
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, str):
                continue
            label = names.get(item) or (item if item in known else "")
            if label and label not in found:
                found.append(label)
    return found


def write_origin(layer, tool: str = "", args=None) -> None:









    try:
        import json
        from datetime import datetime, timezone

        from qgis.core import QgsProject

        if layer.customProperty(ORIGIN_PROPERTY, False):


            return
        metadata = layer.metadata()
        own_id = layer.id()
        names = {lid: str(other.name()) for lid, other in QgsProject.instance().mapLayers().items() if lid != own_id}
        clean = _origin_value(dict(args or {}), names)
        parts = [tr("{day} UTC, added by AI Agent by TerraLab").format(
            day=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))]
        if tool:
            parts.append(tr("tool: {tool}").format(tool=tool))
        if clean:
            parts.append(tr("parameters: {params}").format(
                params=json.dumps(clean, ensure_ascii=False, default=str)[:_ORIGIN_MAX]))
        inputs = _inputs_of(args, tool, names)
        if inputs:
            parts.append(tr("inputs: {names}").format(names=", ".join(inputs)))

        source = "" if layer.providerType() == "memory" else plain_source(layer.source())
        if source:
            parts.append(tr("source: {source}").format(source=source[:500]))
        extent = layer.extent()
        if not extent.isNull():
            parts.append(tr("extent: {box} ({crs})").format(box=extent.toString(4), crs=layer.crs().authid()))
        metadata.addHistoryItem(_SEP.join(parts))
        layer.setMetadata(metadata)
        layer.setCustomProperty(ORIGIN_PROPERTY, True)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Origin not written on a layer: {exc}")

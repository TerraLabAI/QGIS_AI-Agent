# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import re
from urllib.parse import urlsplit

from qgis.PyQt.QtCore import QCoreApplication

from . import browser_connections, catalog, data_date
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
    if kind == data_date.SINCE:
        return tr("Since {year}, live").format(year=first.year)
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








    from qgis.core import QgsDateTimeRange
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

        changed = _add_contact(metadata, provider, "originator") or changed
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
        changed = _add_link(metadata, tr("Dataset page and licence terms"), page_url) or changed
    shown_licence = licence if licence and licence != UNKNOWN else ""
    when = date_text(facts.get("data_date"))
    note = str(facts.get("date_note") or "").strip()
    if note:

        when = f"{when} ({note})"
    head = _SEP.join(part for part in (provider, title, when, shown_licence) if part)
    changed = _prepend_head(metadata, head) or changed
    if changed:
        layer.setMetadata(metadata)
    _tooltip_on_older_qgis(layer, head)


def where_from(address: str) -> str:

    if address.startswith(("http://", "https://")):
        host = urlsplit(address).hostname or ""
        return host[4:] if host.startswith("www.") else host
    return plain_source(address).split("|", 1)[0] if address else ""


def write_unverified(layer, address: str) -> None:

    place = where_from(address)
    head = tr("Source not verified") + (_SEP + place if place else "")
    metadata = layer.metadata()
    if _prepend_head(metadata, head):
        layer.setMetadata(metadata)
    _tooltip_on_older_qgis(layer, head)


PROCESSOR = "TerraLab AI Agent"


_MODIFIED = (("Copernicus Sentinel data", "Contains modified Copernicus Sentinel data"),)


def modified_rights(rights) -> list:

    out = []
    for text in rights or ():
        text = str(text or "").strip()
        for plain, modified in _MODIFIED:
            if text.startswith(plain) and "modified" not in text.lower():
                text = modified + text[len(plain):]
        if text and text not in out:
            out.append(text)
    return out


def _qdatetime(day):
    from qgis.PyQt.QtCore import QDate, QDateTime, QTime

    return QDateTime(QDate(day.year, day.month, day.day), QTime(0, 0, 0))


def _set_date(metadata, kind: str, day) -> bool:

    try:
        from qgis.core import Qgis

        types = getattr(Qgis, "MetadataDateType", None)
        if types is None or not hasattr(metadata, "setDateTime"):
            return False
        which = getattr(types, kind)
        current = metadata.dateTime(which)
        if current is not None and current.isValid():
            return False
        metadata.setDateTime(which, _qdatetime(day))
        return True
    except Exception:  # noqa: BLE001  # nosec B110
        return False


def _add_contact(metadata, organisation: str, role: str) -> bool:
    from qgis.core import QgsAbstractMetadataBase

    contacts = list(metadata.contacts() or [])
    if not organisation or any(str(c.organization or "") == organisation and str(c.role or "") == role
                               for c in contacts):
        return False
    contact = QgsAbstractMetadataBase.Contact()
    contact.organization = organisation
    contact.role = role
    metadata.setContacts(contacts + [contact])
    return True


def _add_link(metadata, name: str, url: str) -> bool:
    from qgis.core import QgsAbstractMetadataBase

    links = list(metadata.links() or [])
    if not url or any(str(link.url or "") == url for link in links):
        return False
    link = QgsAbstractMetadataBase.Link()
    link.name = name
    link.type = "WWW:LINK"
    link.url = url
    metadata.setLinks(links + [link])
    return True


def write_record(layer, data_date_value: str, address: str, derived: bool) -> None:








    from datetime import date

    metadata = layer.metadata()
    changed = False
    if not str(metadata.identifier() or "").strip():
        metadata.setIdentifier(layer.id())
        changed = True
    if not str(metadata.type() or "").strip():
        metadata.setType("dataset")
        changed = True
    web = address.startswith(("http://", "https://"))
    host = where_from(address) if web else ""
    if host:
        changed = _add_contact(metadata, host, "distributor") or changed
    if derived:
        changed = _add_contact(metadata, PROCESSOR, "processor") or changed
    parsed = data_date.parse(data_date_value)
    if parsed is not None and parsed[0] not in (data_date.LIVE, data_date.SINCE):
        changed = _set_date(metadata, "Published", parsed[1]) or changed
    elif parsed is not None:
        changed = _set_date(metadata, "Revised", date.today()) or changed
    if host:
        line = tr("Accessed {day} via {host}").format(day=date.today().isoformat(), host=host)
        if line not in list(metadata.history() or []):
            metadata.addHistoryItem(line)
            changed = True
        changed = _add_link(metadata, tr("Original address"), plain_source(address)) or changed
    if changed:
        layer.setMetadata(metadata)


def parent_facts(layer) -> dict:






    metadata = layer.metadata()
    title = str(metadata.title() or "").strip() or str(layer.name() or "")
    rights = [str(text).strip() for text in metadata.rights() if str(text).strip()]
    attribution = layer_attribution(layer)
    if attribution and attribution not in rights:
        rights.append(attribution)
    provider = provider_of(rights[0] if rights else "")
    extent = ()
    temporal = list(metadata.extent().temporalExtents() or [])
    if temporal and temporal[0].begin().isValid():
        extent = (temporal[0].begin().date().toString("yyyy-MM-dd"), temporal[0].end().date().toString("yyyy-MM-dd"))
    year = data_date.name_label(data_date.from_range(*extent)) if extent else ""
    inside = ", ".join(part for part in (provider, year) if part)
    return {"id": str(metadata.identifier() or "").strip() or layer.id(),
            "label": f"{title} ({inside})" if inside else title,
            "licences": [str(text).strip() for text in metadata.licenses() if str(text).strip()],
            "rights": rights,
            "links": [(str(link.name or ""), str(link.url or "")) for link in metadata.links() or []
                      if str(link.url or "").startswith(("http://", "https://"))],
            "extent": extent}


def write_derived(layer, parents: list) -> None:






    try:
        from qgis.core import QgsDateTimeRange
        from qgis.PyQt.QtCore import QDateTime

        if not parents:
            return
        metadata = layer.metadata()
        history = list(metadata.history() or [])
        for parent in parents:
            line = tr("Derived from: {label}").format(label=parent["label"])
            if line not in history:
                metadata.addHistoryItem(line)
        if not str(metadata.parentIdentifier() or "").strip():
            metadata.setParentIdentifier(parents[0]["id"])
        for parent in parents:
            for name, url in parent.get("links") or ():
                _add_link(metadata, name, url)
        extent = metadata.extent()
        dated = next((parent["extent"] for parent in parents if parent.get("extent")), ())
        if dated and not extent.temporalExtents():
            stamp = "yyyy-MM-ddTHH:mm:ss"
            extent.setTemporalExtents([QgsDateTimeRange(QDateTime.fromString(dated[0] + "T00:00:00", stamp),
                                                        QDateTime.fromString(dated[1] + "T23:59:59", stamp))])
            metadata.setExtent(extent)
        _add_contact(metadata, PROCESSOR, "processor")
        layer.setMetadata(metadata)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Lineage not written on a layer: {exc}")


def dated_name(layer, value) -> str:

    label = data_date.name_label(value)
    name = str(layer.name() or "")
    if not label or not name.strip() or all(year in name for year in label.split("-")):
        return ""
    renamed = f"{name} ({label})"
    layer.setName(renamed)
    return renamed


def credit_line(layer) -> str:






    metadata = layer.metadata()
    text = layer_attribution(layer) or next(
        (str(entry).strip() for entry in metadata.rights() if str(entry).strip()), "")
    if not text:
        return ""
    temporal = list(metadata.extent().temporalExtents() or [])
    year = ""
    if temporal and temporal[0].begin().isValid():
        year = data_date.name_label(data_date.from_range(temporal[0].begin().date().toString("yyyy-MM-dd"),
                                                         temporal[0].end().date().toString("yyyy-MM-dd")))
    terms = next((str(entry).strip() for entry in metadata.licenses()
                  if str(entry).strip() and not str(entry).strip().startswith(("http://", "https://"))), "")
    line = text.rstrip(" .;")
    if year and year not in line:
        line += f" ({year})"
    if terms and terms.lower() not in line.lower():

        short = re.split(r"[(:;]", terms, maxsplit=1)[0].strip(" ,.") or terms
        line += f", {short[:80]}"
    return line


def _record_parts(layer) -> dict:

    metadata = layer.metadata()
    abstract = str(metadata.abstract() or "")
    licences = [str(text).strip() for text in metadata.licenses()
                if str(text).strip() and not str(text).strip().startswith(("http://", "https://"))]
    credit = layer_attribution(layer) or next((str(t).strip() for t in metadata.rights() if str(t).strip()), "")
    producer = next((str(c.organization or "").strip() for c in metadata.contacts() or []
                     if str(c.role or "") == "originator" and str(c.organization or "").strip()), "")
    temporal = list(metadata.extent().temporalExtents() or [])
    when = ""
    if temporal and temporal[0].begin().isValid():
        when = data_date.from_range(temporal[0].begin().date().toString("yyyy-MM-dd"),
                                    temporal[0].end().date().toString("yyyy-MM-dd"))
    return {"metadata": metadata, "abstract": abstract, "licence": licences[0] if licences else "",
            "credit": credit, "provider": producer or provider_of(credit), "when": when}


def source_line(layer) -> str:

    parts = _record_parts(layer)
    head = parts["abstract"].split("\n", 1)[0].strip()
    if head.startswith(tr("Source not verified")):
        return head
    if not (parts["provider"] or parts["licence"]):
        return ""

    live = tr("Live, loaded {day}").split("{", 1)[0]
    since = tr("Since {year}, live").split("{", 1)[0]
    pieces = head.split(_SEP)
    year = next((tr("Live") if piece.startswith(live) else piece for piece in pieces
                 if (live and piece.startswith(live)) or (since and piece.startswith(since))), "")
    year = year or data_date.name_label(parts["when"])
    return _SEP.join(part for part in (parts["provider"], year or tr("Date unknown"), parts["licence"]) if part)


def source_details(layer) -> dict:

    parts = _record_parts(layer)
    metadata = parts["metadata"]
    history = [str(line) for line in metadata.history() or [] if str(line).strip()]
    derived_mark = tr("Derived from: {label}").split("{", 1)[0]
    accessed_mark = tr("Accessed {day} via {host}").split("{", 1)[0]
    parents = [line[len(derived_mark):] for line in history if line.startswith(derived_mark)]
    loaded = next(
        (line[len(accessed_mark):].split(" ", 1)[0] for line in history if line.startswith(accessed_mark)), "")
    if not loaded:
        loaded = next((line[:10] for line in history if re.match(r"^\d{4}-\d{2}-\d{2}", line)), "")
    steps = [line for line in history if not line.startswith((derived_mark, accessed_mark))][-5:]
    page = next((str(link.url or "") for link in metadata.links() or []
                 if str(link.url or "").startswith(("http://", "https://"))
                 and str(link.name or "") != tr("Original address")), "")
    if not page:
        page = next((str(link.url or "") for link in metadata.links() or []
                     if str(link.url or "").startswith(("http://", "https://"))), "")
    return {"title": str(metadata.title() or "").strip(), "licence": parts["licence"], "credit": parts["credit"],
            "loaded": loaded, "steps": steps, "derived_from": parents, "page_url": page}


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
    if isinstance(result.get("layer"), dict):

        candidates.append(result["layer"])
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


def _fallback_row(facts) -> dict | None:






    if not isinstance(facts, dict):
        return None
    row = {"licence": str(facts.get("licence") or facts.get("license") or "").strip()[:300],
           "attribution": str(facts.get("attribution") or "").strip()[:500],
           **catalog._row_extras(facts)}  # noqa: SLF001
    return row if any(row.values()) else None


def _code_names(code) -> set:

    import ast

    try:
        tree = ast.parse(str(code or ""))
    except (SyntaxError, ValueError):
        return set()
    return {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def _project_layers(skip) -> tuple[dict, dict]:
    from qgis.core import QgsProject

    layers = {lid: other for lid, other in QgsProject.instance().mapLayers().items() if lid not in skip}
    by_name = {}
    for other in layers.values():
        by_name.setdefault(str(other.name()), other)
    return layers, by_name


def code_layers(args, skip) -> list:






    if not isinstance(args, dict) or not args.get("code"):
        return []
    layers, by_name = _project_layers(skip)
    names = []
    for item in sorted(_code_names(args["code"])):
        other = layers.get(item) or by_name.get(item)
        if other is not None and str(other.name()) not in names:
            names.append(str(other.name()))
    return names[:20]


def parents_of(args, tool: str, skip) -> list:


    if not isinstance(args, dict):
        return []
    layers, by_name = _project_layers(skip)
    wanted = []
    for key, value in args.items():
        if key != "code" and _layer_parameter(key, tool):
            wanted.extend(item for item in (value if isinstance(value, list) else [value]) if isinstance(item, str))
    found = []
    for item in wanted:
        other = layers.get(item) or by_name.get(item)
        if other is not None and other not in found:
            found.append(other)
    return found[:20]


def credit_added(layers, result, tool: str = "", args=None, facts=None) -> None:
















    added_ids = {layer.id() for layer in layers or () if layer is not None}
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
            if row is None and entry is not None:
                row = _fallback_row(facts) or next(
                    (found for found in (_fallback_row(h.get("source_facts")) for h in holders) if found), None)
            if row is not None:
                row_licence = str(row.get("licence") or "").strip()
                if not licence and not attribution:
                    licence = row_licence
                    attribution = str(row.get("attribution") or "").strip()
                elif (not licence or licence == UNKNOWN) and row_licence and row_licence != UNKNOWN:



                    licence = row_licence
                    attribution = attribution or str(row.get("attribution") or "").strip()
            parents = []
            stamped = bool(layer.customProperty(ORIGIN_PROPERTY, False))
            if not licence and not attribution and row is None and not stamped:


                parents = [parent_facts(other) for other in parents_of(args, tool, added_ids)]
                licence = [text for parent in parents for text in parent["licences"]]
                attribution = modified_rights([text for parent in parents for text in parent["rights"]])
            credit = credit_layer(layer, licence, attribution)
            if row is not None and row.get("licence_url") and credit["licence"] == str(row.get("licence") or ""):
                _licence_page(layer, str(row["licence_url"]))
            failed = not isinstance(result, dict) or "_error" in result
            if entry is not None and not failed:
                entry.setdefault("licence", credit["licence"])
                if credit.get("attribution"):
                    entry.setdefault("attribution", credit["attribution"])
            if failed and result is not None:
                continue




            own = holders if entry is not None else []
            address = _address_of(holders, url)
            derived = bool(parents) or stamped
            dated = _sheet_and_name(layer, name, row, own, credit, tool, address, derived)
            write_record(layer, dated, address, derived)
            write_derived(layer, parents)
            if isinstance(args, dict) and args.get("code") and credit["licence"] == UNKNOWN \
                    and not credit.get("attribution") and not derived:


                _made_by_code(layer, code_layers(args, added_ids))
            write_reprojection(layer)
            if tool and entry is not None:

                browser_connections.save_service(layer, holders, provider_of(credit.get("attribution", "")))
            write_origin(layer, tool, args)
            if not derived:
                note_project_source(layer)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Licence and attribution not written on a layer: {exc}")


def _made_by_code(layer, named: list) -> None:


    try:
        head = tr("Source not verified") + _SEP + tr("made by code")
        metadata = layer.metadata()
        line = tr("Made by code") + (": " + tr("project layers the code names: {names}").format(
            names=", ".join(named)) if named else "")

        changed = (not str(metadata.abstract() or "").startswith(tr("Source not verified"))
                   and _prepend_head(metadata, head))
        if line not in list(metadata.history() or []):
            metadata.addHistoryItem(line)
            changed = True
        if changed:
            layer.setMetadata(metadata)
        _tooltip_on_older_qgis(layer, head)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Code origin not written on a layer: {exc}")


def write_reprojection(layer) -> None:






    try:
        from qgis.core import QgsCoordinateTransform, QgsProject

        metadata = layer.metadata()
        parent_id = str(metadata.parentIdentifier() or "")
        project = QgsProject.instance()
        parent = project.mapLayer(parent_id) if parent_id else None
        if parent is None:
            return
        source, target = parent.crs(), layer.crs()
        if not (source.isValid() and target.isValid()) or source == target:
            return
        transform = QgsCoordinateTransform(source, target, project.transformContext())
        extent = parent.extent()
        if not extent.isEmpty():
            transform.transform(extent.center())
        operation = str(transform.coordinateOperation() or "")
        grids = []
        try:
            details = transform.instantiatedCoordinateOperationDetails()
            operation = operation or str(details.proj or details.name or "")
            grids = [str(grid.shortName or grid.fullName) for grid in details.grids]
        except (AttributeError, RuntimeError):
            pass
        line = tr("Reprojected from {source} to {target}; operation: {operation}").format(
            source=source.authid() or source.description(), target=target.authid() or target.description(),
            operation=operation or tr("QGIS default operation"))
        if grids:
            line += "; " + tr("grids: {grids}").format(grids=", ".join(grids))
        if line not in list(metadata.history() or []):
            metadata.addHistoryItem(line)
            layer.setMetadata(metadata)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Reprojection not written on a layer: {exc}")


_PROJECT_MARK = "Data sources (AI Agent by TerraLab):"
_RUN_LAYERS: list = []


def note_project_source(layer) -> None:





    try:
        from qgis.core import QgsProject

        line = source_line(layer)
        if not line or line.startswith(tr("Source not verified")):
            return
        title = str(layer.metadata().title() or "").strip() or str(layer.name() or "")
        parts = line.split(_SEP)
        text = _SEP.join([parts[0], title, *parts[1:]]) if title and title not in line else line
        _RUN_LAYERS.append(str(layer.name() or ""))
        project = QgsProject.instance()
        metadata = project.metadata()
        abstract = str(metadata.abstract() or "")
        own, mark, block = abstract.partition(_PROJECT_MARK)
        lines = [row[2:] for row in block.strip().splitlines() if row.startswith("- ")] if mark else []
        if text in lines:
            return
        lines.append(text)
        summary = _PROJECT_MARK + "\n" + "\n".join(f"- {row}" for row in lines)
        metadata.setAbstract(f"{own.rstrip()}\n\n{summary}" if own.strip() else summary)
        project.setMetadata(metadata)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Project sources summary not written: {exc}")


def note_project_run(run_id: str) -> None:

    try:
        from datetime import datetime, timezone

        from qgis.core import QgsProject

        if not _RUN_LAYERS:
            return
        names = list(dict.fromkeys(_RUN_LAYERS))
        _RUN_LAYERS.clear()
        project = QgsProject.instance()
        metadata = project.metadata()
        metadata.addHistoryItem(tr("{day} UTC, AI Agent run {run}: added {layers}").format(
            day=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), run=str(run_id)[:8],
            layers=", ".join(names[:20]) + (" ..." if len(names) > 20 else "")))
        project.setMetadata(metadata)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Project history not written: {exc}")


def _licence_page(layer, url: str) -> None:

    metadata = layer.metadata()
    licences = [str(text) for text in metadata.licenses()]
    if url not in licences:
        metadata.setLicenses(licences + [url])
        layer.setMetadata(metadata)


def _loaded_date(holders: list) -> str:

    loaded = data_date.clean(next((h.get("data_date") for h in holders if h.get("data_date")), ""))
    if not loaded:
        release = next((str(h.get("release")) for h in holders if h.get("release")), "")
        loaded = data_date.from_range(release[:10]) if release else ""
    return loaded


def _sheet_and_name(layer, name: str, row, holders: list, credit: dict, tool: str, address: str,
                    derived: bool = False) -> str:

    try:
        row = row or {}
        loaded = _loaded_date(holders)
        facts = {"title": str(row.get("title") or ""), "data_date": loaded or str(row.get("data_date") or ""),
                 "page_url": str(row.get("page_url") or ""),
                 "date_note": "" if loaded else str(row.get("date_note") or "")}
        known = credit["licence"] != UNKNOWN or bool(credit.get("attribution"))
        if any(facts.values()) or (known and not derived):
            write_source_sheet(layer, facts, credit["licence"], credit.get("attribution", ""))
            renamed = dated_name(layer, facts["data_date"])
            if renamed:

                for holder in holders:
                    for key in ("layer_name", "name", "layer"):
                        if holder.get(key) == name:
                            holder[key] = renamed
            if holders:
                holders[0].setdefault("data_date", facts["data_date"] or UNKNOWN)
        elif not known and not row and not derived and (address or tool in LOADER_TOOLS):
            write_unverified(layer, address)
            if holders:
                holders[0]["source_verified"] = False
        return facts["data_date"]
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Source sheet not written on a layer: {exc}")
        return ""




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

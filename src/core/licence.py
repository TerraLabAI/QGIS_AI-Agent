# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Licence, attribution and the source sheet written on every layer a tool just added."""












from __future__ import annotations

import re
from urllib.parse import urlsplit

from qgis.PyQt.QtCore import QCoreApplication

from . import catalog, data_date
from .logger import log_warning

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


def _locale_date(first, pattern: str) -> str:
    """``first`` written by the user's locale (``QLocale``), ISO when Qt cannot."""
    try:
        from qgis.PyQt.QtCore import QDate, QLocale

        text = QLocale().toString(QDate(first.year, first.month, first.day), pattern)
        if isinstance(text, str) and text:
            return text
    except Exception:  # noqa: BLE001  # nosec B110 - the ISO form below still says the date
        pass
    return first.isoformat() if pattern != "MMMM yyyy" else first.isoformat()[:7]


def date_text(value) -> str:
    """The data date as a reader sees it: 2021, 2017-2023, August 2026, 19 August 2026, or Date unknown."""
    parsed = data_date.parse(value)
    if parsed is None:
        return tr("Date unknown")
    kind, first, last = parsed
    if kind == data_date.LIVE:
        return tr("Live, loaded {day}").format(day=_locale_date(first, "d MMMM yyyy"))
    if kind == "year":
        return str(first.year)
    if kind == "span":
        return f"{first.year}-{last.year}"
    if kind == "month":
        return _locale_date(first, "MMMM yyyy")
    return _locale_date(first, "d MMMM yyyy")


def provider_of(attribution: str, page_url: str = "") -> str:
    """The publisher a reader knows: the first credit of the attribution, else the dataset page's host."""
    first = re.split(r"[,;|]", str(attribution or ""), maxsplit=1)[0]
    first = _CREDIT_PREFIX.sub("", first).strip(" .\u00a9")
    if first and len(first) <= 60:
        return first
    host = (urlsplit(page_url).hostname or "") if page_url else ""
    return host[4:] if host.startswith("www.") else host


def _prepend_head(metadata, head: str) -> bool:
    """Put ``head`` as the abstract's first line, once. True when the abstract changed."""
    abstract = str(metadata.abstract() or "")
    if not head or abstract.startswith(head):
        return False
    metadata.setAbstract(f"{head}\n\n{abstract.strip()}" if abstract.strip() else head)
    return True


def _tooltip_on_older_qgis(layer, head: str) -> None:
    """On 3.28 and 3.34 the tooltip reads the server abstract: give it the head line when it has none."""
    try:
        from qgis.core import Qgis

        if int(Qgis.QGIS_VERSION_INT) >= _TOOLTIP_READS_METADATA:
            return
        properties = layer.serverProperties()
        if not str(properties.abstract() or "").strip():
            properties.setAbstract(head)
    except Exception:  # noqa: BLE001  # nosec B110 - the metadata sheet is written either way
        pass


def write_source_sheet(layer, facts: dict, licence: str, attribution: str) -> None:
    """The dataset's sheet in the layer metadata, main thread only."""







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
            link.name = tr("Dataset page")
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
    """Say on the layer that it is the user's own file or address, with no date. Main thread only."""
    host = ""
    if address.startswith(("http://", "https://")):
        host = urlsplit(address).hostname or ""
    head = tr("Your own source") + _SEP + host if host else tr("Your own file") if address else tr("Your own source")
    metadata = layer.metadata()
    if _prepend_head(metadata, head):
        layer.setMetadata(metadata)
    _tooltip_on_older_qgis(layer, head)


def dated_name(layer, value) -> str:
    """Append the year or span of ``value`` to the layer's name when it lacks it. The new name, or empty."""
    label = data_date.name_label(value)
    name = str(layer.name() or "")
    if not label or not name.strip() or all(year in name for year in label.split("-")):
        return ""
    renamed = f"{name} ({label})"
    layer.setName(renamed)
    return renamed


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


def _address_of(holders, url: str) -> str:
    """The address the user reads the layer as coming from: the loader's http url first."""
    for holder in holders:
        value = str(holder.get("url") or "").strip()
        if value.startswith(("http://", "https://")):
            return value
    return url


def credit_added(layers, result, tool: str = "") -> None:
    """Credit every layer one tool call added, main thread only."""











    for layer in layers or ():
        if layer is None:
            continue
        try:
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



                url = _address_of(holders, url)
            row = catalog.source_licence(url, sublayer) if url else None
            for key, prefix in (("collection", "stac:"), ("theme", "overture:")):
                if row is not None:
                    break
                value = next((str(h.get(key)).strip() for h in holders if h.get(key)), "")
                if value:
                    row = (catalog.source_licence(prefix + value) if prefix == "stac:"
                           else catalog.theme_licence(prefix + value))
            if not licence and not attribution and row is not None:
                licence = str(row.get("licence") or "").strip()
                attribution = str(row.get("attribution") or "").strip()
            credit = credit_layer(layer, licence, attribution)
            failed = not isinstance(result, dict) or "_error" in result
            if entry is not None and not failed:
                entry.setdefault("licence", credit["licence"])
                if credit.get("attribution"):
                    entry.setdefault("attribution", credit["attribution"])
            if failed and result is not None:
                continue
            _sheet_and_name(layer, name, row, holders, credit, tool, _address_of(holders, url))
        except Exception as exc:  # noqa: BLE001 - a layer that is added is added, credited or not
            log_warning(f"Licence and attribution not written on a layer: {exc}")


def _sheet_and_name(layer, name: str, row, holders: list, credit: dict, tool: str, address: str) -> None:
    """The source sheet and the dated name, or the own-source line; logged, never raised."""
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
    except Exception as exc:  # noqa: BLE001 - the credit is written; the sheet is a courtesy on top
        log_warning(f"Source sheet not written on a layer: {exc}")

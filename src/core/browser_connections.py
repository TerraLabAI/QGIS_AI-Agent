# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

from urllib.parse import urlsplit

from .logger import log_warning
from .provenance import plain_source


_ROOTS = {"wms": ("ows/items/wms/connections", "WMS"), "wfs": ("ows/items/wfs/connections", "WFS"),
          "oapif": ("ows/items/wfs/connections", "OGC API Features"), "xyz": ("xyz", "XYZ"),
          "arcgisfeatureserver": ("arcgisfeatureserver", "ArcGIS REST"),
          "arcgismapserver": ("arcgismapserver", "ArcGIS REST")}


def _service_of(layer, holders) -> tuple[str, str]:

    from qgis.core import QgsDataSourceUri, QgsProviderRegistry

    provider = layer.providerType()
    parts = QgsProviderRegistry.instance().decodeUri(provider, layer.source()) or {}
    params = QgsDataSourceUri(layer.source())
    url = str(parts.get("url") or params.param("url") or "").strip()
    if provider == "wms":
        kind = "xyz" if str(parts.get("type") or params.param("type") or "") == "xyz" else "wms"
        if kind == "xyz":


            url = url.replace("%25", "%")
        if kind == "wms" and url:
            url = url.split("?", 1)[0] if "SERVICE=WMTS" not in url.upper() else url
        return kind, url
    if provider in ("wfs", "oapif", "arcgisfeatureserver", "arcgismapserver"):
        return provider, url

    for holder in holders:
        address = str(holder.get("url") or "")
        if address.startswith(("http://", "https://")) and holder.get("typename"):
            return "wfs", address.split("?", 1)[0]
        if address.startswith(("http://", "https://")) and "OGC API" in str(holder.get("provider") or ""):
            return "oapif", address.split("/collections", 1)[0]
    return "", ""




_PLAIN_PARAMS = frozenset({
    "service", "request", "version", "layer", "layers", "style", "styles", "format", "tilematrixset",
    "tilematrix", "tilerow", "tilecol", "crs", "srs", "srsname", "bbox", "width", "height", "transparent",
    "typename", "typenames", "outputformat", "x", "y", "z", "f", "dpi", "map", "lang", "language",
})


def credential_free(url: str) -> bool:




    from urllib.parse import parse_qsl

    parts = urlsplit(url)
    if parts.username or parts.password or parts.fragment or plain_source(url) == "":
        return False
    names = [name.strip().lower() for name, _value in parse_qsl(parts.query, keep_blank_values=True)]
    return all(name in _PLAIN_PARAMS for name in names)


def _saved_urls(settings, root: str) -> set:
    group = f"connections/{root}/items"
    settings.beginGroup(group)
    try:
        names = settings.childGroups()
    finally:
        settings.endGroup()
    return {str(settings.value(f"{group}/{name}/url", "") or "").rstrip("/") for name in names}


def save_service(layer, holders, provider_name: str) -> str:

    try:
        from qgis.core import QgsSettings

        kind, url = _service_of(layer, holders)
        if not kind or not url.startswith(("http://", "https://")):
            return ""
        if not credential_free(url):
            return ""
        root, word = _ROOTS[kind]
        settings = QgsSettings()
        if url.rstrip("/") in _saved_urls(settings, root):
            return ""
        host = urlsplit(url).hostname or ""
        name = f"{provider_name or host} - {word} {host}".strip().replace("/", "-").replace("\\", "-")
        group = f"connections/{root}/items"
        base, n = name, 2
        while settings.contains(f"{group}/{name}/url"):
            name, n = f"{base} ({n})", n + 1
        settings.setValue(f"{group}/{name}/url", url)
        if kind == "xyz":
            settings.setValue(f"{group}/{name}/zmin", 0)
            settings.setValue(f"{group}/{name}/zmax", 19)
        if kind == "oapif":
            settings.setValue(f"{group}/{name}/version", "OGC_API_FEATURES")
        return name
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Browser connection not saved: {exc}")
        return ""

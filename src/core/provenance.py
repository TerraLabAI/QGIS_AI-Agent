# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

import hashlib
import os
import re


_SECRET_NAMES = frozenset({
    "key", "apikey", "api_key", "api-key", "apitoken", "api_token", "token", "access_token", "accesstoken",
    "auth", "authkey", "auth_key", "authtoken", "auth_token", "password", "passwd", "pwd", "pass", "secret",
    "client_secret", "clientsecret", "sig", "signature", "jwt", "session", "sessionid", "session_id",
    "credential", "credentials", "private_token", "subscription-key", "subscription_key", "bearer",
    "x-amz-signature", "x-amz-credential", "x-amz-security-token", "x-goog-signature", "x-goog-credential",
    "username", "user",
})

_SIGNED_PATH_PARAM = ("/eodata/", "t")

_KEY_IN_PATH_HOSTS = frozenset({"wxs.ign.fr"})

_CLOUD_VSI = ("vsis3", "vsigs", "vsiaz", "vsiadls", "vsioss", "vsiswift", "vsihdfs", "vsiwebhdfs")
_URL_VSI = ("vsicurl", "vsicurl_streaming")
_ARCHIVE_EXT = (".zip", ".gz", ".tar", ".tgz", ".7z", ".kmz", ".gpkg.zip")
_VSI = re.compile(r"^/(vsi[a-z0-9_]+)/")


_CLOUD_URL = re.compile(r"^(s3|gs|gcs|az|abfss?|wasbs?|oss|swift)://", re.IGNORECASE)
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")

_DRIVER = re.compile(r'^([A-Za-z][A-Za-z0-9_]+):(?:"([^"]*)"(.*)|(.*))$', re.DOTALL)


_LINEAGE: dict = {}
_LINEAGE_MAX = 500


def _param(name: str) -> str:

    name = str(name or "").strip().lower()
    return name[len("amp;"):] if name.startswith("amp;") else name


def _secret(name: str) -> bool:
    return _param(name) in _SECRET_NAMES


def _signed_token(name: str, holder: str) -> bool:

    return _SIGNED_PATH_PARAM[0] in holder and _param(name) == _SIGNED_PATH_PARAM[1]


def _is_local(text: str) -> bool:

    return bool(_DRIVE.match(text)) or text.startswith(("\\", "/", "~", "./", "../", ".\\", "..\\"))


def _file_name(path: str) -> str:
    parts = [part for part in re.split(r"[\\/]", path) if part]
    return parts[-1] if parts else path


def _url(text: str) -> str:

    from qgis.PyQt.QtCore import QUrl, QUrlQuery

    url = QUrl(text)
    if not url.isValid() or not url.host():
        return text
    url = url.adjusted(QUrl.UrlFormattingOption.RemoveUserInfo)
    if url.host().lower() in _KEY_IN_PATH_HOSTS:
        segments = url.path().split("/")
        if len(segments) > 2:
            segments[1] = "KEY"
            url.setPath("/".join(segments))
    if url.hasQuery():
        query = QUrlQuery(url)
        kept = QUrlQuery()
        for name, value in query.queryItems(QUrl.ComponentFormattingOption.FullyDecoded):
            if not _secret(name) and not _signed_token(name, url.path()):
                kept.addQueryItem(name, plain_source(value) if "://" in value else value)
        url.setQuery(kept)
    return url.toString()


def _vsi(name: str, rest: str) -> str:
    if name in _CLOUD_VSI:
        return f"/{name}/{rest}"
    if name in _URL_VSI:
        return f"/{name}/{_url(rest)}"
    if rest.startswith("/vsi"):
        return f"/{name}/{plain_source(rest)}"
    main, bar, suffix = rest.partition("|")
    parts = [part for part in re.split(r"[\\/]", main) if part]
    for index, part in enumerate(parts):
        if part.lower().endswith(_ARCHIVE_EXT):
            return f"/{name}/" + "/".join(parts[index:]) + bar + suffix
    return f"/{name}/" + _file_name(main) + bar + suffix


def _qgis_source(text: str) -> str:

    if "'" in text or "dbname=" in text or " table=" in text:
        from qgis.core import QgsDataSourceUri

        uri = QgsDataSourceUri(text)
        uri.setUsername("")
        uri.setPassword("")
        if uri.database():
            uri.setDatabase(plain_source(uri.database()))
        for name in list(uri.parameterKeys()):
            values = uri.params(name)
            uri.removeParam(name)
            if not _secret(name):
                for value in values:
                    uri.setParam(name, plain_source(value))
        return uri.uri(False).strip()
    return _plain_query(text)


def _plain_query(text: str, encoded: bool = False) -> str:






    from qgis.PyQt.QtCore import QUrl, QUrlQuery

    items = QUrlQuery(text).queryItems(QUrl.ComponentFormattingOption.FullyDecoded)
    signed = " ".join(value for name, value in items if _param(name) == "url")
    kept = QUrlQuery()
    for name, value in items:
        if _secret(name) or _signed_token(name, signed):
            continue
        if _param(name) == "layer" and ":" in value:

            provider, _colon, rest = value.partition(":")
            pieces = rest.rsplit(":", 2)
            value = ":".join([provider, plain_source(pieces[0]), *pieces[1:]])
        else:
            value = plain_source(value)
        kept.addQueryItem(name, value)
    return kept.toString(QUrl.ComponentFormattingOption.FullyEncoded if encoded
                         else QUrl.ComponentFormattingOption.PrettyDecoded)


def plain_source(text) -> str:









    text = str(text or "")
    if not text.strip():
        return text
    if text.startswith("/vsicurl?"):
        from qgis.PyQt.QtCore import QUrl, QUrlQuery

        query = QUrlQuery(text[len("/vsicurl?"):])
        address = query.queryItemValue("url", QUrl.ComponentFormattingOption.FullyDecoded)
        return "/vsicurl/" + _url(address) if address else "/vsicurl/"
    found = _VSI.match(text)
    if found:
        return _vsi(found.group(1), text[found.end():])
    lowered = text.lower()
    if lowered.startswith(("http://", "https://")) or _CLOUD_URL.match(text):
        return _url(text)
    if lowered.startswith("file:"):
        from qgis.PyQt.QtCore import QUrl

        main, bar, suffix = text.partition("|")
        url = QUrl(main)
        shown = _file_name(url.toLocalFile() or url.path() or main[5:])

        return shown + ("?" + _plain_query(url.query()) if url.hasQuery() else "") + bar + suffix
    if text.startswith("?") and "layer=" in text:

        return "?" + _plain_query(text[1:], encoded=True)
    driver = _DRIVER.match(text)
    if driver:


        name, quoted = driver.group(1), driver.group(2)
        rest = driver.group(3) if quoted is not None else driver.group(4)
        if quoted is not None:
            return f'{name}:"{plain_source(quoted)}"{rest}'
        main, colon, tail = rest.rpartition(":")
        if colon and main and not re.search(r"[\\/]", tail) and _is_local(main):
            return f"{name}:{plain_source(main)}:{tail}"
        if _is_local(rest):
            return f"{name}:{plain_source(rest)}"
    if _is_local(text):
        main, bar, suffix = text.partition("|")
        return _file_name(main) + bar + suffix
    if any(mark in text for mark in ("url=", "password=", "dbname=", "user=", "username=")):
        return _qgis_source(text)
    return text


def _norm(path: str) -> str:




    main, _bar, suffix = str(path).partition("|")
    table = next((part[len("layername="):] for part in suffix.split("|") if part.startswith("layername=")), "")
    key = os.path.normcase(os.path.normpath(main))
    return f"{key}|layername={table}" if table else key


def _same_source(source: str) -> str:

    main, bar, suffix = str(source).partition("|")
    return os.path.normcase(os.path.normpath(main)) + bar + suffix


def _static(value):

    for attribute in ("source", "sink"):
        inner = getattr(value, attribute, None)
        if inner is not None and not callable(inner) and (hasattr(value, "selectedFeaturesOnly")
                                                            or hasattr(value, "destinationProject")):
            value = inner
            break
    if hasattr(value, "staticValue"):
        value = value.staticValue()
    return value


def _project_layer(value):
    from qgis.core import QgsMapLayer, QgsProject

    if isinstance(value, QgsMapLayer):
        return value
    if not isinstance(value, str) or not value:
        return None
    project = QgsProject.instance()
    layer = project.mapLayer(value)
    if layer is not None:
        return layer
    if _is_local(value):
        wanted = _same_source(value)
        return next((other for other in project.mapLayers().values() if _same_source(other.source()) == wanted),
                    None)
    return None


def _walk(value):
    value = _static(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    else:
        yield value


def _credit_of(layer) -> tuple:
    from .licence import layer_attribution

    metadata = layer.metadata()
    licences = [str(text).strip() for text in metadata.licenses() if str(text).strip()]
    rights = [str(text).strip() for text in metadata.rights() if str(text).strip()]
    attribution = layer_attribution(layer)
    if attribution and attribution not in rights:
        rights.append(attribution)
    return licences, rights


def _remembered(value: str):


    found = _LINEAGE.get(_norm(value)) or _LINEAGE.get(_norm(value.split("|", 1)[0]))
    if found is None and "|" not in value and value.lower().endswith(".gpkg") and os.path.isfile(value):
        from osgeo import ogr

        dataset = ogr.Open(value)
        try:
            table = dataset.GetLayer(0).GetName() if dataset is not None and dataset.GetLayerCount() else ""
        finally:
            dataset = None
        if table:
            found = _LINEAGE.get(_norm(f"{value}|layername={table}"))
    return found


def inputs_of(parameters: dict, algorithm=None) -> dict:






    inputs, licences, rights, seen = [], [], [], set()

    def add(label, found_licences, found_rights):
        inputs.append(label)
        licences.extend(item for item in found_licences if item not in licences)
        rights.extend(item for item in found_rights if item not in rights)

    layers = []
    read = {}
    for name, value in (parameters or {}).items():
        definition = algorithm.parameterDefinition(name) if algorithm is not None else None

        if definition is not None and definition.isDestination():
            continue
        read[name] = value
    for value in _walk(read):
        layer = _project_layer(value)
        if layer is not None:
            if layer.id() not in seen:
                seen.add(layer.id())
                layers.append(layer)
            continue
        if not isinstance(value, str) or not value or value in seen:
            continue
        remembered = _remembered(value) if _is_local(value) else None
        if remembered is not None:
            seen.add(value)
            add(remembered["label"], remembered["licences"], remembered["rights"])
        elif _is_local(value) or value.startswith(("/vsi", "http://", "https://")):
            seen.add(value)
            add(plain_source(value), [], [])
    names = [layer.name() for layer in layers]
    for layer in layers:
        label = layer.name()
        if names.count(label) > 1:
            label = f"{label} [{plain_source(layer.source())}]"
        add(label, *_credit_of(layer))
    return {"inputs": inputs, "licences": licences, "rights": rights}


def command_text(algorithm_id: str, parameters: dict) -> str:





    originals: dict = {}
    clashes: set = set()

    def plain(value):
        value = _static(value)
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        if isinstance(value, dict):
            return {str(key): plain(item) for key, item in value.items()}
        if value is None or isinstance(value, (bool, int, float)):
            return value
        layer = _project_layer(value)
        if layer is not None:
            return layer.name()
        text = str(value)
        shown = plain_source(text)
        originals.setdefault(shown, set()).add(text)
        if shown in clashes:
            shown += " #" + hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
        return shown

    shown = plain(dict(parameters or {}))
    clashes.update(name for name, sources in originals.items() if len(sources) > 1)
    if clashes:
        shown = plain(dict(parameters or {}))
    return f'processing.run("{algorithm_id}", {shown!r})'


def _table_exists(path: str, table: str) -> bool:
    from osgeo import ogr

    dataset = ogr.Open(path)
    try:
        return dataset is not None and dataset.GetLayerByName(table) is not None
    finally:
        dataset = None


def existing_files(parameters: dict) -> set:






    found: set = set()
    for value in _walk(dict(parameters or {})):
        layer = _project_layer(value)
        text = str(layer.source()) if layer is not None else value
        if not isinstance(text, str) or not text or not _is_local(text):
            continue
        main, _bar, suffix = text.partition("|")
        if not os.path.isfile(main):
            continue
        found.add(_norm(main))
        table = next((part[len("layername="):] for part in suffix.split("|") if part.startswith("layername=")), "")
        try:
            if table and _table_exists(main, table):
                found.add(_norm(text))
        except Exception:  # noqa: BLE001
            found.add(_norm(text))
    return found


def was_there(path: str, table: str, existed) -> bool:

    return _norm(f"{path}|layername={table}" if table else path) in (existed or ())


def remember_output(value, provenance: dict) -> None:

    if not isinstance(value, str) or not _is_local(value) or not provenance:
        return
    inputs = ", ".join(provenance.get("inputs") or [])
    label = f"{plain_source(value)} (from {provenance.get('algorithm') or 'Processing'}"
    label += f" of {inputs})" if inputs else ")"
    if len(_LINEAGE) >= _LINEAGE_MAX:
        _LINEAGE.pop(next(iter(_LINEAGE)))
    _LINEAGE[_norm(value)] = {"label": label, "licences": list(provenance.get("licences") or []),
                              "rights": list(provenance.get("rights") or [])}

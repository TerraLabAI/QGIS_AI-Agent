# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import re


_SECRET_NAMES = frozenset({
    "key", "apikey", "api_key", "api-key", "apitoken", "api_token", "token", "access_token", "accesstoken",
    "auth", "authkey", "auth_key", "authtoken", "auth_token", "password", "passwd", "pwd", "pass", "secret",
    "client_secret", "clientsecret", "sig", "signature", "jwt", "session", "sessionid", "session_id",
    "credential", "credentials", "private_token", "subscription-key", "subscription_key", "bearer",
    "x-amz-signature", "x-amz-credential", "x-amz-security-token", "x-goog-signature", "x-goog-credential",
    "username", "user",

    "tk", "code", "access-token", "api", "gkt", "x-api-key", "api-token", "jwt_token", "ticket",
})

_SECRET_PARTS = ("key", "token", "secret", "passw", "auth", "sig", "ticket", "jwt", "credential", "session")

_SIGNED_PATH_PARAM = ("/eodata/", "t")

_KEY_IN_PATH_HOSTS = frozenset({"wxs.ign.fr"})

_CLOUD_VSI = ("vsis3", "vsigs", "vsiaz", "vsiadls", "vsioss", "vsiswift", "vsihdfs", "vsiwebhdfs")
_URL_VSI = ("vsicurl", "vsicurl_streaming")
_ARCHIVE_EXT = (".zip", ".gz", ".tar", ".tgz", ".7z", ".kmz", ".gpkg.zip")
_VSI = re.compile(r"^/(vsi[a-z0-9_]+)/")


_CLOUD_URL = re.compile(r"^(s3|gs|gcs|az|abfss?|wasbs?|oss|swift)://", re.IGNORECASE)
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")

_DRIVER = re.compile(r'^([A-Za-z][A-Za-z0-9_]+):(?:"([^"]*)"(.*)|(.*))$', re.DOTALL)


def _param(name: str) -> str:

    name = str(name or "").strip().lower()
    return name[len("amp;"):] if name.startswith("amp;") else name


def _secret(name: str) -> bool:
    name = _param(name)
    return name in _SECRET_NAMES or any(part in name for part in _SECRET_PARTS)


def _signed_token(name: str, holder: str) -> bool:

    return _SIGNED_PATH_PARAM[0] in holder and _param(name) == _SIGNED_PATH_PARAM[1]


def is_local(text: str) -> bool:

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

    url.setFragment(None)

    return url.toString().replace("%7B", "{").replace("%7D", "}")


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
        if quoted is None and rest.lower().startswith(("http://", "https://")):

            return f"{name}:{_url(rest)}"
        if quoted is not None:
            return f'{name}:"{plain_source(quoted)}"{rest}'
        main, colon, tail = rest.rpartition(":")
        if colon and main and not re.search(r"[\\/]", tail) and is_local(main):
            return f"{name}:{plain_source(main)}:{tail}"
        if is_local(rest):
            return f"{name}:{plain_source(rest)}"
    if is_local(text):
        main, bar, suffix = text.partition("|")
        return _file_name(main) + bar + suffix
    if any(mark in text for mark in ("url=", "password=", "dbname=", "user=", "username=")):
        return _qgis_source(text)
    return text

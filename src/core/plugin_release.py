# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import json

from qgis.core import Qgis, QgsNetworkAccessManager
from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from .versions import read_version

RELEASES_URL = "https://plugins.qgis.org/plugins/AI_Agent/json"


_PUT_OFF_PROPERTY = "terralabAiAgentUpdateLater"


def put_off_version() -> str:

    app = QCoreApplication.instance()
    value = app.property(_PUT_OFF_PROPERTY) if app is not None else None
    return str(value or "")


def put_off_update(version: str) -> None:

    app = QCoreApplication.instance()
    if app is not None and version:
        app.setProperty(_PUT_OFF_PROPERTY, str(version))


def qgis_version() -> str:

    number = int(Qgis.QGIS_VERSION_INT)
    return f"{number // 10000}.{number // 100 % 100}.{number % 100}"


def newest_release(doc: object, running_qgis: str = "") -> str:

    if not isinstance(doc, dict) or not isinstance(doc.get("versions"), list):
        return ""
    here = read_version(running_qgis)
    best, best_key = "", None
    for entry in doc["versions"]:
        if not isinstance(entry, dict) or entry.get("experimental") is not False:
            continue
        key = read_version(entry.get("version"))
        if key is None:
            continue
        low, high = read_version(entry.get("qgis_min")), read_version(entry.get("qgis_max"))
        if here is not None and ((low is not None and here < low) or (high is not None and here > high)):
            continue
        if best_key is None or key > best_key:
            best, best_key = str(entry.get("version")).strip(), key
    return best


def read_released(on_done) -> QNetworkReply | None:





    request = QNetworkRequest(QUrl(RELEASES_URL))
    request.setRawHeader(b"Accept", b"application/json")
    reply = QgsNetworkAccessManager.instance().get(request)

    def finished() -> None:
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                on_done("", reply.errorString() or "network error")
                return
            try:
                doc = json.loads(bytes(reply.readAll()).decode("utf-8", "replace"))
            except ValueError:
                on_done("", "unreadable plugin page")
                return
            version = newest_release(doc, qgis_version())
            on_done(version, "" if version else "no stable version listed")
        finally:
            reply.deleteLater()

    reply.finished.connect(finished)
    return reply

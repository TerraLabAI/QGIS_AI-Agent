# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import hashlib
import os
import tempfile

from qgis.PyQt.QtGui import QImage
from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

from ..core.host_platform import retry_file_op
from ..core.policy import PLUGIN_CACHE_DIR
from ..core.qt_compat import disconnect_quietly
from .image_guard import read_bounded_image
from .shared import resolve_qt_enum




MAX_THUMBNAIL_BYTES = 900 * 1024

THUMBNAIL_CACHE_DIR = os.path.join(PLUGIN_CACHE_DIR, "thumbnails")

NETWORK_NO_ERROR = resolve_qt_enum(QNetworkReply, "NetworkError", "NoError")
HTTP_STATUS = resolve_qt_enum(QNetworkRequest, "Attribute", "HttpStatusCodeAttribute")
REDIRECT_POLICY = resolve_qt_enum(QNetworkRequest, "Attribute", "RedirectPolicyAttribute")
NO_LESS_SAFE = resolve_qt_enum(QNetworkRequest, "RedirectPolicy", "NoLessSafeRedirectPolicy")


def thumbnail_cache_path(url: str) -> str:

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    return os.path.join(THUMBNAIL_CACHE_DIR, f"{digest}.img")


def is_picture_url_usable(url: object) -> bool:


    if not isinstance(url, str) or not url.lower().startswith("https://"):
        return False
    try:
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        return bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def drop_reply(reply) -> None:

    if reply is None:
        return
    for signal in ("finished", "downloadProgress"):
        disconnect_quietly(reply, signal)
    try:
        reply.abort()
        reply.deleteLater()
    except RuntimeError:
        pass


def cached_thumbnail(url: str) -> QImage | None:
    try:
        if os.path.getsize(thumbnail_cache_path(url)) > MAX_THUMBNAIL_BYTES:
            return None
        with open(thumbnail_cache_path(url), "rb") as handle:
            data = handle.read(MAX_THUMBNAIL_BYTES)
    except OSError:
        return None
    return read_bounded_image(data, 16 * 1024 * 1024)


def store_thumbnail(url: str, data: bytes) -> None:
    path = thumbnail_cache_path(url)
    part = ""
    try:
        os.makedirs(THUMBNAIL_CACHE_DIR, exist_ok=True)




        handle_fd, part = tempfile.mkstemp(prefix=".thumb-", dir=THUMBNAIL_CACHE_DIR)
        with os.fdopen(handle_fd, "wb") as handle:
            handle.write(data)



        retry_file_op(os.replace, part, path)
    except OSError:
        if part:
            try:
                os.remove(part)
            except OSError:
                pass

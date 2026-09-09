# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One line from the server, above the composer, until the user closes it."""













from __future__ import annotations

import contextlib
import html
import json

from qgis.core import QgsSettings
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .external_links import open_external_url
from .font_scale import scale_qss_font_px
from .style import (
    _BTN_CHIP_CLOSE,
    ACCENT_BORDER_SOFT,
    ACCENT_INK,
    ACCENT_TINT,
    FONT_BODY,
    INK,
    ORANGE,
    ORANGE_TINT,
    RADIUS_CARD,
    SPACE_CARD,
)
from .widgets import IconButton



_SETTINGS_KEY = "TerraLab/AIAgent/dismissed_notices"
_DISMISSED_CAP = 50

LEVELS = ("info", "warning")
_MAX_TEXT_CHARS = 280



_MAX_NOTICE_LINES = 4

_INK_ON_ORANGE = ORANGE
_BAR_QSS = {
    "info": (
        f"QWidget#noticeBar {{ background: {ACCENT_TINT}; border: 1px solid {ACCENT_BORDER_SOFT};"
        f" border-radius: {RADIUS_CARD}px; }}"
        "QLabel { background: transparent; border: none; }"
    ),
    "warning": (
        f"QWidget#noticeBar {{ background: {ORANGE_TINT}; border: 1px solid {_INK_ON_ORANGE};"
        f" border-radius: {RADIUS_CARD}px; }}"
        "QLabel { background: transparent; border: none; }"
    ),
}
_TEXT_QSS = f"font-size: {FONT_BODY}px; color: {INK}; background: transparent;"
_CLOSE_PX = 14
_CLOSE_BOX = 22


def dismissed_notice_ids(settings=None) -> list:
    """The ids the user closed, oldest first; empty on anything unreadable."""
    store = settings or QgsSettings()
    try:
        raw = store.value(_SETTINGS_KEY, "", type=str)
        data = json.loads(raw) if raw else []
    except Exception:  # noqa: BLE001 - a stale value is an empty memory
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, str) and item]


def remember_dismissed(notice_id: str, settings=None) -> None:
    """Add one id to the memory, keeping the last `_DISMISSED_CAP`."""
    if not notice_id:
        return
    store = settings or QgsSettings()
    ids = [i for i in dismissed_notice_ids(store) if i != notice_id]
    ids.append(notice_id)

    with contextlib.suppress(Exception):
        store.setValue(_SETTINGS_KEY, json.dumps(ids[-_DISMISSED_CAP:], ensure_ascii=False))


def _shorten(text: str, cap: int) -> str:
    """``text`` within ``cap`` characters, cut on a word and marked as cut."""
    if len(text) <= cap:
        return text
    head = text[:cap - 1]
    space = head.rfind(" ")
    if space > cap // 2:
        head = head[:space]
    return head.rstrip(" ,;:.") + "\u2026"


class NoticeBar(QWidget):
    """The served sentence, hidden until `show_notice` and after the x."""

    dismissed = pyqtSignal(str)
    link_opened = pyqtSignal(str)

    def __init__(self, parent=None, settings=None):
        super().__init__(parent)
        self._settings = settings
        self._notice: dict = {}


        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, SPACE_CARD, 12, 0)
        outer.setSpacing(0)
        self._bar = QWidget(self)
        self._bar.setObjectName("noticeBar")
        self._bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(self._bar)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(SPACE_CARD + 2)
        self._text = QLabel(self._bar)
        self._text.setWordWrap(True)
        self._text.setTextFormat(Qt.TextFormat.RichText)
        self._text.setOpenExternalLinks(False)
        self._text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._text.setStyleSheet(scale_qss_font_px(_TEXT_QSS))
        self._text.setMaximumHeight(self._text.fontMetrics().lineSpacing() * _MAX_NOTICE_LINES)
        self._text.linkActivated.connect(self._on_link)
        row.addWidget(self._text, 1, Qt.AlignmentFlag.AlignVCenter)
        self._close = IconButton(self._bar, None, _CLOSE_PX, self.tr("Dismiss"), _BTN_CHIP_CLOSE)
        self._close.set_icon("close", _CLOSE_PX)
        self._close.setFixedSize(_CLOSE_BOX, _CLOSE_BOX)
        self._close.clicked.connect(self._on_close)
        row.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        outer.addWidget(self._bar)
        self.hide()



    def show_notice(self, notice: dict) -> bool:
        """Show one served notice; False when it is unreadable or was closed before."""
        if not isinstance(notice, dict):
            self.clear()
            return False
        ident = notice.get("id")
        text = notice.get("text")
        if not isinstance(ident, str) or not ident.strip() or not isinstance(text, str) or not text.strip():
            self.clear()
            return False
        ident = ident.strip()
        if ident in dismissed_notice_ids(self._settings):
            self.clear()
            return False
        url = notice.get("url")
        if not isinstance(url, str) or not url.strip().lower().startswith(("http://", "https://")):
            url = ""
        level = notice.get("level") if notice.get("level") in LEVELS else "info"
        full = text.strip()
        shown = _shorten(full, _MAX_TEXT_CHARS)
        self._notice = {"id": ident, "text": shown, "url": url.strip(), "level": level}




        self._text.setToolTip(full if shown != full else "")
        self._bar.setStyleSheet(_BAR_QSS[level])
        body = html.escape(self._notice["text"])
        if self._notice["url"]:
            body += (f' <a href="{html.escape(self._notice["url"], quote=True)}"'
                     f' style="color: {ACCENT_INK}; text-decoration: none; font-weight: 500;">'
                     f"{html.escape(self.tr('Read more'))}</a>")
        self._text.setText(body)
        self._close.setAccessibleName(self.tr("Dismiss"))
        self.show()
        return True

    def clear(self) -> None:
        """Take the bar down without remembering anything."""
        self._notice = {}
        self._text.setText("")
        self.hide()

    def notice_id(self) -> str:
        return self._notice.get("id", "")



    def _on_close(self) -> None:
        ident = self.notice_id()
        remember_dismissed(ident, self._settings)
        self.clear()
        if ident:
            self.dismissed.emit(ident)

    def _on_link(self, href: str) -> None:
        url = self._notice.get("url") or href
        if url:
            open_external_url(url, self.window() or self)
            self.link_opened.emit(url)

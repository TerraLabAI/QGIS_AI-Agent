# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The round marks of an answer's sources, and the sheet that lists them."""
















from __future__ import annotations

import hashlib

from qgis.PyQt.QtCore import QRectF, QSize, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .external_links import open_external_url
from .font_scale import scale_qss_font_px, widget_pixel_ratio
from .icons import pixmap_for, render_pixmap
from .style import (
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INK_3,
    LINE_SOFT,
    LINE_STRONG,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SURFACE,
    drop_shadow,
    hover_pill,
    qcolor,
)
from .widgets import ElidedLabel



MARK_PX = 16
_INITIAL_SHARE = 0.62
_GLYPH_SHARE = 0.62
STACK_OVERLAP = 6
_MAX_STACKED = 3

ROW_PX = 44
SHEET_MARK_PX = 24
_POPOVER_WIDTH = 300
_POPOVER_PAD = 6
_MAX_ROWS_SHOWN = 8
_LINK_PX = 12


_PAGE_GLYPHS = frozenset({"", "globe", "link"})

_POPOVER_SCROLL_QSS = (
    "QScrollArea { background: transparent; border: none; }"
    "QScrollBar:vertical { background: transparent; width: 8px;"
    " margin: 2px 2px 2px 0; border: none; }"
    f"QScrollBar::handle:vertical {{ background: {LINE_STRONG};"
    " border-radius: 3px; min-height: 24px; }"
    f"QScrollBar::handle:vertical:hover {{ background: {INK_3}; }}"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {"
    " height: 0px; border: none; background: none; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
    " background: transparent; }"
)
_TITLE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; font-weight: 500; color: {INK_3};"
    " background: transparent; border: none; }"
)
_NAME_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
)
_HOST_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}"
)



_HUES = (4, 24, 44, 96, 150, 172, 200, 222, 250, 282, 310, 340)


def source_host(url: str) -> str:
    """The host a source is shown as: no scheme, no ``www.``, no path."""
    text = str(url or "").strip()
    host = QUrl(text).host() if "://" in text else text.split("/")[0]
    host = host or text
    if host.lower().startswith("www."):
        host = host[4:]
    return host


def mark_color(host: str) -> QColor:
    """The disc colour of ``host``: hashed, so it never changes between runs."""
    digest = hashlib.sha1(str(host or "").lower().encode("utf-8"), usedforsecurity=False).digest()
    hue = _HUES[digest[0] % len(_HUES)]
    color = QColor()
    color.setHsl(hue, 150, 118)
    return color







_MARK_CACHE: dict = {}
_MARK_CACHE_MAX = 128


def source_mark_pixmap(host: str, size: int = MARK_PX, ratio: float = 1.0,
                       round_: bool = True, glyph: str = "") -> QPixmap:
    """The mark as a pixmap of ``size`` logical pixels at ``ratio``."""






    key = (str(host), int(size), round(float(ratio), 3), bool(round_), str(glyph))
    cached = _MARK_CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = _draw_source_mark(host, size, ratio, round_, glyph)
    if len(_MARK_CACHE) >= _MARK_CACHE_MAX:
        _MARK_CACHE.clear()
    _MARK_CACHE[key] = pixmap
    return pixmap


def _draw_source_mark(host: str, size: int, ratio: float, round_: bool, glyph: str) -> QPixmap:
    physical = max(1, int(round(size * ratio)))
    pixmap = QPixmap(physical, physical)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(mark_color(host))
        rect = QRectF(0, 0, size, size)
        if round_:
            painter.drawEllipse(rect)
        else:
            painter.drawRoundedRect(rect, size * 0.28, size * 0.28)
        glyph = str(glyph or "")
        if glyph:
            inner = max(6, int(round(size * _GLYPH_SHARE)))
            face = render_pixmap(glyph, QColor(Qt.GlobalColor.white), inner, ratio)
            offset = (size - inner) / 2.0
            painter.drawPixmap(QRectF(offset, offset, inner, inner), face, QRectF(face.rect()))
        else:
            initial = (host or "?").strip()[:1].upper() or "?"
            font = QFont(painter.font())
            font.setPixelSize(max(6, int(round(size * _INITIAL_SHARE))))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor(Qt.GlobalColor.white)))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, initial)
    finally:
        painter.end()
    return pixmap


def _mark_glyph(glyph, connector_id: str) -> str:
    """The glyph a mark wears: a connector's own, always; a page's only when it says something a globe does not (the book of a documentation page)."""

    glyph = str(glyph or "").strip()
    if connector_id:
        return glyph or "globe"
    return "" if glyph in _PAGE_GLYPHS else glyph


def _clean(items) -> list:
    """``[{name, url, host, glyph, id}]`` with the entries the panel can draw."""
    out = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        name = str(item.get("name") or "").strip() or source_host(url)
        if not (url or name):
            continue


        cid = str(item.get("id") or "").strip()
        entry = {"name": name, "url": url, "host": source_host(url) or name,
                 "glyph": _mark_glyph(item.get("glyph"), cid),
                 "id": cid, "key": cid or source_host(url) or name}




        identity = url or name
        if identity in seen:
            continue
        seen.add(identity)
        out.append(entry)
    return out


class SourcesButton(QWidget):
    """The stacked marks and ``10 sources``: a click opens the list."""





    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._hover = False
        self._popover: SourcesPopover | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(24)



        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_sources(self, items) -> None:
        self._items = _clean(items)
        if self._popover is not None:


            self._popover.close()
            self._popover.deleteLater()
            self._popover = None
        self.setVisible(bool(self._items))
        self.setToolTip(self.tr("Sources of this answer"))
        self.setAccessibleName(self.tr("Sources of this answer"))
        self.updateGeometry()
        self.update()

    def sources(self) -> list:
        return list(self._items)

    def _label(self) -> str:
        count = len(self._items)
        return self.tr("1 source") if count == 1 else self.tr("{n} sources").format(n=count)

    def _stack_width(self) -> int:
        shown = min(_MAX_STACKED, len(self._items))
        if shown == 0:
            return 0
        return MARK_PX + (shown - 1) * (MARK_PX - STACK_OVERLAP)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        font = QFont(self.font())
        font.setPixelSize(FONT_BODY)
        text_w = QFontMetrics(font).horizontalAdvance(self._label())
        return QSize(6 + self._stack_width() + 6 + text_w + 8, 24)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.sizeHint()

    def enterEvent(self, event):  # noqa: N802 - Qt override
        super().enterEvent(event)
        self._hover = True
        self.update()

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
            self.open_popover()
        super().mouseReleaseEvent(event)

    def open_popover(self) -> None:
        if not self._items:
            return
        if self._popover is None:
            self._popover = SourcesPopover(self._items, self)
        self._popover.show_under(self)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover_pill() if self._hover else qcolor(LINE_SOFT))
            painter.drawRoundedRect(QRectF(self.rect()), self.height() / 2, self.height() / 2)
            ratio = widget_pixel_ratio(self)
            x = 6
            y = (self.height() - MARK_PX) // 2

            shown = self._items[:_MAX_STACKED]
            for index in range(len(shown) - 1, -1, -1):
                item = shown[index]
                pixmap = source_mark_pixmap(item["key"], MARK_PX, ratio, glyph=item["glyph"])
                left = x + index * (MARK_PX - STACK_OVERLAP)

                painter.setPen(QPen(qcolor(SURFACE), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(left, y, MARK_PX, MARK_PX))
                painter.drawPixmap(left, y, pixmap)
            font = QFont(self.font())
            font.setPixelSize(FONT_BODY)
            painter.setFont(font)
            painter.setPen(QPen(qcolor(INK if self._hover else INK_2)))
            text_left = x + self._stack_width() + 6
            painter.drawText(QRectF(text_left, 0, self.width() - text_left - 8, self.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label())
        finally:
            painter.end()


class _SourceRow(QWidget):
    """One row of the sheet: the mark, the name over the host, the open-link glyph under the pointer."""


    clicked = pyqtSignal(str)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self._url = item["url"]
        self._hover = False
        self.setFixedHeight(ROW_PX)
        self.setCursor(Qt.CursorShape.PointingHandCursor if self._url
                       else Qt.CursorShape.ArrowCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 0, 8, 0)
        row.setSpacing(10)
        mark = QLabel(self)
        mark.setFixedSize(SHEET_MARK_PX, SHEET_MARK_PX)
        mark.setPixmap(source_mark_pixmap(item["key"], SHEET_MARK_PX, widget_pixel_ratio(self),
                                          round_=False, glyph=item["glyph"]))
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QWidget(self)
        col = QVBoxLayout(words)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)


        name = ElidedLabel(item["name"], words)
        name.setStyleSheet(_NAME_QSS)
        col.addWidget(name)
        host = ElidedLabel(item["host"] if item["host"] != item["name"] else (self._url or ""),
                           words, mode=Qt.TextElideMode.ElideMiddle)
        host.setStyleSheet(_HOST_QSS)
        host.setVisible(bool(host.full_text()))
        col.addWidget(host)
        row.addWidget(words, 1, Qt.AlignmentFlag.AlignVCenter)
        self._link = QLabel(self)
        self._link.setFixedSize(_LINK_PX + 4, _LINK_PX + 4)
        self._link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._link.setPixmap(pixmap_for(self, "link", _LINK_PX, qcolor(INK_3)))
        self._link.setVisible(False)
        row.addWidget(self._link, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setToolTip(self._url)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(item["name"])
        self.setAccessibleDescription(self._url)

    def focusInEvent(self, event):  # noqa: N802 - Qt override
        super().focusInEvent(event)


        self._hover = True
        self._link.setVisible(bool(self._url))
        self.update()

    def focusOutEvent(self, event):  # noqa: N802 - Qt override
        super().focusOutEvent(event)
        if not self.underMouse():
            self._hover = False
            self._link.setVisible(False)
            self.update()

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit(self._url)
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        super().enterEvent(event)
        self._hover = True
        self._link.setVisible(bool(self._url))
        self.update()

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self._hover = False
        self._link.setVisible(False)
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self._url)
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        if self._hover and self._url:
            painter = QPainter(self)
            try:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(hover_pill())
                painter.drawRoundedRect(QRectF(self.rect()), RADIUS_CONTROL, RADIUS_CONTROL)
            finally:
                painter.end()
        super().paintEvent(event)


class SourcesPopover(QFrame):
    """The list under ``10 sources``: the site's raised sheet, a title row, one row per source, that closes on a click outside like any popup."""


    def __init__(self, items: list, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("sourcesPopover")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QFrame#sourcesPopover {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
            f" border-radius: {RADIUS_CARD}px; }}"
            + _POPOVER_SCROLL_QSS)
        col = QVBoxLayout(self)
        col.setContentsMargins(_POPOVER_PAD, _POPOVER_PAD, _POPOVER_PAD, _POPOVER_PAD)
        col.setSpacing(2)
        count = len(items)
        title = QLabel(self.tr("1 source") if count == 1
                       else self.tr("{n} sources").format(n=count), self)
        title.setStyleSheet(_TITLE_QSS)
        title.setContentsMargins(8, 4, 8, 2)
        col.addWidget(title)



        rows = QWidget(self)
        rows_col = QVBoxLayout(rows)
        rows_col.setContentsMargins(0, 0, 0, 0)
        rows_col.setSpacing(0)
        self._rows: list = []
        self._anchor = None
        for item in items:
            row = _SourceRow(item, rows)
            row.clicked.connect(self._open)
            rows_col.addWidget(row)
            self._rows.append(row)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(rows)
        scroll.setFixedHeight(ROW_PX * min(count, _MAX_ROWS_SHOWN))
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        col.addWidget(scroll)
        self.setFixedWidth(_POPOVER_WIDTH)
        drop_shadow(self, "raised")

    def _open(self, url: str) -> None:
        self.hide()
        if url:
            open_external_url(url, parent=self.parentWidget())

    def keyPressEvent(self, event):  # noqa: N802 - Qt override




        if event.key() == Qt.Key.Key_Escape:
            anchor = self._anchor
            self.hide()
            if anchor is not None:
                try:
                    anchor.setFocus(Qt.FocusReason.PopupFocusReason)
                except (RuntimeError, AttributeError):
                    pass
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            self._walk(1 if event.key() == Qt.Key.Key_Down else -1)
            event.accept()
            return
        super().keyPressEvent(event)

    def _walk(self, step: int) -> None:
        if not self._rows:
            return
        try:
            here = self._rows.index(self.focusWidget())
        except ValueError:
            here = -1 if step > 0 else 0
        self._rows[(here + step) % len(self._rows)].setFocus(Qt.FocusReason.TabFocusReason)

    def show_under(self, anchor: QWidget) -> None:
        """Open under ``anchor``, left edges aligned, kept on the screen."""
        self._anchor = anchor
        self.adjustSize()
        origin = anchor.mapToGlobal(anchor.rect().bottomLeft())
        x, y = origin.x(), origin.y() + 4
        screen = anchor.screen() if hasattr(anchor, "screen") else None
        if screen is not None:
            area = screen.availableGeometry()





            room_below = area.bottom() - y
            room_above = anchor.mapToGlobal(anchor.rect().topLeft()).y() - area.top() - 4
            if self.height() > room_below and room_above > room_below:
                height = min(self.height(), max(80, room_above))
                self.setFixedHeight(height)
                y = anchor.mapToGlobal(anchor.rect().topLeft()).y() - height - 4
            elif self.height() > room_below:
                self.setFixedHeight(min(self.height(), max(80, room_below)))
            x = max(area.left(), min(x, area.right() - self.width()))
            y = max(area.top(), min(y, area.bottom() - self.height()))
        self.move(x, y)
        self.show()
        if self._rows:
            self._rows[0].setFocus(Qt.FocusReason.PopupFocusReason)

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
































from __future__ import annotations

import hashlib

from qgis.PyQt.QtCore import QCoreApplication, QDate, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDockWidget,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import day_pattern, words_locale
from ..core.licence import date_text
from ..core.links import open_data_chip, open_data_label
from .external_links import open_external_url
from .font_scale import scale_point_size, scale_px_length, scale_qss_font_px, widget_pixel_ratio
from .icons import pixmap_for, render_pixmap
from .shared import event_pos, paint_styled_ground, round_popup_corners
from .style import (
    DARK,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INK_3,
    INSET,
    LINE,
    LINE_SOFT,
    LINE_STRONG,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SURFACE,
    hover_pill,
    qcolor,
)
from .widgets import ChatLabel, ElidedLabel



MARK_PX = 16
_INITIAL_SHARE = 0.62
_GLYPH_SHARE = 0.62
STACK_OVERLAP = 6
_MAX_STACKED = 3


ROW_PX = 48
SHEET_MARK_PX = 28
_POPOVER_WIDTH = 300
_POPOVER_MAX_WIDTH = 480
_POPOVER_PAD = 6
_MAX_ROWS_SHOWN = 8
_LINK_PX = 12
_CHEVRON_PX = 16

_ROW_PAD = 10
_ROW_GAP = 12

_MAX_DATES = 31

_DEFAULT_GLYPH = "globe"

_VIA_TERRALAB = "terralab"

_STORES = ((".amazonaws.com", "Amazon S3"), (".blob.core.windows.net", "Azure Blob Storage"),
           (".storage.googleapis.com", "Google Cloud Storage"), (".r2.dev", "Cloudflare R2"),
           (".digitaloceanspaces.com", "DigitalOcean Spaces"))

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
_VALUE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_2}; background: transparent; border: none; }}"
)

_DATASET_KEYS = ("product", "data_date", "license", "page_url")
_DOT = " \u00b7 "



_HUES = (4, 24, 44, 96, 150, 172, 200, 222, 250, 282, 310, 340)


def source_host(url: str) -> str:

    text = str(url or "").strip()
    host = QUrl(text).host() if "://" in text else text.split("/")[0]
    host = host or text
    if host.lower().startswith("www."):
        host = host[4:]
    return host


def readable_host(url: str) -> str:




    text = str(url or "").strip()
    own = open_data_label(text)
    if own:
        return own
    host = source_host(text).lower()
    for suffix, store in _STORES:
        if host.endswith(suffix):
            bucket = host[:-len(suffix)].split(".")[0]
            if bucket == "s3" or bucket.startswith("s3-") or not bucket:
                path = QUrl(text).path() if "://" in text else ""
                bucket = path.strip("/").split("/")[0]
            words = " ".join(bucket.replace("_", "-").split("-")).strip()
            return f"{words[:1].upper()}{words[1:]} ({store})" if words else store
    return source_host(text)


def _page_address(url: str, host: str) -> str:



    text = str(url or "").split("://", 1)[-1].rstrip("/")
    if text.lower().startswith("www."):
        text = text[4:]
    return "" if text.lower() == str(host or "").lower() else text


def mark_color(host: str) -> QColor:

    digest = hashlib.sha1(str(host or "").lower().encode("utf-8"), usedforsecurity=False).digest()
    hue = _HUES[digest[0] % len(_HUES)]
    color = QColor()
    color.setHsl(hue, 150, 118)
    return color







_MARK_CACHE: dict = {}
_MARK_CACHE_MAX = 128


def source_mark_pixmap(host: str, size: int = MARK_PX, ratio: float = 1.0,
                       round_: bool = True, glyph: str = "", tinted: bool = False) -> QPixmap:









    key = (str(host), int(size), round(float(ratio), 3), bool(round_), str(glyph), bool(tinted))
    cached = _MARK_CACHE.get(key)
    if cached is not None:
        return cached
    pixmap = _draw_source_mark(host, size, ratio, round_, glyph, tinted)
    if len(_MARK_CACHE) >= _MARK_CACHE_MAX:
        _MARK_CACHE.clear()
    _MARK_CACHE[key] = pixmap
    return pixmap


def _tint(host: str):


    hue = mark_color(host).hslHue()
    ink = QColor()
    ink.setHsl(hue, 160, 178 if DARK else 96)
    ground = qcolor(SURFACE)
    share = 0.22 if DARK else 0.14
    wash = QColor(int(ground.red() + (ink.red() - ground.red()) * share),
                  int(ground.green() + (ink.green() - ground.green()) * share),
                  int(ground.blue() + (ink.blue() - ground.blue()) * share))
    return wash, ink


def _draw_source_mark(host: str, size: int, ratio: float, round_: bool, glyph: str,
                      tinted: bool = False) -> QPixmap:
    physical = max(1, int(round(size * ratio)))
    pixmap = QPixmap(physical, physical)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setPen(Qt.PenStyle.NoPen)
        wash, ink = _tint(host) if tinted else (mark_color(host), QColor(Qt.GlobalColor.white))
        painter.setBrush(wash)
        rect = QRectF(0, 0, size, size)
        if round_:
            painter.drawEllipse(rect)
        else:
            painter.drawRoundedRect(rect, size * 0.28, size * 0.28)
        glyph = str(glyph or "")
        if glyph:
            inner = max(6, int(round(size * _GLYPH_SHARE)))
            if tinted:
                inner = max(6, int(round(size * 0.54)))
            face = render_pixmap(glyph, ink, inner, ratio)
            offset = (size - inner) / 2.0
            painter.drawPixmap(QRectF(offset, offset, inner, inner), face, QRectF(face.rect()))
        else:
            initial = (host or "?").strip()[:1].upper() or "?"
            font = QFont(painter.font())
            font.setPixelSize(max(6, int(round(size * _INITIAL_SHARE))))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(ink))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, initial)
    finally:
        painter.end()
    return pixmap


def _mark_glyph(glyph) -> str:

    return str(glyph or "").strip() or _DEFAULT_GLYPH


def _logo_path(item: dict) -> str:


    url = str(item.get("logo_url") or "")
    if not url:


        if open_data_label(item.get("url")):
            from .shared import TERRALAB_LOGO_PATH

            return TERRALAB_LOGO_PATH
        return ""
    from .library.pictures import store

    return store().want_file(url)


def item_mark_pixmap(item: dict, size: int, ratio: float = 1.0, round_: bool = True) -> QPixmap:

    path = _logo_path(item)
    if path:
        from .logo_tile import logo_pixmap

        chip = logo_pixmap(path, size, ratio)
        if chip is not None:
            return _clipped(chip, size, ratio, round_)
    return source_mark_pixmap(item["key"], size, ratio, round_=round_, glyph=item["glyph"], tinted=not round_)


def _clipped(chip: QPixmap, size: int, ratio: float, round_: bool) -> QPixmap:

    key = ("logo", chip.cacheKey(), int(size), round(float(ratio), 3), bool(round_))
    cached = _MARK_CACHE.get(key)
    if cached is not None:
        return cached
    physical = max(1, int(round(size * ratio)))
    pixmap = QPixmap(physical, physical)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        rect = QRectF(0, 0, size, size)
        path = QPainterPath()
        if round_:
            path.addEllipse(rect)
        else:
            path.addRoundedRect(rect, size * 0.28, size * 0.28)
        painter.setClipPath(path)
        painter.fillRect(rect, QColor(Qt.GlobalColor.white))
        painter.drawPixmap(rect, chip, QRectF(chip.rect()))
        if not round_:

            painter.setClipping(False)
            painter.setPen(QPen(qcolor(LINE), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), size * 0.28, size * 0.28)
    finally:
        painter.end()
    if len(_MARK_CACHE) >= _MARK_CACHE_MAX:
        _MARK_CACHE.clear()
    _MARK_CACHE[key] = pixmap
    return pixmap


def _logo_watch(widget, items) -> None:


    urls = {str(item.get("logo_url") or "") for item in items} - {""}
    if not urls:
        return
    from .library.pictures import store

    widget._logo_urls = urls
    store().ready.connect(widget._on_logo_ready)


def _clean(items) -> list:

    out = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        name = str(item.get("name") or "").strip() or open_data_chip(url) or readable_host(url)
        if not (url or name):
            continue


        cid = str(item.get("id") or "").strip()
        entry = {"name": name, "url": url, "host": open_data_chip(url) or readable_host(url) or name,
                 "glyph": _mark_glyph(item.get("glyph")),
                 "id": cid, "key": cid or name or source_host(url)}
        for key in _DATASET_KEYS:
            value = str(item.get(key) or "").strip()
            if key == "page_url" and not value.startswith(("https://", "http://")):
                value = ""
            entry[key] = value
        logo = str(item.get("logo_url") or "").strip()
        entry["logo_url"] = logo if logo.startswith("https://") else ""
        entry["via"] = str(item.get("via") or "").strip()
        for key in ("kind", "resolution", "attribution"):
            entry[key] = str(item.get(key) or "").strip()
        dates = item.get("dates")
        entry["dates"] = [str(d) for d in dates[:_MAX_DATES] if str(d).strip()] if isinstance(dates, list) else []
        try:
            entry["files"] = max(0, int(item.get("files") or 0))
        except (TypeError, ValueError):
            entry["files"] = 0




        identity = url or name
        if identity in seen:
            continue
        seen.add(identity)
        out.append(entry)
    return out


class SourcesButton(QWidget):






    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list = []
        self._hover = False
        self._popover: SourcesPopover | None = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(scale_px_length(24))



        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def _on_logo_ready(self, url: str) -> None:
        if url in getattr(self, "_logo_urls", ()):
            self.update()

    def set_sources(self, items) -> None:
        self._items = _clean(items)
        _logo_watch(self, self._items[:_MAX_STACKED])
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

    def sizeHint(self) -> QSize:  # noqa: N802
        font = QFont(self.font())
        font.setPixelSize(scale_point_size(FONT_BODY))
        text_w = QFontMetrics(font).horizontalAdvance(self._label())
        return QSize(6 + self._stack_width() + 6 + text_w + 8, scale_px_length(24))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def enterEvent(self, event):  # noqa: N802
        super().enterEvent(event)
        self._hover = True
        self.update()

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
            self.open_popover()
        super().mouseReleaseEvent(event)

    def open_popover(self) -> None:
        if not self._items:
            return
        if self._popover is None:
            self._popover = SourcesPopover(self._items, self)
        self._popover.show_under(self)

    def paintEvent(self, event):  # noqa: N802
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
                pixmap = item_mark_pixmap(item, MARK_PX, ratio)
                left = x + index * (MARK_PX - STACK_OVERLAP)

                painter.setPen(QPen(qcolor(SURFACE), 2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QRectF(left, y, MARK_PX, MARK_PX))
                painter.drawPixmap(left, y, pixmap)
            font = QFont(self.font())
            font.setPixelSize(scale_point_size(FONT_BODY))
            painter.setFont(font)
            painter.setPen(QPen(qcolor(INK if self._hover else INK_2)))
            text_left = x + self._stack_width() + 6
            painter.drawText(QRectF(text_left, 0, self.width() - text_left - 8, self.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._label())
        finally:
            painter.end()


def _day(iso: str, pattern: str) -> str:

    parsed = QDate.fromString(str(iso or ""), "yyyy-MM-dd")
    return words_locale().toString(parsed, pattern) if parsed.isValid() else str(iso or "")


def compact_dates(days) -> str:


    days = [QDate.fromString(str(d), "yyyy-MM-dd") for d in days or ()]
    days = sorted(d for d in days if d.isValid())
    if not days:
        return ""
    loc = words_locale()
    if len(days) > 3:
        first, last = days[0], days[-1]
        if (first.year(), first.month()) == (last.year(), last.month()):
            span = loc.toString(first, "MMM yyyy")
        elif first.year() == last.year():
            span = f"{loc.toString(first, 'MMM')}-{loc.toString(last, 'MMM yyyy')}"
        else:
            span = f"{loc.toString(first, 'MMM yyyy')}-{loc.toString(last, 'MMM yyyy')}"
        return QCoreApplication.translate("SourcesPopover", "{n} scenes, {span}").format(n=len(days), span=span)
    if len({d.year() for d in days}) == 1:
        return ", ".join([loc.toString(d, "d MMM") for d in days[:-1]] + [loc.toString(days[-1], "d MMM yyyy")])
    return ", ".join(loc.toString(d, "d MMM yyyy") for d in days)


def credits_text(items) -> str:

    lines: list = []
    for item in items or []:
        credit = str(item.get("attribution") or "").strip()
        if credit and credit not in lines:
            lines.append(credit)
    return "\n".join(lines)


class _TextButton(QWidget):



    clicked = pyqtSignal()

    def __init__(self, text: str, glyph: str = "", parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(4)
        self._glyph_name = glyph
        self._glyph = QLabel(self)
        self._glyph.setFixedSize(_LINK_PX, _LINK_PX)
        self._glyph.setVisible(bool(glyph))
        self._words = QLabel(text, self)
        for child in (self._glyph, self._words):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self._glyph, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self._words, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.setAccessibleName(text)
        self._paint(False)

    def set_text(self, text: str) -> None:
        self._words.setText(text)

    def _paint(self, hot: bool) -> None:
        ink = INK if hot else INK_2
        self._words.setStyleSheet(scale_qss_font_px(
            f"QLabel {{ font-size: {FONT_HINT}px; color: {ink}; background: transparent; border: none;"
            f" text-decoration: {'underline' if hot else 'none'}; }}"))
        if self._glyph_name:
            self._glyph.setPixmap(pixmap_for(self, self._glyph_name, _LINK_PX, qcolor(ink)))

    def enterEvent(self, event):  # noqa: N802
        super().enterEvent(event)
        self._paint(True)

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self._paint(self.hasFocus())

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)
        self._paint(True)

    def focusOutEvent(self, event):  # noqa: N802
        super().focusOutEvent(event)
        self._paint(self.underMouse())

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.clicked.emit()
        event.accept()


def _via_text(widget, via: str) -> str:

    return QCoreApplication.translate("SourcesPopover", "Served by TerraLab") if via == _VIA_TERRALAB else via


def _detail_fields(widget, item: dict) -> list:




    fields = []
    dates = item.get("dates") or []
    if dates:
        days = ", ".join(_day(d, day_pattern(short_month=True)) for d in dates)
        fields.append((QCoreApplication.translate("SourcesPopover", "Dates"), days))
    elif item.get("data_date"):
        fields.append((QCoreApplication.translate("SourcesPopover", "Date"), date_text(item["data_date"])))
    for key, label in (
            ("resolution", QCoreApplication.translate("SourcesPopover", "Resolution")),
            ("license", QCoreApplication.translate("SourcesPopover", "Licence")),
            ("attribution", QCoreApplication.translate("SourcesPopover", "Credit"))):
        if item.get(key):
            fields.append((label, item[key]))
    vias = [_via_text(widget, v.strip()) for v in str(item.get("via") or "").split(", ") if v.strip()]
    if vias:
        fields.append((QCoreApplication.translate("SourcesPopover", "Access"), ", ".join(vias)))
    files = item.get("files", 0)
    if files:
        fields.append((QCoreApplication.translate("SourcesPopover", "Files"), str(files)))
    if open_data_label(item.get("url")):


        fields.append((QCoreApplication.translate("SourcesPopover", "Address"), str(item["url"])))
    return fields


class _SourceDetails(QWidget):




    open_url = pyqtSignal(str)

    def __init__(self, fields: list, url: str, page: bool, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)

        col.setContentsMargins(_ROW_PAD + SHEET_MARK_PX + _ROW_GAP, 0, _ROW_PAD, 10)
        col.setSpacing(8)
        rule = QFrame(self)
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"QFrame {{ background: {LINE}; border: none; }}")
        col.addWidget(rule)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        keys = []
        for index, (label, value) in enumerate(fields):
            key = QLabel(label, self)
            key.setStyleSheet(_HOST_QSS)
            key.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            text = ChatLabel(value, self, wrap=True, selectable=True)
            text.setStyleSheet(_VALUE_QSS)
            grid.addWidget(key, index, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(text, index, 1)
            keys.append(key)
        grid.setColumnStretch(1, 1)
        if keys:

            for key in keys:
                key.ensurePolished()
            grid.setColumnMinimumWidth(0, max(k.fontMetrics().horizontalAdvance(k.text()) for k in keys) + 2)
        col.addLayout(grid)
        if url:
            link = _TextButton(self.tr("Open page") if page else self.tr("Open dataset page"), "external", self)
            link.setToolTip(url)
            link.clicked.connect(lambda: self.open_url.emit(url))
            col.addWidget(link)
        self.hide()


class _SourceRow(QWidget):





    clicked = pyqtSignal(str)
    toggled = pyqtSignal(bool)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self._url = item.get("page_url") or item["url"]
        self._hover = False
        self._open = False


        self.setMinimumHeight(ROW_PX)
        row = QHBoxLayout(self)
        row.setContentsMargins(_ROW_PAD, 8, _ROW_PAD, 8)
        row.setSpacing(_ROW_GAP)
        self._item = item
        self._mark = QLabel(self)
        self._mark.setFixedSize(SHEET_MARK_PX, SHEET_MARK_PX)
        self._paint_mark()
        _logo_watch(self, [item])
        row.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignTop)
        words = QWidget(self)
        col = QVBoxLayout(words)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)



        product = item.get("product", "")
        top = product or item["name"]
        self._maker = item["name"] if product and not product.lower().startswith(item["name"].lower()) else ""


        name = ChatLabel(top, words, wrap=True)
        name.setStyleSheet(_NAME_QSS)
        name.setMaximumHeight(2 * name.fontMetrics().lineSpacing() + 2)
        name.setToolTip(top)
        col.addWidget(name)
        self._fact_text = self._fact(item)
        under = self._under(False)
        self._under_label = ElidedLabel(under, words, mode=Qt.TextElideMode.ElideRight if self._described(item)
                                        else Qt.TextElideMode.ElideMiddle)
        self._under_label.setStyleSheet(_HOST_QSS)
        self._under_label.setVisible(bool(under))
        col.addWidget(self._under_label)
        row.addWidget(words, 1, Qt.AlignmentFlag.AlignVCenter)
        self.fields = _detail_fields(self, item)
        self.expandable = bool(self.fields)
        self._chevron = QLabel(self)
        self._chevron.setFixedSize(_CHEVRON_PX, _CHEVRON_PX)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignTop)
        if self.expandable:
            self._paint_chevron()
        else:

            self._chevron.setPixmap(pixmap_for(self, "link", _LINK_PX, qcolor(INK_3)))
            self._chevron.setVisible(False)
            self.setToolTip(self._url)
        self.setCursor(Qt.CursorShape.PointingHandCursor if (self.expandable or self._url)
                       else Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(top)
        self.setAccessibleDescription(under)

    def _under(self, open_: bool) -> str:


        parts = [self._maker] if self._maker else []
        if self._fact_text and not open_:
            parts.append(self._fact_text)
        return _DOT.join(parts)

    @staticmethod
    def _described(item: dict) -> bool:
        return any(item.get(key) for key in _DATASET_KEYS)

    def _fact(self, item: dict) -> str:


        dates = compact_dates(item.get("dates"))
        if dates:
            return dates
        if item.get("data_date"):
            return date_text(item["data_date"])
        if _VIA_TERRALAB in [v.strip() for v in str(item.get("via") or "").split(",")]:
            return self.tr("Served by TerraLab")
        if item.get("license"):
            return item["license"]
        if self._described(item) or (item.get("via") and item["host"] == item["name"]):
            return ""
        return item["host"] if item["host"] != item["name"] else _page_address(item["url"], item["host"])

    def is_open(self) -> bool:
        return self._open

    def set_open(self, on: bool) -> None:
        if not self.expandable or bool(on) == self._open:
            return
        self._open = bool(on)
        self._paint_chevron()
        under = self._under(self._open)
        self._under_label.setText(under)
        self._under_label.setVisible(bool(under))
        self.toggled.emit(self._open)

    def _activate(self) -> None:
        if self.expandable:
            self.set_open(not self._open)
        else:
            self.clicked.emit(self._url)

    def _paint_chevron(self) -> None:

        self._chevron.setPixmap(pixmap_for(self, "chevron_down" if self._open else "chevron_right",
                                           _CHEVRON_PX, qcolor(INK_3)))

    def _paint_mark(self) -> None:
        self._mark.setPixmap(item_mark_pixmap(self._item, SHEET_MARK_PX, widget_pixel_ratio(self), round_=False))

    def _on_logo_ready(self, url: str) -> None:
        if url in getattr(self, "_logo_urls", ()):
            self._paint_mark()

    def _set_hover(self, on: bool) -> None:
        self._hover = on
        if not self.expandable:
            self._chevron.setVisible(on and bool(self._url))

        if self.parentWidget() is not None:
            self.parentWidget().update()

    def focusInEvent(self, event):  # noqa: N802
        super().focusInEvent(event)

        self._set_hover(True)

    def focusOutEvent(self, event):  # noqa: N802
        super().focusOutEvent(event)
        if not self.underMouse():
            self._set_hover(False)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event):  # noqa: N802
        super().enterEvent(event)
        self._set_hover(True)

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self._set_hover(self.hasFocus())

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self._activate()
        super().mouseReleaseEvent(event)

    def lit(self) -> bool:
        return self._hover and bool(self.expandable or self._url)


class _SourceItem(QWidget):





    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self.row = _SourceRow(item, self)
        col.addWidget(self.row)
        self.details = None
        if self.row.expandable:
            page = item.get("kind", "") in ("page", "doc")
            self.details = _SourceDetails(self.row.fields, self.row._url, page, self)
            col.addWidget(self.details)

    def paintEvent(self, event):  # noqa: N802
        open_ = self.row.is_open()
        lit = self.row.lit()
        if not (open_ or lit):
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover_pill() if lit else qcolor(INSET))
            painter.drawRoundedRect(QRectF(self.rect()), RADIUS_CONTROL, RADIUS_CONTROL)
        finally:
            painter.end()


class SourcesPopover(QFrame):




    def __init__(self, items: list, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        round_popup_corners(self)
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



        self._list = QWidget(self)
        rows_col = QVBoxLayout(self._list)
        rows_col.setContentsMargins(0, 0, 0, 0)
        rows_col.setSpacing(2)
        self._rows: list = []
        self._anchor = None
        for item in items:
            card = _SourceItem(item, self._list)
            row = card.row
            row.clicked.connect(self._open)
            rows_col.addWidget(card)
            self._rows.append(row)
            if card.details is not None:
                card.details.open_url.connect(self._open)
                row.toggled.connect(lambda on, d=card.details, c=card: self._fold(d, on, c))
        rows_col.addStretch(1)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setWidget(self._list)

        self._scroll.viewport().setAutoFillBackground(False)
        self._list.setAutoFillBackground(False)
        self._list.setObjectName("sourcesList")
        self._list.setStyleSheet("QWidget#sourcesList { background: transparent; }")
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        col.addWidget(self._scroll)
        self._credits = credits_text(items)
        self._copy = _TextButton(self.tr("Copy credits"), "copy", self)
        self._copy.setToolTip(self.tr("Copy one credit line per source, for a print layout"))
        self._copy.setContentsMargins(8, 2, 8, 2)
        self._copy.clicked.connect(self._copy_credits)
        self._copy.setVisible(bool(self._credits))
        col.addWidget(self._copy)

        self._copied = QTimer(self)
        self._copied.setSingleShot(True)
        self._copied.setInterval(1500)
        self._copied.timeout.connect(lambda: self._copy.set_text(self.tr("Copy credits")))
        self.setFixedWidth(_POPOVER_WIDTH)
        self._fit_list()






    def _fit_list(self) -> None:

        layout = self._list.layout()

        width = self.width() - 2 * _POPOVER_PAD - 2
        height = layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
        self._scroll.setFixedHeight(min(height, ROW_PX * _MAX_ROWS_SHOWN))

    def _fold(self, details: QWidget, on: bool, card: QWidget = None) -> None:
        details.setVisible(on)
        if card is not None:
            card.update()
        self._fit_list()
        if self.isVisible() and self._anchor is not None:
            self._place(self._anchor)

    def _copy_credits(self) -> None:
        if not self._credits:
            return
        QApplication.clipboard().setText(self._credits)
        self._copy.set_text(self.tr("Copied"))
        self._copied.start()

    def paintEvent(self, event):  # noqa: N802
        paint_styled_ground(self)
        super().paintEvent(event)

    def _open(self, url: str) -> None:
        self.hide()
        if url:
            open_external_url(url, parent=self.parentWidget())

    def keyPressEvent(self, event):  # noqa: N802




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

        self._anchor = anchor
        self._place(anchor)
        self.show()
        if self._rows:
            self._rows[0].setFocus(Qt.FocusReason.PopupFocusReason)

    def _place(self, anchor: QWidget) -> None:

        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)


        dock = anchor
        while dock is not None and not isinstance(dock, QDockWidget):
            dock = dock.parentWidget()
        span = (dock or anchor.window()).width() - 32
        self.setFixedWidth(max(_POPOVER_WIDTH, min(_POPOVER_MAX_WIDTH, span)))
        self._fit_list()
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

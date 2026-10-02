# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import re

from qgis.PyQt.QtCore import QEvent, QRect, QRectF, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QKeySequence, QPainter, QPalette, QPen, QTextOption
from qgis.PyQt.QtWidgets import (
    QApplication,
    QLabel,
    QLayout,
    QMenu,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from .icons import icon_factory, icon_for, ink_of
from .style import _BTN_ICON, accent_color, repolish


_FLOW_ITEM_MIN_PX = 64


class FlowLayout(QLayout):






    def __init__(self, parent=None, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)




        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self):  # noqa: N802




        flags = getattr(Qt, "Orientations", None)
        if flags is not None:
            try:
                return flags(0)
            except (TypeError, ValueError):
                pass
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, dry_run=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802




        size = QSize()
        for item in self._items:
            wanted = item.minimumSize()
            size = size.expandedTo(QSize(min(wanted.width(), _FLOW_ITEM_MIN_PX), wanted.height()))
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _slots(self, area: QRect) -> tuple[list, int]:










        widest = max(1, area.width())
        edge = area.x() + area.width()
        slots = []
        left, top, row_height = area.x(), area.y(), 0
        for item in self._items:
            wanted = item.sizeHint()
            width, height = min(wanted.width(), widest), wanted.height()
            if row_height > 0 and left + width > edge:
                left, top, row_height = area.x(), top + row_height + self._v, 0
            slots.append((item, QRect(left, top, width, height)))
            left += width + self._h
            row_height = max(row_height, height)
        return slots, top + row_height

    def _arrange(self, rect: QRect, dry_run: bool) -> int:

        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        slots, bottom = self._slots(area)
        if not dry_run:
            for item, slot in slots:
                item.setGeometry(slot)
        return bottom - rect.y() + margins.bottom()


class Spinner(QWidget):






    def __init__(self, diameter: int = 14, color: str | None = None, parent=None):
        super().__init__(parent)
        self._angle = 0
        self._d = diameter

        if color is None:
            self._color = QColor(ink_of(parent) if parent is not None else ink_of(None))
            self._color.setAlphaF(0.7)
        else:
            self._color = QColor(color)
        self._wanted = False
        self.setFixedSize(diameter, diameter)
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._advance)

    def start(self) -> None:
        self._wanted = True
        if self.isVisible():
            self._timer.start()

    def stop(self) -> None:
        self._wanted = False
        self._timer.stop()

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._wanted:
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802
        self._timer.stop()
        super().hideEvent(event)

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):  # noqa: N802


        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            margin = 2.0
            rect = QRectF(margin, margin, self._d - 2 * margin, self._d - 2 * margin)
            pen = QPen(self._color)
            pen.setWidthF(max(1.6, self._d / 7.0))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawArc(rect, int(-self._angle * 16), 270 * 16)
            painter.end()
        except Exception:  # noqa: BLE001
            return



_MEASURE_RE = re.compile(r"\d[\d.,]*(?:[ \u00a0]?(?:%|[^\W\d_]{1,3}\b))?")


class ElidedLabel(QLabel):




    def __init__(self, text: str = "", parent=None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self._full = ""
        self._tail = ""



        self._elide_mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def set_elide_mode(self, mode: Qt.TextElideMode) -> None:
        self._elide_mode = mode
        self.update()

    def set_kept_tail(self, tail: str) -> None:


        self._tail = tail or ""
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802
        self._full = text or ""
        super().setText(self._full)
        self._sync_tooltip()
        self.updateGeometry()
        self.update()

    def full_text(self) -> str:
        return self._full

    def _sync_tooltip(self) -> None:



        width = self.contentsRect().width()
        trimmed = self.fontMetrics().horizontalAdvance(self._full) > width
        self.setToolTip(self._full if trimmed else "")

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._sync_tooltip()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        hint = super().minimumSizeHint()
        return QSize(24, hint.height())

    def _keep_measures(self, full: str, shown: str) -> str:



        if self._elide_mode != Qt.TextElideMode.ElideRight or not shown.endswith("\u2026") \
                or shown == full:
            return shown
        cut = len(shown) - 1
        for match in _MEASURE_RE.finditer(full):
            if match.start() < cut < match.end():
                return full[:match.start()].rstrip() + "\u2026"
        return shown

    def paintEvent(self, event):  # noqa: N802
        try:
            painter = QPainter(self)


            self.drawFrame(painter)
            rect = self.contentsRect()
            metrics = self.fontMetrics()
            tail = self._tail if self._tail and self._full.endswith(self._tail) else ""
            if tail and metrics.horizontalAdvance(self._full) > rect.width():
                head = self._full[:-len(tail)]
                room = max(0, rect.width() - metrics.horizontalAdvance(tail))
                text = self._keep_measures(head, metrics.elidedText(head, self._elide_mode, room)) + tail
            else:
                text = self._keep_measures(
                    self._full, metrics.elidedText(self._full, self._elide_mode, rect.width()))
            painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            painter.setFont(self.font())
            option = QTextOption(self.alignment())
            option.setWrapMode(QTextOption.WrapMode.NoWrap)
            painter.drawText(QRectF(rect), text, option)
            painter.end()
        except Exception:  # noqa: BLE001
            return




_BREAK = "​"


_RUN_PX_CHARS = 16
_BREAK_AFTER = frozenset("/?&=_-.,;:%+#|\\")


def break_anywhere(text: str) -> str:









    if not text:
        return text or ""
    out = []
    run = 0
    for ch in text:
        if ch.isspace() or ch == _BREAK:
            run = 0
            out.append(ch)
            continue
        if run >= _RUN_PX_CHARS:
            out.append(_BREAK)
            run = 0
        out.append(ch)
        run += 1
        if ch in _BREAK_AFTER:
            out.append(_BREAK)
            run = 0
    return "".join(out)


def unbroken(text: str) -> str:

    return (text or "").replace(_BREAK, "")


class ChatLabel(QLabel):










    def __init__(self, text: str = "", parent=None, wrap: bool = False,
                 selectable: bool = False):
        super().__init__(parent)
        self._raw = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        if selectable:
            self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        super().setWordWrap(bool(wrap))
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802
        self._raw = str(text or "")
        super().setText(break_anywhere(self._raw) if self.wordWrap() else self._raw)

    def setWordWrap(self, on: bool) -> None:  # noqa: N802
        super().setWordWrap(on)
        self.setText(self._raw)

    def text(self) -> str:
        return self._raw

    def selected_text(self) -> str:
        return unbroken(self.selectedText())

    def keyPressEvent(self, event):  # noqa: N802
        if event.matches(QKeySequence.StandardKey.Copy) and self.hasSelectedText():
            QApplication.clipboard().setText(self.selected_text())
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event):  # noqa: N802
        if not (self.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse):
            super().contextMenuEvent(event)
            return
        menu = QMenu(self)
        copy = menu.addAction(self.tr("Copy"))
        copy.setEnabled(self.hasSelectedText())
        copy.triggered.connect(lambda: QApplication.clipboard().setText(self.selected_text()))
        select_all = menu.addAction(self.tr("Select All"))
        select_all.triggered.connect(lambda: self.setSelection(0, len(super(ChatLabel, self).text())))
        menu.exec(event.globalPos())
        event.accept()


class IconButton(QToolButton):








    def __init__(self, parent=None, name: str | None = None, size: int = 18,
                 tooltip: str = "", qss: str = _BTN_ICON):
        super().__init__(parent)
        self.setProperty("hover", False)
        self.setProperty("active", False)
        self._glyph_factory = None
        self._glyph_name = None
        self._glyph_size = 18
        self._glyph_color = None
        self._hovering = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoRaise(True)
        self.setStyleSheet(qss)
        if tooltip:
            self.setToolTip(tooltip)
            self.setAccessibleName(tooltip)
        if name:
            self.set_icon(name, size)

    def set_icon(self, name: str, size: int = 18, color=None) -> None:
        self.set_glyph_icon(icon_factory(name, size, color), size, name=name, color=color)

    def set_glyph_icon(self, factory, size_px: int, name: str | None = None, color=None) -> None:






        self._glyph_factory = (factory, int(size_px))
        self._glyph_name, self._glyph_size, self._glyph_color = name, int(size_px), color
        self._repaint_glyph_icon()

    def _repaint_glyph_icon(self) -> None:
        pair = self._glyph_factory
        if not pair:
            return
        factory, size_px = pair
        try:
            if self._hovering and self._glyph_name and self._glyph_color is None:
                self.setIcon(icon_for(self, self._glyph_name, self._glyph_size, accent_color()))
            else:
                self.setIcon(factory(self))
            self.setIconSize(QSize(size_px, size_px))
        except (RuntimeError, AttributeError, TypeError):
            pass

    def enterEvent(self, event):  # noqa: N802
        super().enterEvent(event)
        self._set_glyph_hover(True)

    def leaveEvent(self, event):  # noqa: N802
        super().leaveEvent(event)
        self._set_glyph_hover(False)

    def _set_glyph_hover(self, hovering: bool) -> None:






        if self._hovering == hovering or not self.isEnabled():
            return
        self._hovering = hovering
        self._repaint_glyph_icon()

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        try:
            if event.type() == QEvent.Type.PaletteChange:
                self._repaint_glyph_icon()
        except (RuntimeError, AttributeError):
            pass

    def set_hovered(self, hovered: bool) -> None:
        if bool(self.property("hover")) == hovered:
            return
        self.setProperty("hover", hovered)
        repolish(self)

    def set_active(self, active: bool) -> None:

        if bool(self.property("active")) == active:
            return
        self.setProperty("active", active)
        repolish(self)

    def attach_menu(self, menu) -> None:

        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setMenu(menu)
        menu.aboutToShow.connect(lambda: self.set_active(True))
        menu.aboutToHide.connect(self._menu_closed)

    def _menu_closed(self) -> None:
        self.setDown(False)
        self.set_hovered(False)
        self.set_active(False)
        self._set_glyph_hover(self.underMouse())



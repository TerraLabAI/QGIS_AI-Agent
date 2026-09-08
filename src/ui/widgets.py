# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small widgets the panel shares: the flow layout, the spinner, the eliding label, the icon button and the clickable strip."""







from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter, QPalette, QPen, QTextOption
from qgis.PyQt.QtWidgets import (
    QLabel,
    QLayout,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from .icons import icon_factory, icon_for, ink_of
from .shared import event_pos
from .style import _BTN_ICON, accent_color, repolish


class FlowLayout(QLayout):
    """Lays items left to right and wraps, like words in a paragraph."""









    def __init__(self, parent=None, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self._h = h_spacing
        self._v = v_spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802 - Qt override
        self._items.append(item)




        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt override
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int):  # noqa: N802 - Qt override
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    def expandingDirections(self):  # noqa: N802 - Qt override




        flags = getattr(Qt, "Orientations", None)
        if flags is not None:
            try:
                return flags(0)
            except (TypeError, ValueError):
                pass
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self._arrange(QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect):  # noqa: N802 - Qt override
        super().setGeometry(rect)
        self._arrange(rect, dry_run=False)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt override
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, dry_run: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y = area.x(), area.y()
        line_height = 0
        room = max(1, area.width())
        for item in self._items:
            hint = item.sizeHint()





            if hint.width() > room:
                hint = QSize(room, hint.height())
            next_x = x + hint.width() + self._h
            if next_x - self._h > area.right() + 1 and line_height > 0:
                x = area.x()
                y += line_height + self._v
                next_x = x + hint.width() + self._h
                line_height = 0
            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()


class Spinner(QWidget):
    """A small rotating arc, the conventional busy indicator."""





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

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._wanted:
            self._timer.start()

    def hideEvent(self, event):  # noqa: N802 - Qt override
        self._timer.stop()
        super().hideEvent(event)

    def _advance(self) -> None:
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override


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
        except Exception:  # noqa: BLE001 - paint must never raise
            return


class ElidedLabel(QLabel):
    """A one-line label that trims with an ellipsis instead of widening the panel."""



    def __init__(self, text: str = "", parent=None,
                 mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight):
        super().__init__(parent)
        self._full = ""



        self._elide_mode = mode
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def set_elide_mode(self, mode: Qt.TextElideMode) -> None:
        self._elide_mode = mode
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full = text or ""
        super().setText(self._full)
        self.setToolTip(self._full if len(self._full) > 40 else "")
        self.updateGeometry()
        self.update()

    def full_text(self) -> str:
        return self._full

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        return QSize(24, hint.height())

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            rect = self.contentsRect()
            metrics = self.fontMetrics()
            text = metrics.elidedText(self._full, self._elide_mode, rect.width())
            painter.setPen(self.palette().color(QPalette.ColorRole.WindowText))
            painter.setFont(self.font())
            option = QTextOption(self.alignment())
            option.setWrapMode(QTextOption.WrapMode.NoWrap)
            painter.drawText(QRectF(rect), text, option)
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return




_UNBOUNDED_PX = 1 << 20


class WrapLabel(QLabel):
    """A wrapping label that stays as tall as the text it actually holds."""












    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self._sync_height()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        super().setText(text)
        self._sync_height()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_height()

    def _sync_height(self) -> None:







        rect = self.contentsRect()
        if rect.width() <= 0 or not self.text():
            return
        flags = int(Qt.TextFlag.TextWordWrap) | int(self.alignment())
        text_height = self.fontMetrics().boundingRect(
            0, 0, rect.width(), _UNBOUNDED_PX, flags, self.text()).height()
        needed = text_height + (self.height() - rect.height())
        if needed > 0 and needed != self.minimumHeight():
            self.setMinimumHeight(needed)


class IconButton(QToolButton):
    """Flat icon tool button with a property-driven hover and an active tint."""







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
        """Paint the button's vector glyph, and keep painting it."""





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

    def enterEvent(self, event):  # noqa: N802 - Qt override
        super().enterEvent(event)
        self._set_glyph_hover(True)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        super().leaveEvent(event)
        self._set_glyph_hover(False)

    def _set_glyph_hover(self, hovering: bool) -> None:
        """The glyph goes green under the cursor, ink again when it leaves."""





        if self._hovering == hovering or not self.isEnabled():
            return
        self._hovering = hovering
        self._repaint_glyph_icon()

    def changeEvent(self, event):  # noqa: N802 - Qt override
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
        """Leaf-green tint while the attached menu is open."""
        if bool(self.property("active")) == active:
            return
        self.setProperty("active", active)
        repolish(self)

    def attach_menu(self, menu) -> None:
        """Own ``menu`` as an instant popup and keep the tint honest around it."""
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setMenu(menu)
        menu.aboutToShow.connect(lambda: self.set_active(True))
        menu.aboutToHide.connect(self._menu_closed)

    def _menu_closed(self) -> None:
        self.setDown(False)
        self.set_hovered(False)
        self.set_active(False)
        self._set_glyph_hover(self.underMouse())



_FooterIconButton = IconButton


class ClickableArea(QWidget):
    """A strip of passive widgets that answers a click as one control."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if (event.button() == Qt.MouseButton.LeftButton
                and self.rect().contains(event_pos(event))):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


_ClickableFooterArea = ClickableArea

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The sheet above the composer for ``@``: rows and section headings."""



















from __future__ import annotations

from qgis.PyQt.QtCore import QEasingCurve, QPoint, QRectF, QSize, Qt, QVariantAnimation, pyqtSignal
from qgis.PyQt.QtGui import (
    QFont,
    QFontMetrics,
    QPainter,
    QStandardItem,
    QStandardItemModel,
)
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from .font_scale import scale_px_length, scale_qss_font_px, widget_pixel_ratio
from .icons import icon_for
from .layer_icons import layer_icon
from .mention_search import FILES
from .style import (
    _COMPLETER_POPUP_QSS,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    MONO_FAMILY,
    MOTION_POP_MS,
    RADIUS_CARD,
    RADIUS_CONTROL,
    SURFACE,
    drop_shadow,
    hover_pill,
    qcolor,
)





SHEET_WIDTH = 420
_SHEET_MIN_WIDTH = 240
_SHEET_MAX_WIDTH = 560
_SHEET_PAD = 6
_SHEET_GAP = 6
_SHADOW = 16
_MAX_VISIBLE_ROWS = 8

_ROW_HEIGHT = 36
_ROW_RADIUS = RADIUS_CONTROL
_ROW_PAD = 8
_GLYPH = 16
_GLYPH_GAP = 10
_DETAIL_GAP = 8
_DEPTH_INDENT = 14
_ROW_SLACK = 6
_POP_RISE = 4

_POPUP_KEYS = None

_SHEET_QSS = scale_qss_font_px(
    f"QFrame#mentionSheet {{ background: {SURFACE}; border: 1px solid {LINE_STRONG};"
    f" border-radius: {RADIUS_CARD}px; }}"
    f"QFrame#mentionRule {{ background: {LINE}; border: none; max-height: 1px; min-height: 1px; }}"
    f"QLabel#mentionFooter {{ font-size: {FONT_HINT}px; color: {INK_3};"
    " background: transparent; border: none; padding: 6px 8px 2px 8px; }"
) + _COMPLETER_POPUP_QSS + (

    "QListView#mentionPopup { background: transparent; border: none; padding: 0; }"
)


def popup_keys():
    """The keys an open sheet answers before the box sees them."""
    global _POPUP_KEYS
    if _POPUP_KEYS is None:
        _POPUP_KEYS = (
            Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab, Qt.Key.Key_Escape,
            Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
        )
    return _POPUP_KEYS


def _font(base: QFont, px: int, weight: int = 400, mono: bool = False) -> QFont:
    """A copy of ``base`` at ``px`` pixels, medium when asked, mono when asked."""
    font = QFont(base)
    if mono:
        families = [f.strip().strip("'") for f in MONO_FAMILY.split(",")]
        if hasattr(font, "setFamilies"):
            font.setFamilies(families)
        else:
            font.setFamily(families[0])
        font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPixelSize(scale_px_length(px))
    font.setWeight(QFont.Weight.Medium if weight >= 500 else QFont.Weight.Normal)
    return font


class _SheetDelegate(QStyledItemDelegate):
    """One row of the ``@`` sheet, painted whole."""










    def paint(self, painter, option, index):  # noqa: N802 - Qt override
        data = index.data(Qt.ItemDataRole.UserRole)
        data = data if isinstance(data, dict) else {}
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        rect = QRectF(opt.rect)
        if data.get("kind") == "header":
            self._paint_header(painter, opt, rect)
            return
        depth = int(data.get("depth") or 0)
        if depth:

            rect = rect.adjusted(min(depth, 4) * _DEPTH_INDENT, 0, 0, 0)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        states = QStyle.StateFlag
        on = bool(opt.state & states.State_Selected) or bool(opt.state & states.State_MouseOver)
        if on:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hover_pill())
            painter.drawRoundedRect(QRectF(opt.rect), _ROW_RADIUS, _ROW_RADIUS)
        left = rect.left() + _ROW_PAD
        if not opt.icon.isNull():
            top = rect.center().y() - _GLYPH / 2.0
            opt.icon.paint(painter, int(left), int(top), _GLYPH, _GLYPH)
        left += _GLYPH + _GLYPH_GAP
        edge = rect.right() - _ROW_PAD
        name_font = _font(opt.font, FONT_BASE, 500)
        detail_font = _font(opt.font, FONT_BODY)
        name_metrics = QFontMetrics(name_font)
        detail_metrics = QFontMetrics(detail_font)
        detail = str(data.get("detail") or "")
        room = max(40.0, edge - left)
        detail_width = detail_metrics.horizontalAdvance(detail) if detail else 0
        name_room = max(40.0, room - (detail_width + _DETAIL_GAP if detail else 0))
        name = name_metrics.elidedText(opt.text, Qt.TextElideMode.ElideRight, int(name_room))
        painter.setFont(name_font)
        painter.setPen(qcolor(INK))
        painter.drawText(QRectF(left, rect.top(), name_room, rect.height()),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), name)
        if detail:
            start = left + name_metrics.horizontalAdvance(name) + _DETAIL_GAP
            painter.setFont(detail_font)
            painter.setPen(qcolor(INK_2))
            painter.drawText(QRectF(start, rect.top(), max(0.0, edge - start), rect.height()),
                             int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                             detail_metrics.elidedText(detail, Qt.TextElideMode.ElideRight,
                                                       int(max(0.0, edge - start))))
        painter.restore()

    def _paint_header(self, painter, opt, rect) -> None:
        """The name of a layer tree group: a micro label, never a choice."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = _font(opt.font, FONT_MICRO, 500)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 104)
        painter.setFont(font)
        painter.setPen(qcolor(INK_3))
        left = rect.left() + _ROW_PAD
        metrics = QFontMetrics(font)
        room = max(40.0, rect.right() - _ROW_PAD - left)

        painter.drawText(QRectF(left, rect.top(), room, rect.height() - 6),
                         int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
                         metrics.elidedText(opt.text, Qt.TextElideMode.ElideRight, int(room)))
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        hint = super().sizeHint(option, index)
        hint.setHeight(_ROW_HEIGHT)
        return hint


class _SheetView(QListView):
    """The list inside the sheet: no focus of its own, the pointer tracked."""






    picked = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mentionPopup")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setIconSize(QSize(_GLYPH, _GLYPH))
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSelectionMode(QListView.SelectionMode.SingleSelection)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        index = self.indexAt(event.pos())
        if event.button() == Qt.MouseButton.LeftButton and index.isValid():
            self.picked.emit(index)
            return
        super().mouseReleaseEvent(event)


class MentionSheet(QWidget):
    """The sheet above the composer: the rows, a hairline, the footer line."""













    picked = pyqtSignal(object)

    def __init__(self, owner: QWidget):
        super().__init__(owner, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setObjectName("mentionSheetHost")
        self.setFocusProxy(owner)
        self._owner = owner
        self._model = QStandardItemModel(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(_SHADOW, _SHADOW, _SHADOW, _SHADOW)
        outer.setSpacing(0)
        self._frame = QFrame(self)
        self._frame.setObjectName("mentionSheet")
        self._frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._frame.setStyleSheet(_SHEET_QSS)
        drop_shadow(self._frame, "overlay")
        outer.addWidget(self._frame)
        col = QVBoxLayout(self._frame)
        col.setContentsMargins(_SHEET_PAD, _SHEET_PAD, _SHEET_PAD, _SHEET_PAD)
        col.setSpacing(_SHEET_GAP)
        self._view = _SheetView(self._frame)
        self._view.setModel(self._model)
        self._delegate = _SheetDelegate(self._view)
        self._view.setItemDelegate(self._delegate)
        self._view.picked.connect(self._on_picked)
        col.addWidget(self._view, 1)
        self._rule = QFrame(self._frame)
        self._rule.setObjectName("mentionRule")
        self._rule.setFrameShape(QFrame.Shape.NoFrame)
        col.addWidget(self._rule)
        self._footer = QLabel(self._frame)
        self._footer.setObjectName("mentionFooter")
        col.addWidget(self._footer)
        self._pop = QVariantAnimation(self)
        self._pop.setDuration(MOTION_POP_MS)
        self._pop.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._pop.valueChanged.connect(self._on_pop)
        self._final = QPoint()



    def set_rows(self, items, footer: str = "") -> None:
        """Fill the list: ``[{kind, label, value, detail, glyph}]``."""
        self._footer.setText(footer)
        self._footer.setVisible(bool(footer))
        self._rule.setVisible(bool(footer))
        self._model.clear()
        fallback = icon_for(self._view, "layers", _GLYPH, qcolor(INK_3))
        for item in list(items or [])[:500]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("value") or "")[:200]
            if not label:
                continue
            row = QStandardItem(label)
            row.setData(dict(item), Qt.ItemDataRole.UserRole)
            row.setEditable(False)
            kind = str(item.get("kind") or "")
            if kind == "header":


                row.setEnabled(False)
                row.setSelectable(False)
                self._model.appendRow(row)
                continue
            glyph = str(item.get("glyph") or "")
            logo = self._logo(str(item.get("icon") or ""))
            if logo is not None:



                row.setIcon(logo)
            elif kind == "layer":
                icon = layer_icon(str(item.get("value") or ""))
                row.setIcon(icon if not icon.isNull() else fallback)
            elif kind == FILES:
                row.setIcon(icon_for(self._view, "paperclip", _GLYPH, qcolor(INK_3)))
            elif glyph:

                row.setIcon(icon_for(self._view, glyph, _GLYPH, qcolor(INK_3)))
            detail = str(item.get("detail") or "")[:240]
            if detail:
                row.setToolTip(detail)
            self._model.appendRow(row)
        self._view.setCurrentIndex(self._first_choice())

    def _logo(self, path: str):
        """A plugin's own icon file as a row glyph, or None when there is none."""
        if not path:
            return None
        try:
            from qgis.PyQt.QtGui import QIcon

            from .logo_tile import logo_pixmap

            chip = logo_pixmap(path, _GLYPH, widget_pixel_ratio(self))
            return QIcon(chip) if chip is not None else None
        except Exception:  # noqa: BLE001 - a missing logo is a glyph, never a crash
            return None

    def row_count(self) -> int:
        return self._model.rowCount()

    def content_width(self) -> int:
        """The width the widest row needs to print its name and its description."""





        base = self._view.font()
        name_font = QFontMetrics(_font(base, FONT_BASE, 500))
        detail_font = QFontMetrics(_font(base, FONT_BODY))
        widest = 0
        for row in range(self._model.rowCount()):
            index = self._model.index(row, 0)
            data = index.data(Qt.ItemDataRole.UserRole)
            data = data if isinstance(data, dict) else {}
            if data.get("kind") == "header":
                continue
            width = 2 * _ROW_PAD + _GLYPH + _GLYPH_GAP
            width += name_font.horizontalAdvance(str(index.data() or ""))
            detail = str(data.get("detail") or "")
            if detail:
                width += _DETAIL_GAP + detail_font.horizontalAdvance(detail)
            widest = max(widest, width)


        widest += _ROW_SLACK
        if self._model.rowCount() > _MAX_VISIBLE_ROWS:

            widest += self._view.verticalScrollBar().sizeHint().width()
        return widest + 2 * _SHEET_PAD

    def _first_choice(self):
        """The first row of the sheet that is not a group heading."""
        for row in range(self._model.rowCount()):
            index = self._model.index(row, 0)
            data = index.data(Qt.ItemDataRole.UserRole)
            if not (isinstance(data, dict) and data.get("kind") == "header"):
                return index
        return self._model.index(0, 0)

    def current_data(self) -> dict | None:
        index = self._view.currentIndex()
        data = index.data(Qt.ItemDataRole.UserRole) if index.isValid() else None
        return data if isinstance(data, dict) else None

    def step(self, step: int) -> None:
        """Up or Down: the next pickable row, wrapping, headings stepped over."""
        count = self._model.rowCount()
        if not count:
            return
        current = self._view.currentIndex().row() if self._view.currentIndex().isValid() else -1
        for _ in range(count):
            current = (current + step) % count
            index = self._model.index(current, 0)
            if bool(self._model.flags(index) & Qt.ItemFlag.ItemIsSelectable):
                self._view.setCurrentIndex(index)
                self._view.scrollTo(index)
                return

    def page(self, step: int) -> None:
        for _ in range(_MAX_VISIBLE_ROWS - 1):
            self.step(step)

    def take_current(self) -> None:
        """Enter or Tab: the row under the keys is picked."""
        index = self._view.currentIndex()
        if index.isValid():
            self._on_picked(index)



    def open_above(self, anchor: QWidget) -> None:
        """Size to the rows and show above ``anchor``, left edges aligned."""





        width = max(_SHEET_MIN_WIDTH, min(SHEET_WIDTH, anchor.width()))
        width = max(width, min(_SHEET_MAX_WIDTH, self.content_width()))
        screen = QApplication.screenAt(anchor.mapToGlobal(QPoint(0, 0))) if hasattr(
            QApplication, "screenAt") else None
        if screen is not None:
            width = min(width, max(_SHEET_MIN_WIDTH, screen.availableGeometry().width() - 2 * _SHADOW - 16))
        rows = min(_MAX_VISIBLE_ROWS, max(1, self._model.rowCount()))
        self._view.setFixedHeight(rows * _ROW_HEIGHT)
        self.setFixedWidth(width + 2 * _SHADOW)
        self._frame.layout().activate()
        self.setFixedHeight(self._frame.sizeHint().height() + 2 * _SHADOW)
        top_left = anchor.mapToGlobal(QPoint(0, 0))
        x = top_left.x() - _SHADOW
        y = top_left.y() - self.height() + _SHADOW - 4
        screen = QApplication.screenAt(top_left) if hasattr(QApplication, "screenAt") else None
        if screen is not None:
            geometry = screen.availableGeometry()
            x = max(geometry.left(), min(x, geometry.right() - self.width()))
            if y < geometry.top():
                y = top_left.y() + anchor.height() - _SHADOW + 4
        self._final = QPoint(x, y)
        was_visible = self.isVisible()
        self.move(self._final)
        if not was_visible:
            self.show()
            self._pop_in(anchor)
        else:
            self.update()

    def _pop_in(self, anchor: QWidget) -> None:
        """The site's pop-in: opacity from 0 and a short rise, 160 ms."""
        self._pop.stop()
        if not anchor.isVisible():
            self.setWindowOpacity(1.0)
            return
        self.setWindowOpacity(0.0)
        self._pop.setStartValue(0.0)
        self._pop.setEndValue(1.0)
        self._pop.start()

    def _on_pop(self, value) -> None:
        try:
            progress = float(value)
        except (TypeError, ValueError):
            progress = 1.0
        self.setWindowOpacity(progress)
        self.move(QPoint(self._final.x(), self._final.y() + int(_POP_RISE * (1.0 - progress))))

    def hideEvent(self, event):  # noqa: N802 - Qt override
        self._pop.stop()
        super().hideEvent(event)



    def keyPressEvent(self, event):  # noqa: N802 - Qt override

        QApplication.sendEvent(self._owner, event)

    def _on_picked(self, index) -> None:
        data = index.data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict) or data.get("kind") == "header":
            return
        self.hide()
        self.picked.emit(data)

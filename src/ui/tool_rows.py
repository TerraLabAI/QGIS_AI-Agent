# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The parts a tool chip row and a task row are built from, and the group."""










from __future__ import annotations

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QFont, QPainter, QPainterPath, QPen
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .card_base import mono_font
from .cards_click import _ClickRow
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .layer_links import LayerLinkRow
from .style import (
    ACCENT_BORDER_SOFT,
    ACCENT_INK,
    CHIP_PX,
    FIELD,
    FONT_BODY,
    FONT_HINT,
    GREEN,
    HOVER,
    INK,
    INK_2,
    INK_3,
    LINE_STRONG,
    MOTION_FOLD_MS,
    ORANGE,
    RADIUS_CHIP,
    RADIUS_CONTROL,
    RED,
    ROW_PX,
    SPACE_CARD,
    SPACE_TIGHT,
    qcolor,
)
from .tool_describe import FAMILY_COLOUR_TOKENS, FAMILY_GLYPHS, FAMILY_ORDER
from .widgets import Spinner

_UNBOUNDED = 16777215

_MEDIUM = getattr(getattr(QFont, "Weight", None), "Medium", 63)

_CHEVRON_PX = 12
_FAMILY_GLYPH_PX = 11
_DISC_PX = 22
_GROUP_HEAD_QSS = scale_qss_font_px(
    f"QWidget#toolGroupHead {{ background: transparent; border-radius: {RADIUS_CONTROL}px; }}"
    f"QWidget#toolGroupHead:hover {{ background: {HOVER}; }}"



    f"QWidget#toolGroupHead:focus {{ border: 1px solid {ACCENT_BORDER_SOFT}; }}"
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)


class _HoverRow(_ClickRow):
    """A click row that says when the pointer is on it, for the parts that only show under the pointer (the chevron)."""


    hovered = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self.hovered.emit(False)
        super().leaveEvent(event)


class _MonoChip(QLabel):
    """The argument of a tool row: mono on the field, 22 px, 6 px corners, elided at the right."""



    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full = ""
        self._font = mono_font(FONT_HINT)
        self.setFixedHeight(CHIP_PX)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full = " ".join(str(text or "").split())
        super().setText(self._full)



        self.setToolTip(self._full)
        self.updateGeometry()
        self.update()

    def full_text(self) -> str:
        return self._full

    def _text_width(self) -> int:
        from qgis.PyQt.QtGui import QFontMetrics

        return QFontMetrics(self._font).horizontalAdvance(self._full)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._text_width() + 12 + 2, CHIP_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(min(48, self.sizeHint().width()), CHIP_PX)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), RADIUS_CHIP, RADIUS_CHIP)
            painter.fillPath(path, qcolor(FIELD))
            painter.setFont(self._font)
            painter.setPen(qcolor(INK_2))
            from qgis.PyQt.QtGui import QFontMetrics

            text = QFontMetrics(self._font).elidedText(
                self._full, Qt.TextElideMode.ElideRight, max(0, self.width() - 12))
            painter.drawText(QRectF(6, 0, self.width() - 12, self.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return


def _fold(widget: QWidget, open_it: bool, keeper) -> None:
    """Roll ``widget`` open or shut over 300 ms by its maximum height; in place on a hidden panel."""


    previous = getattr(keeper, "_fold_anim", None)
    if previous is not None:
        try:
            previous.stop()
        except (RuntimeError, AttributeError):
            pass
        keeper._fold_anim = None
    if not widget.isVisible() and not open_it:
        widget.setMaximumHeight(0)
        widget.setVisible(False)
        return
    window = widget.window()
    if window is None or not window.isVisible():
        widget.setVisible(open_it)
        widget.setMaximumHeight(_UNBOUNDED if open_it else 0)
        return
    target = max(0, widget.sizeHint().height())
    start = widget.height() if widget.isVisible() else 0
    if open_it:
        widget.setMaximumHeight(0)
        widget.setVisible(True)
    roll = QVariantAnimation(keeper)
    roll.setDuration(MOTION_FOLD_MS)
    roll.setEasingCurve(QEasingCurve.Type.OutQuint)
    roll.setStartValue(start if open_it else max(start, target))
    roll.setEndValue(target if open_it else 0)

    def step(value) -> None:
        try:
            widget.setMaximumHeight(max(0, int(value)))
        except RuntimeError:
            pass

    def done() -> None:
        try:
            widget.setVisible(open_it)
            widget.setMaximumHeight(_UNBOUNDED if open_it else 0)
            keeper._fold_anim = None
        except RuntimeError:
            pass

    roll.valueChanged.connect(step)
    roll.finished.connect(done)
    keeper._fold_anim = roll
    roll.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


class _StateDisc(QWidget):
    """The 22 px disc at the left of a task row: a green check when done, the step number in a ring with a green progress arc while active, the."""



    def __init__(self, number: int, parent=None):
        super().__init__(parent)
        self.number = number
        self.state = "pending"
        self.progress = 0.0
        self.setFixedSize(_DISC_PX, _DISC_PX)

        self._font = QFont(self.font())
        self._font.setPixelSize(FONT_HINT)
        self._font.setWeight(_MEDIUM)

    def set_state(self, state: str, progress: float | None = None) -> None:
        self.state = state
        if progress is not None:
            self.progress = max(0.0, min(1.0, float(progress)))
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            centre = rect.center()
            if self.state == "done":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(GREEN))
                painter.drawEllipse(rect)
                painter.setPen(_pen(_white(), 2.0))
                painter.drawPolyline(_check(centre))
            elif self.state == "failed":
                painter.setPen(_pen(qcolor(RED), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect)
                painter.setPen(_pen(qcolor(RED), 1.6))
                painter.drawLine(centre.x() - 3, centre.y() - 3, centre.x() + 3, centre.y() + 3)
                painter.drawLine(centre.x() + 3, centre.y() - 3, centre.x() - 3, centre.y() + 3)
            elif self.state == "skipped":
                painter.setPen(_pen(qcolor(LINE_STRONG), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect)
                painter.setPen(_pen(qcolor(INK_3), 1.6))
                painter.drawLine(centre.x() - 3.5, centre.y(), centre.x() + 3.5, centre.y())
            else:
                painter.setPen(_pen(qcolor(LINE_STRONG), 1.5))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(rect)
                if self.state == "active":
                    arc = rect.adjusted(0.25, 0.25, -0.25, -0.25)
                    painter.setPen(_pen(qcolor(GREEN), 1.8))
                    span = max(0.08, self.progress) * 360.0
                    painter.drawArc(arc, 90 * 16, -int(span * 16))
                painter.setFont(self._font)
                painter.setPen(qcolor(INK if self.state == "active" else INK_3))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(self.number))
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return


def _pen(colour, width: float) -> QPen:
    pen = QPen(colour)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _white():
    from qgis.PyQt.QtGui import QColor

    return QColor(255, 255, 255)


def _check(centre):
    from qgis.PyQt.QtCore import QPointF
    from qgis.PyQt.QtGui import QPolygonF

    return QPolygonF([QPointF(centre.x() - 4, centre.y() + 0.5),
                      QPointF(centre.x() - 1, centre.y() + 3.5),
                      QPointF(centre.x() + 4.5, centre.y() - 3)])






_FAMILY_COLOURS = {"accent_ink": ACCENT_INK, "orange": ORANGE, "ink_2": INK_2, "ink_3": INK_3}
_GLYPH_PX = 13
_STACK_HEAD_QSS = scale_qss_font_px(
    f"QLabel#stackLabel {{ font-size: {FONT_BODY}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
    f"QLabel#stackCount {{ font-size: {FONT_HINT}px; color: {INK_3};"
    " background: transparent; border: none; }"
)


def _card_of(row):
    """The ``ToolCard`` behind a row: a search or a code row wraps one."""
    return getattr(row, "card", row)


def stack_key(row) -> str:
    """What makes two rows in a row one row: the card's connector or tool."""
    card = _card_of(row)
    key = getattr(card, "stack_key", None)
    try:
        return str(key() or "") if callable(key) else ""
    except Exception:  # noqa: BLE001 - a row that cannot say stacks alone
        return ""


def family_of(row) -> str:
    """The family of a row: connector, plugin or qgis."""
    return str(getattr(_card_of(row), "family", "") or "")


def _family_colour(token: str):
    colour = _FAMILY_COLOURS.get(str(token or ""))
    if colour is None and str(token).startswith("#"):
        colour = token
    return qcolor(colour or INK_2)


class ToolStack(QWidget):
    """Seven calls to OpenStreetMap in a row are one row: the connector's glyph and name, ``4 calls``, and a chevron that opens the calls."""







    def __init__(self, first: QWidget, parent=None):
        super().__init__(parent)
        self.rows: list[QWidget] = []
        self.key = stack_key(first)
        self._open = False
        self._fold_anim = None
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._head = _HoverRow(self)
        self._head.setObjectName("toolGroupHead")
        self._head.setStyleSheet(_GROUP_HEAD_QSS + _STACK_HEAD_QSS)
        self._head.setFixedHeight(ROW_PX)
        self._head.setCursor(Qt.CursorShape.PointingHandCursor)
        head = QHBoxLayout(self._head)
        head.setContentsMargins(3, 0, 3, 0)
        head.setSpacing(SPACE_CARD)
        self._icon = QLabel(self._head)
        self._icon.setFixedSize(_GLYPH_PX + 3, _GLYPH_PX + 3)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)


        self._spinner = Spinner(_GLYPH_PX, INK_3, self._head)
        self._spinner.hide()
        head.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self._label = QLabel(self._head)
        self._label.setObjectName("stackLabel")
        head.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._count = QLabel(self._head)
        self._count.setObjectName("stackCount")
        head.addWidget(self._count, 1, Qt.AlignmentFlag.AlignVCenter)
        self._chevron = QLabel(self._head)
        self._chevron.setFixedSize(_CHEVRON_PX + 2, _CHEVRON_PX + 2)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self._head.clicked.connect(self.toggle)
        col.addWidget(self._head)

        self._body = QWidget(self)
        self._body_col = QVBoxLayout(self._body)
        self._body_col.setContentsMargins(_GLYPH_PX + SPACE_CARD, 0, 0, SPACE_TIGHT)
        self._body_col.setSpacing(SPACE_TIGHT)
        self._body.setVisible(False)
        self._body.setMaximumHeight(0)
        col.addWidget(self._body)
        self.add(first)



    def add(self, row: QWidget) -> None:
        row.setParent(self._body)
        self._body_col.addWidget(row)
        self.rows.append(row)
        finished = getattr(_card_of(row), "finished", None)
        if finished is not None and hasattr(finished, "connect"):
            finished.connect(self._sync)
        self._sync()

    def cards(self) -> list:
        return [_card_of(row) for row in self.rows]

    @property
    def repeats(self) -> int:
        return sum(int(getattr(row, "repeats", 1) or 1) for row in self.rows)

    def _sync(self) -> None:
        card = _card_of(self.rows[0]) if self.rows else None
        glyph = str(getattr(card, "glyph", "") or "circle")
        colour = getattr(card, "glyph_colour", None)
        self._icon.setPixmap(pixmap_for(self, glyph, _GLYPH_PX,
                                        colour() if callable(colour) else qcolor(INK_2)))
        running = any(getattr(_card_of(row), "ok", None) is None for row in self.rows)
        self._spinner.setVisible(running)
        self._icon.setVisible(not running)
        if running:
            self._spinner.start()
        else:
            self._spinner.stop()
        label = ""
        if card is not None:
            connector = getattr(card, "connector", None) or {}
            label = str(connector.get("name") or "") or str(getattr(card, "_verb", "") or "")
        self._label.setText(label)
        n = self.repeats
        self._count.setText(self.tr("%n calls", "", n) if n != 1 else self.tr("1 call"))
        name = "chevron_down" if self._open else "chevron_right"
        self._chevron.setPixmap(pixmap_for(self, name, _CHEVRON_PX, qcolor(INK_3)))



    def toggle(self) -> None:
        self.set_open(not self._open)

    def set_open(self, open_: bool) -> None:
        open_ = bool(open_)
        if open_ == self._open:
            return
        self._open = open_
        _fold(self._body, open_, self)
        self._sync()

    def is_open(self) -> bool:
        return self._open

    def cleanup(self) -> None:
        anim = self._fold_anim
        self._fold_anim = None
        if anim is not None:
            try:
                anim.stop()
            except (RuntimeError, AttributeError):
                pass
        self._spinner.stop()
        for row in self.rows:
            clean = getattr(row, "cleanup", None)
            if callable(clean):
                clean()

    def to_markdown(self) -> str:
        return "\n".join(row.to_markdown() for row in self.rows)






class ToolGroup(QWidget):
    """``4 tool calls, 2 messages`` with a chevron, the tool rows under it, the changed layers as chips under those."""








    toggled = pyqtSignal(bool)
    layer_action_requested = pyqtSignal(str, str)

    def __init__(self, run_id: str = "", parent=None):
        super().__init__(parent)
        self.run_id = run_id
        self.cards: list[QWidget] = []

        self._items: list[QWidget] = []
        self.tool_calls = 0
        self.messages = 0
        self._folded = False
        self._fold_anim = None
        self._families_shown: list | None = None
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, SPACE_TIGHT)
        col.setSpacing(0)

        self._head = _HoverRow(self)
        self._head.setObjectName("toolGroupHead")
        self._head.setStyleSheet(_GROUP_HEAD_QSS)
        self._head.setFixedHeight(ROW_PX - 1)
        head = QHBoxLayout(self._head)
        head.setContentsMargins(6, 0, 6, 0)
        head.setSpacing(SPACE_CARD)
        self._chevron = QLabel(self._head)
        self._chevron.setFixedSize(_CHEVRON_PX + 2, _CHEVRON_PX + 2)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)


        self._families = QWidget(self._head)
        self._family_row = QHBoxLayout(self._families)
        self._family_row.setContentsMargins(0, 0, 0, 0)
        self._family_row.setSpacing(SPACE_TIGHT)
        head.addWidget(self._families, 0, Qt.AlignmentFlag.AlignVCenter)
        self._title = QLabel(self._head)
        head.addWidget(self._title, 1, Qt.AlignmentFlag.AlignVCenter)
        self._head.clicked.connect(self.toggle)
        col.addWidget(self._head)

        self._body = QWidget(self)
        self._rows = QVBoxLayout(self._body)
        self._rows.setContentsMargins(6, 0, 6, 0)
        self._rows.setSpacing(SPACE_TIGHT)
        col.addWidget(self._body)
        self._changes: LayerLinkRow | None = None
        self._sync()



    def add_card(self, card: QWidget) -> None:
        """One more row."""


        self.cards.append(card)
        key = stack_key(card)
        last = self._items[-1] if self._items else None
        if key and isinstance(last, ToolStack) and last.key == key:
            last.add(card)
        elif key and last is not None and not isinstance(last, ToolStack) \
                and stack_key(last) == key:
            index = self._rows.indexOf(last)
            self._rows.removeWidget(last)
            stack = ToolStack(last, self._body)
            stack.add(card)
            self._rows.insertWidget(max(0, index), stack)
            self._items[-1] = stack
        else:
            card.setParent(self._body)
            index = self._rows.count() - (1 if self._changes is not None else 0)
            self._rows.insertWidget(max(0, index), card)
            self._items.append(card)
        self.tool_calls = sum(c.repeats for c in self.cards)
        self._sync()

    def set_counts(self, tool_calls: int | None = None, messages: int | None = None) -> None:
        if tool_calls is not None:
            self.tool_calls = max(int(tool_calls), sum(c.repeats for c in self.cards))
        if messages is not None:
            self.messages = int(messages)
        self._sync()

    def set_changes(self, layers) -> None:
        """The changed layers as chips under the rows."""
        if self._changes is not None:




            self._rows.removeWidget(self._changes)
            self._changes.hide()
            self._changes.setParent(None)
            self._changes.deleteLater()
            self._changes = None
        items = [x for x in (layers or []) if isinstance(x, dict)]
        if not items:
            return
        self._changes = LayerLinkRow(items, self._body)
        self._changes.setContentsMargins(0, SPACE_CARD, 0, 0)
        self._changes.layer_action_requested.connect(self.layer_action_requested.emit)
        self._rows.addWidget(self._changes)

    def _sync(self) -> None:
        n, m = self.tool_calls, self.messages
        calls = self.tr("%n tools", "", n) if n != 1 else self.tr("1 tool")
        if m:
            msgs = self.tr("%n messages", "", m) if m != 1 else self.tr("1 message")
            self._title.setText(f"{calls} \u00b7 {msgs}")
        else:
            self._title.setText(calls)
        self._sync_families()
        name = "chevron_right" if self._folded else "chevron_down"
        self._chevron.setPixmap(pixmap_for(self, name, _CHEVRON_PX, qcolor(INK_3)))

    def _sync_families(self) -> None:
        """A glyph per family present, in the fixed order connector, plugin, QGIS, so the head keeps its shape as a run goes on."""

        present = {family_of(card) for card in self.cards}
        wanted = [f for f in FAMILY_ORDER if f in present]
        if wanted == getattr(self, "_families_shown", None):
            return
        self._families_shown = wanted
        while self._family_row.count():
            item = self._family_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for family in wanted:
            label = QLabel(self._families)
            label.setFixedSize(_FAMILY_GLYPH_PX + 2, _FAMILY_GLYPH_PX + 2)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setPixmap(pixmap_for(self, FAMILY_GLYPHS.get(family, "circle"),
                                       _FAMILY_GLYPH_PX,
                                       _family_colour(FAMILY_COLOUR_TOKENS.get(family, ""))))
            self._family_row.addWidget(label)
        self._families.setVisible(bool(wanted))



    def toggle(self) -> None:
        self.set_folded(not self._folded)

    def set_folded(self, folded: bool) -> None:
        folded = bool(folded)
        if folded == self._folded:
            return
        self._folded = folded
        _fold(self._body, not folded, self)
        self._sync()
        self.toggled.emit(not folded)

    def is_folded(self) -> bool:
        return self._folded

    def cleanup(self) -> None:
        anim = self._fold_anim
        self._fold_anim = None
        if anim is not None:
            try:
                anim.stop()
            except (RuntimeError, AttributeError):
                pass
        for card in self.cards:
            card.cleanup()

    def to_markdown(self) -> str:
        return "\n".join(card.to_markdown() for card in self.cards)


__all__ = ["ToolGroup", "ToolStack", "family_of", "stack_key"]

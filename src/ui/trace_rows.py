# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rows of a run's trace: one quiet list, the way ChatGPT's Thinking reads."""





















from __future__ import annotations

from html import escape

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QRectF,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.PyQt.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .cards_click import _ClickRow
from .font_scale import scale_qss_font_px
from .icons import pixmap_for, render_pixmap
from .style import (
    FONT_BODY,
    FONT_HINT,
    HOVER,
    INK,
    INK_2,
    INK_3,
    LINE_STRONG,
    MOTION_FADE_UP_MS,
    RADIUS_CHIP,
    SPACE_CARD,
    SPACE_TIGHT,
    accent_color,
    qcolor,
)
from .widgets import Spinner


GLYPH_PX = 13
GLYPH_SLOT_PX = 16

ROW_MIN_PX = 28


_ROW_PAD_X = 6
_ROW_PAD_Y = 2
_ROW_GAP = SPACE_CARD + 2

STEP_INDENT_PX = GLYPH_SLOT_PX + _ROW_GAP

_RISE_PX = 8

_LABEL_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK}; background: transparent; border: none; }}"
)
_MEDIUM_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
)
_SECOND_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)
_HINT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}"
)


def fade_up(widget: QWidget, owner) -> None:
    """The site's ``fade-up``: opacity 0 to 1 and 8 px of rise over 300 ms."""






    layout = widget.layout()
    try:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        margins = layout.contentsMargins() if layout is not None else None
        rise = QVariantAnimation(widget)
        rise.setDuration(MOTION_FADE_UP_MS)
        rise.setEasingCurve(QEasingCurve.Type.OutQuint)
        rise.setStartValue(0.0)
        rise.setEndValue(1.0)

        def _step(value):
            try:
                effect.setOpacity(float(value))
                if margins is not None:
                    lift = int(round(_RISE_PX * (1.0 - float(value))))
                    layout.setContentsMargins(margins.left(), margins.top() + lift,
                                              margins.right(), max(0, margins.bottom() - lift))
            except RuntimeError:
                pass

        def _done():
            try:
                widget.setGraphicsEffect(None)
                if margins is not None:
                    layout.setContentsMargins(margins)
            except RuntimeError:
                pass
            owner.release(rise)

        rise.valueChanged.connect(_step)
        rise.finished.connect(_done)


        widget._fade_anim = rise
        owner.hold(rise)
        rise.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    except (RuntimeError, AttributeError, TypeError):
        try:
            widget.setGraphicsEffect(None)
        except (RuntimeError, AttributeError):
            pass


class HoverRow(_ClickRow):
    """A clickable row that paints the hover step under the pointer."""


    hovered = pyqtSignal(bool)

    def __init__(self, parent=None, radius: int = RADIUS_CHIP):
        super().__init__(parent)
        self._radius = radius
        self._hot = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)

    def is_hot(self) -> bool:
        return self._hot

    def enterEvent(self, event):  # noqa: N802 - Qt override
        self._hot = True
        self.update()
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt override
        self._hot = False
        self.update()
        self.hovered.emit(False)
        super().leaveEvent(event)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        if self._hot and self.cursor().shape() == Qt.CursorShape.PointingHandCursor:
            try:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(HOVER))
                painter.drawRoundedRect(QRectF(self.rect()), self._radius, self._radius)
                painter.end()
            except Exception:  # nosec B110 - noqa: BLE001 - paint must never raise
                pass
        super().paintEvent(event)


class Chevron(QWidget):
    """The chevron of a fold: points down when closed, turns up over the fold's duration when opened, and takes the accent under the pointer."""


    def __init__(self, size: int, parent=None):
        super().__init__(parent)
        self._size = size
        self._angle = 0.0
        self._hot = False
        self._turn: QVariantAnimation | None = None
        self.setFixedSize(size, size)

    def set_open(self, open_: bool, duration_ms: int = 0) -> None:
        target = 180.0 if open_ else 0.0
        self._stop_turn()
        if duration_ms <= 0 or not self.isVisible() or self._angle == target:
            self._angle = target
            self.update()
            return
        turn = QVariantAnimation(self)
        turn.setDuration(duration_ms)
        turn.setEasingCurve(QEasingCurve.Type.OutQuint)
        turn.setStartValue(self._angle)
        turn.setEndValue(target)
        turn.valueChanged.connect(self._on_turn)
        self._turn = turn
        turn.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _on_turn(self, value) -> None:
        try:
            self._angle = float(value)
            self.update()
        except RuntimeError:
            pass

    def _stop_turn(self) -> None:
        turn, self._turn = self._turn, None
        if turn is not None:
            try:
                turn.stop()
            except (RuntimeError, AttributeError):
                pass

    def cleanup(self) -> None:
        self._stop_turn()

    def set_hot(self, hot: bool) -> None:
        self._hot = bool(hot)
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            colour = accent_color() if self._hot else qcolor(INK_3)
            ratio = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
            pixmap = render_pixmap("chevron_down", colour, self._size, ratio)
            half = self._size / 2.0
            painter.translate(half, half)
            painter.rotate(self._angle)
            painter.drawPixmap(QRectF(-half, -half, self._size, self._size), pixmap,
                               QRectF(pixmap.rect()))
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return


def _row_layout(row: QWidget, height: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout(row)
    lay.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y, _ROW_PAD_X, _ROW_PAD_Y)
    lay.setSpacing(_ROW_GAP)
    if height is not None:
        row.setMinimumHeight(height)
    return lay


def _glyph_label(parent, name: str, colour: QColor, size: int = GLYPH_PX) -> QLabel:
    label = QLabel(parent)
    label.setFixedSize(GLYPH_SLOT_PX, GLYPH_SLOT_PX)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setPixmap(pixmap_for(parent, name, size, colour))
    return label






class StepRow(QWidget):
    """One goal of the plan, and under it the work it did, always open."""

    def __init__(self, step_id: str, label: str, count: str = "", parent=None):
        super().__init__(parent)
        self.step_id = step_id
        self.state = "pending"
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._head = QWidget(self)
        row = _row_layout(self._head, ROW_MIN_PX)
        self._icon = QLabel(self._head)
        self._icon.setFixedSize(GLYPH_SLOT_PX, GLYPH_SLOT_PX)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._spinner = Spinner(GLYPH_PX, color=INK_3, parent=self._head)
        row.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self._spinner.hide()
        self._label = QLabel(label, self._head)
        self._label.setObjectName("planStep")
        self._label.setStyleSheet(_MEDIUM_QSS)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setWordWrap(True)
        row.addWidget(self._label, 1, Qt.AlignmentFlag.AlignVCenter)

        self._count = QLabel(count or "", self._head)
        self._count.setObjectName("planStepCount")
        self._count.setStyleSheet(_HINT_QSS)
        self._count.setVisible(bool(count))
        row.addWidget(self._count, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addWidget(self._head)

        self._details = QWidget(self)
        self._detail_col = QVBoxLayout(self._details)
        self._detail_col.setContentsMargins(STEP_INDENT_PX, 0, 0, SPACE_TIGHT)
        self._detail_col.setSpacing(SPACE_TIGHT)
        self._details.hide()
        col.addWidget(self._details)

        self.set_state("pending")



    def add_detail(self, widget: QWidget) -> None:
        widget.setParent(self._details)
        self._detail_col.addWidget(widget)
        self._details.show()

    def take_details(self) -> list:
        """Let go of every activity hung under this step, still alive."""





        taken = []
        while self._detail_col.count():
            item = self._detail_col.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                taken.append(widget)
        self._details.hide()
        return taken

    def set_label(self, label: str, count: str = "") -> None:
        """The same step, reworded by a later plan of the same run."""
        self._label.setText(label)
        self._count.setText(count or "")
        self._count.setVisible(bool(count))

    def detail_count(self) -> int:
        return self._detail_col.count()

    def details(self) -> list:
        return [self._detail_col.itemAt(i).widget() for i in range(self._detail_col.count())]

    def label(self) -> str:
        return self._label.text()



    def set_state(self, state: str) -> None:
        self.state = state
        active = state == "active"
        self._icon.setVisible(not active)
        self._spinner.setVisible(active)
        if active:
            self._spinner.start()
        else:
            self._spinner.stop()




            name, colour = {
                "done": ("check", qcolor(INK_3)),
                "failed": ("close", qcolor(INK_2)),
                "skipped": ("dash", qcolor(INK_3)),
            }.get(state, ("circle", qcolor(LINE_STRONG)))
            self._icon.setPixmap(pixmap_for(self, name, GLYPH_PX, colour))


        self._label.setStyleSheet(_SECOND_QSS if state == "pending" else _MEDIUM_QSS)

    def cleanup(self) -> None:
        self._spinner.stop()


class TraceSteps(QWidget):
    """The plan as the headings of the list: one row per step, no title."""






    changed = pyqtSignal()
    row_added = pyqtSignal(QWidget)


    details_orphaned = pyqtSignal(list)

    def __init__(self, steps=None, parent=None):
        super().__init__(parent)
        self._rows: dict[str, StepRow] = {}
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)
        self.set_steps(steps or [])

    def set_steps(self, steps) -> None:
        """The plan, as rows."""









        if isinstance(steps, (str, bytes)) or not hasattr(steps, "__iter__"):
            steps = []
        steps = [step for step in steps if isinstance(step, dict)]
        kept: dict[str, StepRow] = {}
        order: list[StepRow] = []
        fresh: list[StepRow] = []
        for i, step in enumerate(steps):
            step_id = str(step.get("id") or i)



            if step_id in kept:
                continue
            label = str(step.get("label") or "")
            count = str(step.get("count") or "")
            row = self._rows.pop(step_id, None)
            if row is None:
                row = StepRow(step_id, label, count, self)
                fresh.append(row)
            else:
                row.set_label(label, count)
            row.set_state(str(step.get("state") or "pending"))
            kept[step_id] = row
            order.append(row)
        orphans = []
        for row in self._rows.values():
            orphans += row.take_details()



            row.cleanup()
            self._col.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._rows = kept
        for row in order:
            self._col.addWidget(row)
        if orphans and order:


            for widget in orphans:
                order[-1].add_detail(widget)
            orphans = []
        for row in fresh:
            self.row_added.emit(row)
        if orphans:
            self.details_orphaned.emit(orphans)
        self.changed.emit()

    def update_step(self, step_id: str, state: str) -> None:
        row = self._rows.get(str(step_id))
        if row is not None:
            row.set_state(state)
            self.changed.emit()

    def steps(self) -> list:
        return [{"id": r.step_id, "label": r.label(), "state": r.state} for r in self._rows.values()]

    def rows(self) -> list:
        return list(self._rows.values())

    def active_row(self):
        """The step in progress, else the last one that started, else None."""
        last = None
        for row in self._rows.values():
            if row.state == "active":
                return row
            if row.state != "pending":
                last = row
        return last or (next(iter(self._rows.values())) if self._rows else None)

    def add_detail(self, widget) -> bool:
        """Hang a row under the step it belongs to."""

        row = self.active_row()
        if row is None:
            return False
        row.add_detail(widget)
        return True

    def detail_count(self) -> int:
        return sum(row.detail_count() for row in self._rows.values())

    def active_label(self) -> str:
        last = ""
        for row in self._rows.values():
            if row.state == "active":
                return row.label()
            if row.state != "pending":
                last = row.label()
        return last

    def cleanup(self) -> None:
        for row in self._rows.values():
            row.cleanup()

    def to_markdown(self) -> str:
        lines = [f"**{self.tr('Plan')}**"]
        for row in self._rows.values():
            state = row.state
            mark = "x" if state in ("done", "failed", "skipped") else " "
            suffix = f" ({state})" if state in ("failed", "skipped", "active") else ""
            lines.append(f"- [{mark}] {row.label()}{suffix}")
            for widget in row.details():
                text = getattr(widget, "to_markdown", None)
                if callable(text):
                    lines += [f"  {line}" for line in text().splitlines()]
                elif hasattr(widget, "text") and callable(widget.text):
                    lines.append(f"  - *{widget.text()}*")
        return "\n".join(lines)






class ThoughtRow(QWidget):
    """A thought, whole: a paragraph in the second ink at a line height of 1.5, selectable, and nothing to click."""


    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full = text
        lay = QVBoxLayout(self)
        lay.setContentsMargins(_ROW_PAD_X, _ROW_PAD_Y + 2, _ROW_PAD_X, _ROW_PAD_Y + 2)
        lay.setSpacing(0)
        self._text = QLabel(self)
        self._text.setObjectName("thought")
        self._text.setStyleSheet(_SECOND_QSS)
        self._text.setTextFormat(Qt.TextFormat.RichText)
        self._text.setText(f'<p style="line-height:150%; margin:0">{escape(text)}</p>')
        self._text.setWordWrap(True)
        self._text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self._text)

    def text(self) -> str:
        return self._full


__all__ = ["Chevron", "GLYPH_PX", "GLYPH_SLOT_PX", "HoverRow", "ROW_MIN_PX", "STEP_INDENT_PX",
           "StepRow", "ThoughtRow", "TraceSteps", "fade_up"]

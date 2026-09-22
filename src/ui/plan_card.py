# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

from itertools import islice

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QColor, QFont, QPainter, QPen
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .font_scale import scale_point_size, scale_qss_font_px
from .icons import pixmap_for
from .style import (
    ACCENT_BORDER,
    ACCENT_TINT_ON,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    FONT_MICRO,
    HOVER,
    INK,
    INK_2,
    INK_3,
    LINE,
    LINE_STRONG,
    MOTION_FOLD_MS,
    RADIUS_CARD,
    RADIUS_CONTROL,
    accent_color,
    qcolor,
)
from .trace_rows import Chevron, HoverRow
from .widgets import ChatLabel, ElidedLabel



_MARK_PX = 16
_MARK_SLOT_PX = 20
_MARK_TOP_PX = 3

_TURN_MS = 900
_MAX_STEPS = 100

_TITLE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BASE}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
)


_BOX_QSS = f"QFrame#planBox {{ background: transparent; border: 1px solid {LINE}; border-radius: {RADIUS_CARD}px; }}"
_HINT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}"
)


_STEP_QSS = {
    state: scale_qss_font_px(
        f"QLabel {{ font-size: {FONT_BODY}px; color: {colour};{weight}"
        " background: transparent; border: none; }"
    )
    for state, colour, weight in (
        ("active", INK, " font-weight: 500;"),
        ("pending", INK_2, ""),
        ("done", INK_3, ""),
        ("failed", INK_2, ""),
        ("skipped", INK_3, ""),
    )
}
_STATES = tuple(_STEP_QSS)


class _StepMark(QWidget):


    def __init__(self, number: int, parent=None):
        super().__init__(parent)
        self.number = number
        self.state = "pending"
        self.first = False
        self.last = False
        self._angle = 0.0
        self._turn: QVariantAnimation | None = None
        self.motion = True
        self.setFixedWidth(_MARK_SLOT_PX)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

    def set_state(self, state: str) -> None:
        self.state = state
        if state == "active" and self.motion:
            self._start_turn()
        else:
            self._stop_turn()
        self.update()

    def set_ends(self, first: bool, last: bool) -> None:
        self.first, self.last = first, last
        self.update()



    def _start_turn(self) -> None:
        if self._turn is not None or not self.isVisible():
            return
        turn = QVariantAnimation(self)
        turn.setDuration(_TURN_MS)
        turn.setStartValue(0.0)
        turn.setEndValue(360.0)
        turn.setLoopCount(-1)
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

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self.state == "active" and self.motion:
            self._start_turn()

    def hideEvent(self, event):  # noqa: N802

        self._stop_turn()
        super().hideEvent(event)

    def cleanup(self) -> None:
        self._stop_turn()



    def paintEvent(self, event):  # noqa: N802
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            cx = self.width() / 2.0
            top = float(_MARK_TOP_PX)
            bottom = top + _MARK_PX


            if not self.first:
                painter.fillRect(QRectF(cx - 0.5, 0, 1, top - 2), qcolor(LINE))
            if not self.last:
                painter.fillRect(QRectF(cx - 0.5, bottom + 2, 1, max(0.0, self.height() - bottom - 2)),
                                 qcolor(ACCENT_BORDER if self.state == "done" else LINE))
            ring = QRectF(cx - _MARK_PX / 2.0 + 0.75, top + 0.75, _MARK_PX - 1.5, _MARK_PX - 1.5)
            if self.state == "done":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(qcolor(ACCENT_TINT_ON))
                painter.drawEllipse(ring)
                self._glyph(painter, "lu.check", accent_color(), ring)
            elif self.state in ("failed", "skipped"):
                painter.setPen(QPen(qcolor(LINE_STRONG), 1.2))
                painter.setBrush(qcolor(HOVER))
                painter.drawEllipse(ring)
                name = "lu.x" if self.state == "failed" else "lu.minus"
                self._glyph(painter, name, qcolor(INK_2 if self.state == "failed" else INK_3), ring)
            else:
                active = self.state == "active"
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(qcolor(LINE if active else LINE_STRONG), 1.2))
                painter.drawEllipse(ring)
                if active:
                    pen = QPen(accent_color(), 1.6)
                    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                    painter.setPen(pen)

                    painter.drawArc(ring, int((90 - self._angle) * 16), -100 * 16)
                font = QFont(self.font())
                font.setPixelSize(scale_point_size(FONT_MICRO))
                font.setWeight(QFont.Weight.Medium if active else QFont.Weight.Normal)
                painter.setFont(font)
                painter.setPen(qcolor(INK if active else INK_3))
                painter.drawText(ring, int(Qt.AlignmentFlag.AlignCenter), str(self.number))
            painter.end()
        except Exception:  # noqa: BLE001
            return

    def _glyph(self, painter: QPainter, name: str, colour: QColor, ring: QRectF) -> None:
        size = 10
        pixmap = pixmap_for(self, name, size, colour)
        painter.drawPixmap(QRectF(ring.center() - QPointF(size / 2.0, size / 2.0),
                                  QRectF(0, 0, size, size).size()),
                           pixmap, QRectF(pixmap.rect()))


class _StepRow(QWidget):


    def __init__(self, step_id: str, number: int, label: str, count: str = "", parent=None):
        super().__init__(parent)
        self.step_id = step_id
        self.state = "pending"
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.mark = _StepMark(number, self)
        row.addWidget(self.mark)
        self._label = ChatLabel(label, self, wrap=True)
        self._label.setObjectName("planStep")

        self._label.setContentsMargins(0, 2, 0, 12)
        row.addWidget(self._label, 1, Qt.AlignmentFlag.AlignTop)

        self._count = QLabel(count or "", self)
        self._count.setObjectName("planStepCount")
        self._count.setStyleSheet(_HINT_QSS)
        self._count.setContentsMargins(0, 3, 0, 0)
        self._count.setVisible(bool(count))
        row.addWidget(self._count, 0, Qt.AlignmentFlag.AlignTop)
        self.set_state("pending")

    def set_label(self, label: str, count: str = "") -> None:

        self._label.setText(str(label or "")[:240])
        self._count.setText(str(count or "")[:80])
        self._count.setVisible(bool(count))

    def set_state(self, state: str) -> None:
        state = state if state in _STATES else "pending"
        self.state = state
        self.mark.set_state(state)
        self._label.setStyleSheet(_STEP_QSS[state])

    def label(self) -> str:
        return self._label.text()

    def cleanup(self) -> None:
        self.mark.cleanup()


class PlanCard(QWidget):


    toggled = pyqtSignal(bool)

    def __init__(self, run_id: str, parent=None, reduced_motion=None):
        super().__init__(parent)
        self.run_id = run_id
        self.status = "running"
        self._rows: dict[str, _StepRow] = {}
        self._reduced_motion = reduced_motion
        self._expanded = True

        self._manual: bool | None = None
        self.setObjectName("planCard")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(0)
        frame = QFrame(self)
        frame.setObjectName("planBox")
        frame.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        frame.setStyleSheet(_BOX_QSS)
        outer.addWidget(frame)
        col = QVBoxLayout(frame)
        col.setContentsMargins(4, 4, 4, 2)
        col.setSpacing(0)

        self._head = HoverRow(frame, radius=RADIUS_CONTROL)
        head = QHBoxLayout(self._head)
        head.setContentsMargins(6, 4, 6, 4)
        head.setSpacing(8)
        icon = QLabel(self._head)
        icon.setFixedSize(16, 16)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(pixmap_for(self, "lu.list-checks", 14, qcolor(INK_2)))
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._title = QLabel(self.tr("Plan"), self._head)
        self._title.setObjectName("planTitle")
        self._title.setStyleSheet(_TITLE_QSS)
        head.addWidget(self._title, 0, Qt.AlignmentFlag.AlignVCenter)

        self._current = ElidedLabel("", self._head)
        self._current.setObjectName("planCurrent")
        self._current.setStyleSheet(_HINT_QSS)
        self._current.hide()
        head.addWidget(self._current, 1, Qt.AlignmentFlag.AlignVCenter)
        self._spacer = QWidget(self._head)
        self._spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        head.addWidget(self._spacer, 1)
        self._counter = QLabel("", self._head)
        self._counter.setObjectName("planCounter")
        self._counter.setStyleSheet(_HINT_QSS)
        head.addWidget(self._counter, 0, Qt.AlignmentFlag.AlignVCenter)
        self._chevron = Chevron(12, self._head)
        head.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self._head.clicked.connect(self._on_head_clicked)
        self._head.hovered.connect(self._chevron.set_hot)
        col.addWidget(self._head)

        self._body = QWidget(frame)
        self._steps = QVBoxLayout(self._body)
        self._steps.setContentsMargins(8, 6, 8, 0)
        self._steps.setSpacing(0)
        col.addWidget(self._body)
        self._chevron.set_open(True)



    def set_steps(self, steps) -> None:







        if isinstance(steps, (str, bytes)) or not hasattr(steps, "__iter__"):
            steps = []
        steps = [step for step in islice(steps, _MAX_STEPS) if isinstance(step, dict)]
        kept: dict[str, _StepRow] = {}
        for i, step in enumerate(steps):
            step_id = str(step.get("id") or i)

            if step_id in kept:
                continue
            label = str(step.get("label") or "")[:240]
            count = str(step.get("count") or "")[:80]
            row = self._rows.pop(step_id, None)
            if row is None:
                row = _StepRow(step_id, len(kept) + 1, label, count, self._body)
                row.mark.motion = not self._still()
            else:
                row.set_label(label, count)
                row.mark.number = len(kept) + 1
            row.set_state(str(step.get("state") or "pending"))
            kept[step_id] = row
        for row in self._rows.values():



            row.cleanup()
            self._steps.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self._rows = kept
        for row in kept.values():
            self._steps.addWidget(row)
        self._sync()

    def update_step(self, step_id: str, state: str) -> None:
        row = self._rows.get(str(step_id))
        if row is not None:
            row.set_state(state)
            self._sync()

    def steps(self) -> list:
        return [{"id": r.step_id, "label": r.label(), "state": r.state} for r in self._rows.values()]

    def active_label(self) -> str:

        last = ""
        for row in self._rows.values():
            if row.state == "active":
                return row.label()
            if row.state != "pending":
                last = row.label()
        return last

    def _sync(self) -> None:
        rows = list(self._rows.values())
        for i, row in enumerate(rows):
            row.mark.set_ends(i == 0, i == len(rows) - 1)
        total = len(rows)
        done = sum(1 for row in rows if row.state == "done")
        self._counter.setText(self.tr("{done} of {total}").format(done=done, total=total) if total else "")
        self._sync_head()

    def _sync_head(self) -> None:
        current = self.active_label() if self.status == "running" and not self._expanded else ""
        self._current.setText(current)
        self._current.setVisible(bool(current))
        self._spacer.setVisible(not current)

    def _still(self) -> bool:
        return callable(self._reduced_motion) and bool(self._reduced_motion())



    def finish(self, status: str) -> None:




        if self.status != "running":
            return
        self.status = status or "done"
        for row in self._rows.values():
            if row.state == "active":
                row.set_state("done" if self.status == "done" else "skipped")
            elif row.state == "pending":
                row.set_state("skipped")
        self._sync()
        if self._manual is not True:
            self.set_expanded(False)



    def _on_head_clicked(self) -> None:
        self._manual = not self._expanded
        self.set_expanded(self._manual)

    def toggle(self) -> None:
        self._on_head_clicked()

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self._expanded = expanded
        self._body.setVisible(expanded)
        self._chevron.set_open(expanded, 0 if self._still() else MOTION_FOLD_MS)
        self._sync_head()
        self.toggled.emit(expanded)

    def is_expanded(self) -> bool:
        return self._expanded

    def cleanup(self) -> None:
        self._chevron.cleanup()
        for row in self._rows.values():
            row.cleanup()

    def to_markdown(self) -> str:
        lines = [f"**{self.tr('Plan')}**"]
        for row in self._rows.values():
            state = row.state
            mark = "x" if state in ("done", "failed", "skipped") else " "
            suffix = f" ({state})" if state in ("failed", "skipped", "active") else ""
            lines.append(f"- [{mark}] {row.label()}{suffix}")
        return "\n".join(lines)


__all__ = ["PlanCard"]

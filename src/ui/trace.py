# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""An activity block: what the agent did between two things it said."""






















from __future__ import annotations

from qgis.PyQt.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    Qt,
    QVariantAnimation,
    pyqtSignal,
)
from qgis.PyQt.QtGui import QPainter
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .card_base import format_duration
from .cards_tool import ToolCard
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .loader import ElapsedClock, ShimmerLabel
from .style import (
    FONT_BASE,
    FONT_BODY,
    INK_2,
    INK_3,
    LINE,
    RADIUS_CONTROL,
    RED,
    ROW_PX,
    SPACE_TIGHT,
    qcolor,
)
from .trace_rows import STEP_INDENT_PX, Chevron, HoverRow, ThoughtRow, TraceSteps, fade_up
from .trace_tools import ActivityRow
from .widgets import ElidedLabel




_FOLD_MS = 300
_UNBOUNDED = 16777215

_SPARK_PX = 14
_CHEVRON_PX = 12

_HEAD_MARGINS = (6, 4, 6, 4)
_HEAD_GAP = 8

_BODY_INDENT = 22
_RAIL_X = 8
_BODY_PAD_Y = 4

_HEAD_SOURCES = 2


_TRACE_TITLE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BASE}px; color: {INK_2}; font-weight: 500;"
    " background: transparent; border: none; }"
)

_NOTE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)


class _Body(QWidget):
    """The rows, with the rail painted at their left."""

    def paintEvent(self, event):  # noqa: N802 - Qt override
        try:
            painter = QPainter(self)
            painter.fillRect(_RAIL_X, _BODY_PAD_Y, 1, max(0, self.height() - 2 * _BODY_PAD_Y),
                             qcolor(LINE))
            painter.end()
        except Exception:  # noqa: BLE001 - paint must never raise
            return


class RunTrace(QWidget):
    """One activity block of a run: live under the text, folded once closed."""

    toggled = pyqtSignal(bool)

    def __init__(self, run_id: str, parent=None, started: float | None = None,
                 reduced_motion=None):
        super().__init__(parent)
        self.run_id = run_id
        self.plan: TraceSteps | None = None
        self.tools: list[ToolCard] = []


        self.status = "running"
        self.duration_s: float | None = None
        self._live = started is not None
        self._reduced_motion = reduced_motion
        self._latest = ""
        self._expanded = True
        self._roll_anim: QVariantAnimation | None = None
        self._anims: set = set()
        self.setObjectName("runTrace")
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._head = HoverRow(self, radius=RADIUS_CONTROL)
        self._head.setMinimumHeight(ROW_PX)
        head = QHBoxLayout(self._head)
        head.setContentsMargins(*_HEAD_MARGINS)
        head.setSpacing(_HEAD_GAP)
        self._icon = QLabel(self._head)
        self._icon.setFixedSize(_SPARK_PX + 2, _SPARK_PX + 2)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setPixmap(pixmap_for(self, "spark", _SPARK_PX, qcolor(INK_3)))
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)

        self._live_text = ShimmerLabel("", self._head)
        self._live_text.setObjectName("traceLive")
        head.addWidget(self._live_text, 0, Qt.AlignmentFlag.AlignVCenter)




        self._title = ElidedLabel("", self._head)
        self._title.setObjectName("traceTitle")
        self._title.setStyleSheet(_TRACE_TITLE_QSS)
        self._title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._title.hide()
        head.addWidget(self._title, 0, Qt.AlignmentFlag.AlignVCenter)
        self._clock = ElapsedClock(started, self._head)
        head.addWidget(self._clock, 0, Qt.AlignmentFlag.AlignVCenter)
        self._chevron = Chevron(_CHEVRON_PX, self._head)
        head.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        self._head.clicked.connect(self.toggle)

        self._head.hovered.connect(self._chevron.set_hot)
        col.addWidget(self._head)

        self._body = _Body(self)
        self._body_col = QVBoxLayout(self._body)
        self._body_col.setContentsMargins(_BODY_INDENT, _BODY_PAD_Y, 0, _BODY_PAD_Y)
        self._body_col.setSpacing(SPACE_TIGHT)
        col.addWidget(self._body)

        if not self._live:
            self._clock.stop()
            self._clock.hide()
        self._sync_head()
        self.set_expanded(False)



    def set_plan(self, steps) -> TraceSteps:




        if isinstance(steps, (str, bytes)) or not hasattr(steps, "__iter__"):
            steps = []
        if self.plan is None:
            self.plan = TraceSteps(None, self._body)
            self.plan.changed.connect(self._on_plan_changed)
            self.plan.row_added.connect(self._fade_up)
            self.plan.details_orphaned.connect(self._readopt_rows)
            self.plan.set_steps(list(steps or []))
            self._body_col.insertWidget(0, self.plan)
            self._adopt_loose_rows()
        else:
            self.plan.set_steps(list(steps or []))
        self._sync_head()
        return self.plan

    def _on_plan_changed(self) -> None:


        self._latest = ""
        self._sync_head()

    def add_tool(self, card: ToolCard) -> None:
        """One more call."""

        self.tools.append(card)
        last = self._last_row()
        key = card.stack_key()
        if key and isinstance(last, ActivityRow) and last.key == key:
            last.add(card)
            row = last
        else:
            row = ActivityRow(card, self._body)
            self._place(row)
        self._latest = row.line()
        self._sync_head()

    def set_activity(self, text: str) -> None:
        """What the agent is on right now, in the server's own words: the status line of the run, shown in this head while the block is live."""

        text = " ".join(str(text or "").split()).rstrip(". …")


        if text and "{" not in text:
            self._latest = text
            self._sync_head()

    def set_run_counts(self, tool_calls: int, messages: int) -> None:
        """Kept for the panel: the head no longer counts, it names."""

    def _last_row(self):
        """The row at the bottom of the list, wherever it hangs."""
        if self.plan is not None:
            step = self.plan.active_row()
            if step is not None and step.detail_count():
                return step.details()[-1]
        count = self._body_col.count()
        for i in range(count - 1, -1, -1):
            widget = self._body_col.itemAt(i).widget()
            if widget is not None and widget is not self.plan:
                return widget
        return None

    def _readopt_rows(self, widgets) -> None:
        """Activity rows a plan update left without a step, put back."""




        for widget in list(widgets or []):
            if widget is None:
                continue
            widget.setParent(self._body)
            self._body_col.addWidget(widget)
            self._indent(widget)
            widget.show()

    def _adopt_loose_rows(self) -> None:
        """What the agent did before the plan arrived belongs to its first step: the block shows goals, not a preamble."""

        loose = []
        for i in range(self._body_col.count() - 1, 0, -1):
            widget = self._body_col.itemAt(i).widget()
            if widget is not None and widget is not self.plan:
                self._body_col.takeAt(i)
                loose.insert(0, widget)
        for widget in loose:
            if not self.plan.add_detail(widget):
                self._body_col.addWidget(widget)

    def _place(self, widget: QWidget) -> None:
        """Under the step it belongs to, else at the block's own level."""
        if self.plan is not None and self.plan.add_detail(widget):
            self._fade_up(widget)
            return
        widget.setParent(self._body)
        self._body_col.addWidget(widget)
        if self.plan is not None:
            self._indent(widget)
        self._fade_up(widget)

    @staticmethod
    def _indent(widget: QWidget) -> None:
        """Under a plan, a row at the block's level sits one step in, level with the work of the steps."""

        layout = widget.layout()
        if layout is None:
            return
        margins = layout.contentsMargins()
        layout.setContentsMargins(STEP_INDENT_PX, margins.top(), margins.right(), margins.bottom())

    def add_note(self, text: str, failed: bool = False) -> None:
        """One muted line that is not a tool call: a skipped action."""
        row = QWidget(self._body)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(_HEAD_GAP)
        icon = QLabel(row)
        icon.setFixedSize(_SPARK_PX + 2, _SPARK_PX + 2)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(pixmap_for(row, "dash", 11, qcolor(RED) if failed else qcolor(INK_3)))
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        label = QLabel(text, row)
        label.setObjectName("thought")
        label.setStyleSheet(_NOTE_QSS)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        lay.addWidget(label, 1)
        self._place(row)

    def add_narration(self, text: str) -> None:
        """A paragraph the agent wrote inside the block: only a chat saved by an older build carries one; a live run keeps its text in the chat."""

        text = " ".join((text or "").split())
        if not text:
            return
        self._place(ThoughtRow(text, self._body))

    def last_tool(self) -> ToolCard | None:
        return self.tools[-1] if self.tools else None

    def rows(self) -> list:
        """Every activity row of the block, in order."""
        found = []
        if self.plan is not None:
            for step in self.plan.rows():
                found += [w for w in step.details() if isinstance(w, ActivityRow)]
        for i in range(self._body_col.count()):
            widget = self._body_col.itemAt(i).widget()
            if isinstance(widget, ActivityRow):
                found.append(widget)
        return found

    def step_count(self) -> int:
        """Tool calls, counted with their repeats; the plan when none ran."""
        count = sum(card.repeats for card in self.tools)
        if count == 0 and self.plan is not None:
            count = len(self.plan.steps())
        return count

    def failed_count(self) -> int:
        return sum(1 for card in self.tools if card.ok is False)

    def is_empty(self) -> bool:
        """Nothing to show: no plan, no call and no note."""
        return self.plan is None and not self.tools and self._body_col.count() == 0

    def is_live(self) -> bool:
        return self.status == "running"



    def hold(self, anim) -> None:
        """Keep a row's fade so ``cleanup`` can stop it before the row goes."""
        self._anims.add(anim)

    def release(self, anim) -> None:
        self._anims.discard(anim)

    def _fade_up(self, widget: QWidget) -> None:
        """A row arriving in an open block fades up; one arriving while the block rolls, or into a folded one, is simply there when the reader opens."""



        if self.isVisible() and self._expanded and self._roll_anim is None:
            fade_up(widget, self)



    def running_title(self) -> str:
        """What the agent is on right now: the step in progress when the block holds the plan, else the latest event (the server's status line, a."""


        if self.plan is not None:
            label = self.plan.active_label()
            if label:
                return label
        if self._latest:
            return self._latest
        return self.tr("Thinking")

    def _sync_head(self) -> None:
        live = self.status == "running"
        if live:
            self._live_text.setText(self.running_title())
            motion = not (callable(self._reduced_motion) and self._reduced_motion())
            self._live_text.set_motion(motion)
            self._live_text.start()
        else:
            self._live_text.stop()
            self._title.setText(self.head_line())
        self._live_text.setVisible(live)
        self._title.setVisible(not live)

    def head_line(self) -> str:
        """What the block came to, once closed: the sources it used (``OpenStreetMap, Read features +2``), ``3 steps``, ``Failed``, ``Stopped``."""


        if self.status == "failed":
            failed = self.failed_count()
            return self.tr("%n failed", "", failed) if failed > 1 else self.tr("Failed")
        if self.status == "cancelled":
            return self.tr("Stopped")
        if self.plan is not None and self.plan.steps():
            count = len(self.plan.steps())
            return self.tr("%n steps", "", count) if count != 1 else self.tr("1 step")
        rows = self.rows()
        if len(rows) == 1:
            return rows[0].line()
        names = []
        for row in rows:
            label = row.label()
            if label and label not in names:
                names.append(label)
        if names:
            head = ", ".join(names[:_HEAD_SOURCES])
            rest = len(names) - _HEAD_SOURCES
            return f"{head} +{rest}" if rest > 0 else head
        return self.tr("Done")



    def close(self) -> None:
        """The run went on to say something: the block is over, the run is not."""






        if self.status != "running":
            return
        if self.plan is not None and any(
                step["state"] in ("pending", "active") for step in self.plan.steps()):
            self._latest = ""
            self._sync_head()
            self.set_expanded(False)
            return
        self.status = "closed"
        self.duration_s = self._clock.elapsed() if self._live else None
        self._clock.freeze(self.duration_s)
        self._sync_head()
        self.set_expanded(False)

    def finish(self, status: str, duration_s=None) -> None:
        """The run ended: close the plan, write the head line, fold."""





        if self.status != "running":
            return
        was_live = self.status == "running"
        self.status = status or "done"
        if self.plan is not None:





            for step in self.plan.steps():
                if step["state"] == "active":
                    self.plan.update_step(step["id"], "done" if self.status == "done" else "skipped")
                elif step["state"] == "pending":
                    self.plan.update_step(step["id"], "skipped")
        if was_live:
            if self._live:
                self.duration_s = self._clock.elapsed()
            elif duration_s is not None:
                self.duration_s = duration_s
            self._clock.freeze(self.duration_s)


        if self.status == "failed":
            self._icon.setPixmap(pixmap_for(self, "close", _SPARK_PX, qcolor(RED)))
        self._sync_head()
        self.set_expanded(False)



    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        """Open or close the block, rolling rather than jumping."""






        expanded = bool(expanded)
        was, self._expanded = self._expanded, expanded
        if was == expanded or not self.isVisible():
            self._chevron.set_open(expanded)
            self._body.setVisible(expanded)
            self._body.setMaximumHeight(_UNBOUNDED if expanded else 0)
            self.toggled.emit(expanded)
            return
        self._chevron.set_open(expanded, _FOLD_MS)
        self._roll(expanded)
        self.toggled.emit(expanded)

    def _roll(self, open_it: bool) -> None:
        """Animate the body's height between 0 and what it asks for."""










        width = self._body.width() or self.width()
        target = max(0, self._body.sizeHint().height())
        if self._body.hasHeightForWidth() and width > 0:
            target = max(target, self._body.heightForWidth(width))
        start = self._body.height() if self._body.isVisible() else 0





        self._stop_roll()
        if open_it:
            self._body.setMaximumHeight(0)
            self._body.setVisible(True)
        roll = QVariantAnimation(self)
        roll.setDuration(_FOLD_MS)
        roll.setEasingCurve(QEasingCurve.Type.OutQuint)
        roll.setStartValue(float(start if open_it else max(start, target)))
        roll.setEndValue(float(target if open_it else 0))

        def _step(value):
            try:
                self._body.setMaximumHeight(max(0, int(value)))
            except RuntimeError:
                pass

        roll.valueChanged.connect(_step)
        roll.finished.connect(lambda: self._roll_done(open_it))
        self._roll_anim = roll
        roll.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _stop_roll(self) -> None:
        """Stop a fold still running, so it cannot outlive what it animates."""
        roll = self._roll_anim
        self._roll_anim = None
        if roll is None:
            return
        try:
            roll.stop()
        except (RuntimeError, AttributeError):
            pass


        self._roll_done(self._expanded)

    def cleanup(self) -> None:
        """Called before the trace is removed from the list."""
        self._stop_roll()
        for anim in list(self._anims):
            try:
                anim.stop()
            except (RuntimeError, AttributeError):
                pass
        self._anims.clear()
        self._chevron.cleanup()
        self._live_text.stop()
        self._clock.stop()
        for row in self.rows():
            row.cleanup()
        if self.plan is not None:
            self.plan.cleanup()

    def _roll_done(self, open_it: bool) -> None:
        try:
            self._body.setVisible(open_it)

            self._body.setMaximumHeight(_UNBOUNDED if open_it else 0)
        except RuntimeError:
            pass

    def is_expanded(self) -> bool:
        return self._expanded

    def to_markdown(self) -> str:
        """The plan with the work of each step under it, then whatever happened outside a step."""

        parts = []
        if self.plan is not None:
            parts.append(self.plan.to_markdown())
        for i in range(self._body_col.count()):
            widget = self._body_col.itemAt(i).widget()
            if widget is None or widget is self.plan:
                continue
            if isinstance(widget, (ToolCard, ActivityRow)):
                parts.append(widget.to_markdown())
            elif isinstance(widget, ThoughtRow):
                parts.append(f"- *{widget.text()}*")
        return "\n".join(parts)


class RunFootnote(QLabel):
    """The one muted line under an answer whose run had no trace: ``3.3 s``."""

    def __init__(self, duration_s=None, parent=None):
        super().__init__(parent)
        self.setObjectName("traceHead")
        self.setContentsMargins(2, 0, 2, 0)
        self.setText(format_duration(duration_s) if duration_s is not None else "")
        self.setVisible(bool(self.text()))

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




































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
from .trace_rows import Chevron, HoverRow, ThoughtRow, fade_up
from .trace_tools import ActivityRow
from .widgets import ChatLabel, ElidedLabel




_FOLD_MS = 300
_UNBOUNDED = 16777215

_SPARK_PX = 14
_CHEVRON_PX = 12

_HEAD_MARGINS = (6, 4, 6, 4)
_HEAD_GAP = 8

_BODY_INDENT = 22
_RAIL_X = 8
_BODY_PAD_Y = 4


_LIVE_ROWS = 4


_TRACE_TITLE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BASE}px; color: {INK_2}; font-weight: 500;"
    " background: transparent; border: none; }"
)

_NOTE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)


class _Body(QWidget):


    def paintEvent(self, event):  # noqa: N802
        try:
            painter = QPainter(self)
            painter.fillRect(_RAIL_X, _BODY_PAD_Y, 1, max(0, self.height() - 2 * _BODY_PAD_Y),
                             qcolor(LINE))
            painter.end()
        except Exception:  # noqa: BLE001
            return


class RunTrace(QWidget):


    toggled = pyqtSignal(bool)

    def __init__(self, run_id: str, parent=None, started: float | None = None,
                 reduced_motion=None):
        super().__init__(parent)
        self.run_id = run_id
        self.tools: list[ToolCard] = []


        self.status = "running"
        self.duration_s: float | None = None
        self._live = started is not None
        self._reduced_motion = reduced_motion
        self._latest = ""
        self._expanded = True

        self._manual: bool | None = None

        self._show_all = False
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
        self._icon.setPixmap(pixmap_for(self, "lu.sparkle", _SPARK_PX,
                                        qcolor(INK_2 if self._live else INK_3)))
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
        self._head.clicked.connect(self._on_head_clicked)

        self._head.hovered.connect(self._chevron.set_hot)
        col.addWidget(self._head)

        self._body = _Body(self)
        self._body_col = QVBoxLayout(self._body)
        self._body_col.setContentsMargins(_BODY_INDENT, _BODY_PAD_Y, 0, _BODY_PAD_Y)
        self._body_col.setSpacing(SPACE_TIGHT)

        self._earlier = HoverRow(self._body)
        self._earlier.setObjectName("traceEarlier")
        self._earlier.setCursor(Qt.CursorShape.PointingHandCursor)
        self._earlier.setFixedHeight(ROW_PX - 4)
        earlier = QHBoxLayout(self._earlier)
        earlier.setContentsMargins(6, 0, 6, 0)
        self._earlier_label = QLabel(self._earlier)
        self._earlier_label.setStyleSheet(_NOTE_QSS)
        earlier.addWidget(self._earlier_label)
        earlier.addStretch(1)
        self._earlier.clicked.connect(self._on_earlier_clicked)
        self._earlier.hide()
        self._body_col.addWidget(self._earlier)
        col.addWidget(self._body)

        if not self._live:
            self._clock.stop()
            self._clock.hide()
        self._sync_head()
        self.set_expanded(False)



    def add_tool(self, card: ToolCard) -> None:


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
        self._grew()

    def set_activity(self, text: str) -> None:


        text = " ".join(str(text or "").split()).rstrip(". …")


        if text and "{" not in text:
            self._latest = text
            self._sync_head()

    def _last_row(self):

        lines = self._lines()
        return lines[-1] if lines else None

    def _place(self, widget: QWidget) -> None:
        widget.setParent(self._body)
        self._body_col.addWidget(widget)
        self._fade_up(widget)

    def add_note(self, text: str, failed: bool = False) -> None:

        row = QWidget(self._body)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(_HEAD_GAP)
        icon = QLabel(row)
        icon.setFixedSize(_SPARK_PX + 2, _SPARK_PX + 2)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setPixmap(pixmap_for(row, "dash", 11, qcolor(RED) if failed else qcolor(INK_3)))
        lay.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        label = ChatLabel(text, row, wrap=True)
        label.setObjectName("thought")
        label.setStyleSheet(_NOTE_QSS)
        lay.addWidget(label, 1)
        self._place(row)
        self._grew()

    def add_narration(self, text: str) -> None:


        text = " ".join((text or "").split())
        if not text:
            return
        self._place(ThoughtRow(text, self._body))

    def last_tool(self) -> ToolCard | None:
        return self.tools[-1] if self.tools else None

    def rows(self) -> list:

        return [widget for widget in self._lines() if isinstance(widget, ActivityRow)]

    def step_count(self) -> int:

        return sum(card.repeats for card in self.tools)

    def failed_count(self) -> int:
        return sum(1 for card in self.tools
                   if card.ok is False and getattr(card, "ended", "") not in ("denied", "stopped"))

    def is_empty(self) -> bool:

        return not self.tools and not self._lines()

    def is_live(self) -> bool:
        return self.status == "running"



    def _lines(self) -> list:


        found = []
        for i in range(self._body_col.count()):
            widget = self._body_col.itemAt(i).widget()
            if widget is not None and widget is not self._earlier:
                found.append(widget)
        return found

    def _grew(self) -> None:


        self._trim()
        if self.status == "running" and self._live and self._manual is None and not self._expanded:
            self.set_expanded(True)

    def _trim(self) -> None:



        live = self.status == "running" and self._live and not self._show_all
        lines = self._lines()

        hidden = len(lines) - _LIVE_ROWS if live and len(lines) > _LIVE_ROWS + 1 else 0
        for i, widget in enumerate(lines):
            widget.setVisible(i >= hidden)
        if hidden:


            self._earlier_label.setText(
                self.tr("%n earlier actions", "", hidden))
        self._earlier.setVisible(bool(hidden))

    def _on_earlier_clicked(self) -> None:
        self._show_all = True
        self._trim()

    def _on_head_clicked(self) -> None:
        self._manual = not self._expanded
        self.set_expanded(self._manual)



    def hold(self, anim) -> None:

        self._anims.add(anim)

    def release(self, anim) -> None:
        self._anims.discard(anim)

    def _fade_up(self, widget: QWidget) -> None:




        if self.isVisible() and self._expanded and self._roll_anim is None:
            fade_up(widget, self)



    def running_title(self) -> str:


        if self._latest:
            return self._latest
        return self.tr("Thinking")

    def _sync_head(self) -> None:
        live = self.status == "running"


        if self.status == "failed":
            self._icon.setPixmap(pixmap_for(self, "lu.x", _SPARK_PX, qcolor(RED)))
        else:
            self._icon.setPixmap(pixmap_for(self, "lu.sparkle", _SPARK_PX,
                                            qcolor(INK_2 if live and self._live else INK_3)))

        self._clock.setVisible(live and self._live)
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








        took = format_duration(self.duration_s) if self.duration_s else ""
        if self.status == "failed":
            return self.tr("Failed after {time}").format(time=took) if took else self.tr("Failed")
        if self.status == "cancelled":
            return self.tr("Stopped after {time}").format(time=took) if took else self.tr("Stopped")
        rows = self.rows()
        if len(rows) == 1:
            return rows[0].line()
        if rows:
            names = list(dict.fromkeys(row.label() for row in rows if row.label()))
            return " \u00b7 ".join(names)
        if took:
            return self.tr("Thought for {time}").format(time=took)
        return self.tr("Done")



    def close(self) -> None:



        if self.status != "running":
            return
        self.status = "closed"
        self.duration_s = self._clock.elapsed() if self._live else None
        self._clock.freeze(self.duration_s)
        self._sync_head()
        self._trim()
        self._auto_fold()

    def finish(self, status: str, duration_s=None) -> None:






        if self.status != "running":
            return
        was_live = self.status == "running"
        self.status = status or "done"
        if was_live:
            if self._live:
                self.duration_s = self._clock.elapsed()
            elif duration_s is not None:
                self.duration_s = duration_s
            self._clock.freeze(self.duration_s)



        self._sync_head()
        self._trim()
        self._auto_fold()

    def _auto_fold(self) -> None:


        if self._manual is not True:
            self.set_expanded(False)

    def reveal_failure(self) -> bool:




        failed = [row for row in self.rows() if row.failed()]
        if not failed:
            return False
        failed[-1].set_open(True)
        self.set_expanded(True)
        return True



    def toggle(self) -> None:
        self._on_head_clicked()

    def set_expanded(self, expanded: bool) -> None:







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

    def _roll_done(self, open_it: bool) -> None:
        try:
            self._body.setVisible(open_it)

            self._body.setMaximumHeight(_UNBOUNDED if open_it else 0)
        except RuntimeError:
            pass

    def is_expanded(self) -> bool:
        return self._expanded

    def to_markdown(self) -> str:

        parts = []
        for widget in self._lines():
            if isinstance(widget, (ToolCard, ActivityRow)):
                parts.append(widget.to_markdown())
            elif isinstance(widget, ThoughtRow):
                parts.append(f"- *{widget.text()}*")
        return "\n".join(parts)


class RunFootnote(QLabel):


    def __init__(self, duration_s=None, parent=None):
        super().__init__(parent)
        self.setObjectName("traceHead")
        self.setContentsMargins(2, 0, 2, 0)
        self.setText(format_duration(duration_s) if duration_s is not None else "")
        self.setVisible(bool(self.text()))

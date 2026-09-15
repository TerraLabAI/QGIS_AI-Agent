# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The rows of a run's trace: the tool chips."""

























from __future__ import annotations

import re
import time

from qgis.PyQt.QtCore import (
    Qt,
    QTimer,
    pyqtSignal,
)
from qgis.PyQt.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .card_base import (
    _FADE_UP_WINDOW_S,
    _args_text,
    fade_up,
    format_duration,
    humanise_tool_name,
    stop_fade_up,
)
from .code_block import CodeBlock
from .font_scale import scale_qss_font_px
from .icons import pixmap_for
from .shared import connector_for_call
from .style import (
    _BTN_QUIET,
    ACCENT_INK,
    FONT_BODY,
    FONT_HINT,
    GREEN,
    HOVER,
    INK,
    INK_2,
    INK_3,
    ORANGE,
    RADIUS_CONTROL,
    RED,
    ROW_PX,
    SPACE_CARD,
    SPACE_TIGHT,
    qcolor,
    repolish,
)
from .tool_describe import (
    EMPTY_SUMMARY_RE,
    _with_layer_names,
    call_sentence,
    call_subject,
    check_warning,
    describe_tool_call,
    describe_tool_parts,
    group_number,
    outcome_sentence,
    result_facts,
    source_text,
    tool_glyph,
)
from .tool_rows import _HoverRow, _MonoChip
from .transcript import fence
from .widgets import ElidedLabel, FlowLayout, Spinner



SCRIPT_PREAMBLE = (
    "try:\n"
    "    iface\n"
    "except NameError:\n"
    "    from qgis.utils import iface\n"
)
_CODE_TOOLS = ("execute_code", "run_code", "run_python", "python")

_COPIED_MS = 1500



_EMPTY_SUMMARY_RE = EMPTY_SUMMARY_RE



_CANCELLED_RE = re.compile(r"^\s*CANCELLED\b")



_GLYPH_SLOT_PX = 16
_GLYPH_PX = 13
_CHEVRON_PX = 12
_ROW_GAP_PX = 8
_ROW_PAD_PX = 3



_TOOL_LINE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; font-weight: 500; color: {INK};"
    " background: transparent; border: none; }"
    f'QLabel[failed="true"] {{ color: {INK_2}; }}'
)


FAMILY_COLOURS = {"accent_ink": ACCENT_INK, "orange": ORANGE, "ink_2": INK_2, "ink_3": INK_3,
                  "green": GREEN, "red": RED}
_TOOL_SUB_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK_2}; background: transparent; border: none; }}"
)
_TOOL_NOTE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_3}; background: transparent; border: none; }}"
)
_ROW_HOVER_QSS = (
    f"QWidget#toolRow {{ background: transparent; border-radius: {RADIUS_CONTROL}px; }}"
    f"QWidget#toolRow:hover {{ background: {HOVER}; }}"
)






class ToolCard(QWidget):
    """One 28 px row, the glyph, ``Buffer 50`` and the chip ``Roads``, that opens on the arguments, the code and the raw result."""








    finished = pyqtSignal()


    state_changed = pyqtSignal()
    expanded_changed = pyqtSignal(object, bool)

    def __init__(self, tool_call_id: str, name: str, args=None,
                 danger: str = "read", sentence: str = "", parent=None, animate: bool = True):
        super().__init__(parent)
        self.tool_call_id = tool_call_id
        self.name = name
        self.args = args or {}
        self.danger = danger or "read"
        self.sentence = sentence or ""
        self.ok: bool | None = None

        self.ended = ""
        self.summary = ""
        self.duration_s = 0.0
        self.detail = ""

        self.repeat_ids = [tool_call_id]
        self.repeats = 1
        self._expanded = False
        self._hovering = False



        self._fade_up_wanted = bool(animate)
        self._fade_up_done = False
        self._fade_up_born = time.monotonic()


        resolved_args = _with_layer_names(self.args) if isinstance(self.args, dict) else {}
        self._line = describe_tool_call(name, self.args, resolved_args)
        self._verb, self._chip_text = describe_tool_parts(name, self.args, resolved_args)
        self.connector = connector_for_call(name, self.args)
        glyph, self._colour_token, self.family = tool_glyph(name, self.args)




        self._subject = ""
        self._over_area = False
        if self.connector is not None:
            self.family = "connector"
            glyph = str(self.connector.get("glyph") or "") or glyph
            token = str(self.connector.get("colour") or self.connector.get("color") or "")
            self._colour_token = token or "accent_ink"
            self._subject, self._over_area = call_subject(name, self.args)
        self.glyph = glyph
        self._label_text = (str(self.connector.get("name") or "") if self.connector else "")
        self._label_text = self._label_text or self._verb
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_TIGHT)

        self._header = _HoverRow(self)
        self._header.setObjectName("toolRow")
        self._header.setStyleSheet(_ROW_HOVER_QSS)
        self._header.setFixedHeight(ROW_PX)
        head = QHBoxLayout(self._header)
        head.setContentsMargins(_ROW_PAD_PX, 0, _ROW_PAD_PX, 0)
        head.setSpacing(_ROW_GAP_PX)
        self._icon = QLabel(self._header)
        self._icon.setFixedSize(_GLYPH_SLOT_PX, _GLYPH_SLOT_PX)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon.setPixmap(pixmap_for(self, self.glyph, _GLYPH_PX, self.glyph_colour()))
        head.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._spinner = Spinner(_GLYPH_PX, INK_3, self._header)
        head.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        self._text = ElidedLabel(self._label_text, self._header)
        self._text.setObjectName("toolLine")
        self._text.setStyleSheet(_TOOL_LINE_QSS)
        self._text.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        head.addWidget(self._text, 0, Qt.AlignmentFlag.AlignVCenter)


        self._subject_label = ElidedLabel(self.subject_text(), self._header)
        self._subject_label.setObjectName("toolSubject")
        self._subject_label.setStyleSheet(_TOOL_SUB_QSS)
        self._subject_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._subject_label.setVisible(bool(self.subject_text()))
        head.addWidget(self._subject_label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._chip = _MonoChip(self._chip_text, self._header)
        self._chip.setVisible(self.connector is None and bool(self._chip_text))
        head.addWidget(self._chip, 0, Qt.AlignmentFlag.AlignVCenter)

        self._layer_label = ElidedLabel("", self._header)
        self._layer_label.setObjectName("toolLayer")
        self._layer_label.setStyleSheet(_TOOL_SUB_QSS)
        self._layer_label.hide()
        head.addWidget(self._layer_label, 1, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)




        self._note = ElidedLabel("", self._header)
        self._note.setObjectName("toolNote")
        self._note.setStyleSheet(_TOOL_NOTE_QSS)
        self._note.setTextFormat(Qt.TextFormat.PlainText)
        self._note.hide()
        head.addWidget(self._note, 0, Qt.AlignmentFlag.AlignVCenter)


        self._result_chip = _MonoChip("", self._header)
        self._result_chip.hide()
        head.addWidget(self._result_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        self._chevron = QLabel(self._header)
        self._chevron.setFixedSize(_CHEVRON_PX + 2, _CHEVRON_PX + 2)
        self._chevron.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._chevron.hide()
        head.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self._header.hovered.connect(self._on_hover)
        self._header.clicked.connect(self.toggle)
        col.addWidget(self._header)




        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(_GLYPH_SLOT_PX + _ROW_GAP_PX + _ROW_PAD_PX, 0, 0, SPACE_TIGHT)
        body.setSpacing(SPACE_TIGHT)
        self._sentence_label = QLabel(self._body)
        self._sentence_label.setObjectName("toolSentence")
        self._sentence_label.setStyleSheet(_TOOL_SUB_QSS)
        self._sentence_label.setTextFormat(Qt.TextFormat.PlainText)
        self._sentence_label.setWordWrap(True)
        self._sentence_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self._sentence_label)




        self._source_block = None
        self._source_pending: tuple | None = None
        self._source_btn = None
        self._script_btn = None
        self._copied_timer: QTimer | None = None
        source, language = source_text(self.name, self.args)
        if source:





            source_host = QWidget(self._body)
            source_row = FlowLayout(source_host, SPACE_CARD, SPACE_CARD)
            self._source_btn = self._button(self.tr("Show the code"), self._toggle_source)
            source_row.addWidget(self._source_btn)
            if self.code_text():
                self._script_btn = self._button(self.tr("Copy as script"), self._on_copy_script)
                self._script_btn.setToolTip(
                    self.tr("Copy the code with the setup lines it needs, ready to paste into the console."))
                self._script_btn.hide()
                source_row.addWidget(self._script_btn)
            body.addWidget(source_host)



            self._source_pending = (source, language)
        self._body.hide()
        col.addWidget(self._body)
        self._sync_sentence()

        self._set_running(True)
        self._sync_chevron()



    def sentence_text(self) -> str:
        """The one sentence the opened row shows: what was asked, then what came back."""


        if self.ok is not True and self.ended:
            words = {"denied": self.tr("not run, permission denied"),
                     "stopped": self.tr("stopped before it finished")}
            label = describe_tool_call(self.name, self.args)
            return f"{label}: {words.get(self.ended, self.tr('never finished'))}"
        source = str(self.connector.get("name") or "") if self.connector else ""
        line = call_sentence(self.name, self.args, source)
        duration = format_duration(self.duration_s) if self.ok is not None else ""
        outcome = outcome_sentence(self.ok, self.summary, duration, line)
        return f"{line}, {outcome}" if outcome else line

    def _sync_sentence(self) -> None:
        self._sentence_label.setText(self.sentence_text())



    def _toggle_source(self) -> None:
        self.reveal_source(self._source_block is None or not self._source_block.isVisible())

    def _ensure_source_block(self):
        if self._source_block is None and self._source_pending is not None:
            source, language = self._source_pending
            self._source_pending = None
            self._source_block = CodeBlock(
                source, "script.py" if language == "python" else self.tr("expression"),
                language, self._body)
            self._source_block.hide()
            self._body.layout().addWidget(self._source_block)
        return self._source_block

    def reveal_source(self, shown: bool = True) -> None:
        """Open or close the one fold that holds the call's own code."""




        shown = bool(shown)
        block = self._ensure_source_block() if shown else self._source_block
        if block is None:
            return
        block.setVisible(shown)
        if self._script_btn is not None:
            self._script_btn.setVisible(shown)
        if self._source_btn is not None:
            self._source_btn.setText(self.tr("Hide the code") if shown
                                     else self.tr("Show the code"))

    def _button(self, text: str, slot):
        button = QPushButton(text, self)
        button.setStyleSheet(_BTN_QUIET)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(slot)
        return button

    def _set_running(self, running: bool) -> None:
        self._spinner.setVisible(running)
        self._icon.setVisible(not running)
        if running:
            self._spinner.start()
            return
        self._spinner.stop()

    def set_bare(self, bare: bool = True) -> None:
        """Without its own head row: the activity line of the trace (``trace_tools.ActivityRow``) wears the call, and the card keeps only what opens."""


        self._bare = bool(bare)
        self._header.setVisible(not self._bare)

    def _on_hover(self, hovering: bool) -> None:
        self._hovering = hovering
        self._sync_chevron()

    def _sync_chevron(self) -> None:
        show = self._hovering or self._expanded
        if show:
            name = "chevron_down" if self._expanded else "chevron_right"
            self._chevron.setPixmap(pixmap_for(self, name, _CHEVRON_PX, qcolor(INK_3)))
        self._chevron.setVisible(show)

    def glyph_colour(self):
        """The colour of the row's glyph: the connector's own when it named one, else the colour of the call's family."""

        token = str(self._colour_token or "")
        colour = FAMILY_COLOURS.get(token)
        if colour is None and token.startswith("#") and len(token) in (4, 7, 9):
            colour = token
        return qcolor(colour or INK_2)

    def subject_text(self) -> str:
        """What a connector call asked for, in words: ``building in the area``, ``hotels``."""

        if not self._subject:
            return ""
        if self._over_area:
            return self.tr("{what} in the area").format(what=self._subject)
        return self._subject

    def label_text(self) -> str:
        """The row's label: the connector's name, else the verb; with the repeat count when identical calls folded into this line."""

        text = self._label_text
        if self.repeats > 1:
            text = self.tr("{line} ({n} times)").format(line=text, n=self.repeats)
        return text

    def stack_key(self) -> str:
        """What makes two rows in a row one row: the connector, else the tool."""

        if self.connector is not None and self.connector.get("id"):
            return f"connector:{self.connector['id']}"
        return f"tool:{self.name}" if self.name else ""

    def line(self) -> str:
        """The plain line this call shows, with its repeat count."""
        text = self._line
        if self.repeats > 1:
            text = self.tr("{line} ({n} times)").format(line=text, n=self.repeats)
        return text

    def verb(self) -> str:
        text = self._verb
        if self.repeats > 1:
            text = self.tr("{line} ({n} times)").format(line=text, n=self.repeats)
        return text

    def chip_text(self) -> str:
        return self._chip_text



    def finish(self, ok: bool, summary: str, duration_s: float, detail: str = "") -> None:
        self.ok = bool(ok)
        self.summary = summary or ""



        if not ok and _CANCELLED_RE.match(self.summary) and self.ended != "denied":
            self.ended = "stopped"
        try:
            self.duration_s = float(duration_s or 0.0)
        except (TypeError, ValueError):
            self.duration_s = 0.0
        self.detail = str(detail or "")
        note = self.summary.strip()
        if note and _EMPTY_SUMMARY_RE.match(note):
            note = ""
        facts = result_facts(self.summary)
        chip = ""
        if ok:
            if facts["count"] is not None:



                count = int(facts["count"])
                chip = (self.tr("1 feature") if count == 1
                        else self.tr("{n} features").format(n=group_number(count)))
            elif facts["layer"]:
                chip = facts["layer"]
            elif facts["facts"]:
                value = next(iter(facts["facts"].values()))
                chip = group_number(value) if str(value).strip().isdigit() else value
        self._result_chip.setText(chip)
        self._result_chip.setVisible(bool(chip))

        if ok and facts["layer"] and self.connector is not None:
            self._layer_label.setText(f"\u2192 {facts['layer']}")
            self._layer_label.show()



        check = check_warning(self.summary)
        if chip:

            note = ""
        if check:
            note = check
        if ok:
            self._text.setProperty("failed", False)
        elif self.ended in ("denied", "stopped"):


            note = self.tr("denied") if self.ended == "denied" else self.tr("stopped")
            self._text.setProperty("failed", False)
        else:




            note = self.tr("did not work")
            self._text.setProperty("failed", True)
        self._note.setText(" ".join(note.split()))
        self._note.setVisible(bool(note))
        repolish(self._text)


        self._sync_sentence()
        self._set_running(False)



        if ok:
            self._icon.setPixmap(pixmap_for(self, self.glyph, _GLYPH_PX, self.glyph_colour()))
        elif self.ended in ("denied", "stopped"):
            self._icon.setPixmap(pixmap_for(self, "dash", _GLYPH_PX, qcolor(INK_3)))
        else:
            self._icon.setPixmap(pixmap_for(self, "close", _GLYPH_PX, qcolor(RED)))
        line = self.line()
        self.setToolTip(f"{line}  ·  {note}" if note else line)
        self.finished.emit()
        self.state_changed.emit()

    def mark_unfinished(self, reason: str = "") -> None:
        """A call whose end was never recorded: denied at its permission card, stopped with the run, or cut off in a saved chat."""


        if self.ok is not None:
            self.state_changed.emit()
            return
        self.ended = reason if reason in ("denied", "stopped") else "unfinished"
        self._spinner.stop()
        self._spinner.hide()
        self._icon.show()
        if self.ended != "unfinished":
            self._icon.setPixmap(pixmap_for(self, "dash", _GLYPH_PX, qcolor(INK_3)))
            self._note.setText(self.tr("denied") if self.ended == "denied" else self.tr("stopped"))
            self._note.show()
        self._sync_sentence()
        self.state_changed.emit()

    def same_call(self, name: str, args) -> bool:
        """True when ``name`` and ``args`` are this card's call, exactly."""
        return name == self.name and (args or {}) == (self.args or {})

    def can_fold(self) -> bool:
        """Whether one more identical call may share this line."""






        return self.ok is not None

    def add_repeat(self, tool_call_id: str) -> None:
        """Fold one more identical call into this line and show it running."""
        self._result_chip.hide()
        self.repeat_ids.append(tool_call_id)
        self.repeats += 1
        self._text.setText(self.label_text())
        self._note.hide()



        self._layer_label.hide()
        self._text.setProperty("failed", False)
        repolish(self._text)
        self._icon.setPixmap(pixmap_for(self, self.glyph, _GLYPH_PX, self.glyph_colour()))
        self.ok = None
        self.ended = ""
        self._sync_sentence()
        self._set_running(True)
        self.state_changed.emit()

    def code_text(self) -> str:
        """The Python this call runs, when it is a code tool."""
        code = self.args.get("code") if isinstance(self.args, dict) else None
        if isinstance(code, str) and code.strip() and (
                self.name in _CODE_TOOLS or "code" in self.name):
            return code
        return ""

    def _on_copy_script(self) -> None:
        try:
            QApplication.clipboard().setText(SCRIPT_PREAMBLE + "\n" + self.code_text().rstrip() + "\n")
        except (RuntimeError, AttributeError):
            return
        self._script_btn.setText(self.tr("Copied"))



        if self._copied_timer is None:
            self._copied_timer = QTimer(self)
            self._copied_timer.setSingleShot(True)
            self._copied_timer.timeout.connect(
                lambda: self._script_btn.setText(self.tr("Copy as script")))
        self._copied_timer.start(_COPIED_MS)

    def toggle(self) -> None:
        self.set_expanded(not self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = bool(expanded)
        self._body.setVisible(self._expanded)


        if not self._expanded:
            self.reveal_source(False)
        self._sync_chevron()
        self.expanded_changed.emit(self, self._expanded)

    def is_expanded(self) -> bool:
        return self._expanded



    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        if self._fade_up_done or not self._fade_up_wanted:
            return
        self._fade_up_done = True
        if time.monotonic() - self._fade_up_born <= _FADE_UP_WINDOW_S:
            fade_up(self)

    def hideEvent(self, event):  # noqa: N802 - Qt override
        stop_fade_up(self)
        super().hideEvent(event)

    def cleanup(self) -> None:
        stop_fade_up(self)

    def to_markdown(self) -> str:
        head = self.line()
        status = ""
        if self.ok is not True and self.ended:
            status = self.ended
        elif self.ok is False:
            status = self.tr("failed") + (f": {self.summary}" if self.summary else "")
        elif self.summary and not _EMPTY_SUMMARY_RE.match(self.summary):
            status = self.summary
        duration = format_duration(self.duration_s) if self.ok is not None else ""
        line = f"- {head}"
        if status:
            line += f", {status}"
        if duration:
            line += f", {duration}"
        parts = [line]
        if self.args:
            parts.append(fence(_args_text(self.args), "json"))
        if self.detail:
            parts.append(fence(self.detail[-2000:]))
        return "\n".join(parts)


__all__ = ["CodeBlock", "SCRIPT_PREAMBLE", "ToolCard", "humanise_tool_name"]

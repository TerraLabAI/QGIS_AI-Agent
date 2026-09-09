# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The cards a run asks or tells the user: permission, error, quota pause, and the run summary at the end."""














from __future__ import annotations

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .card_base import (
    _UNBOUNDED_PX,
    _Card,
    glyph_row,
    humanise_tool_name,
    msg_kind_colour,
    msg_kind_icon,
    plain_label,
)
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_QSS,
    _ASK_INPUT_QSS,
    _ASK_LABEL_QSS,
    _ASK_MARGINS,
    _ASK_TEXT_QSS,
    _BTN_GHOST_PILL,
    _BTN_PRIMARY_PILL,
    _BTN_QUIET_LINK,
    _PILL_PX,
    AnswerFooter,
    FoldMixin,
    _pill,
    ask_head,
    done_row,
)
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .shared import tr
from .style import (
    _BTN_DANGER_GHOST,
    FONT_BASE,
    FONT_BODY,
    INK,
    INK_3,
    RED,
    SPACE_CARD,
    SPACE_OUTER,
    qcolor,
)
from .tool_describe import describe_tool_call
from .transcript import fence
from .widgets import ElidedLabel


__all__ = [
    "ErrorCard",
    "PermissionCard",
    "QuotaPauseCard",
    "RestoreWarningCard",
    "RunSummaryCard",
    "_ASK_CARD_QSS",
    "_ASK_DONE_QSS",
    "_ASK_INPUT_QSS",
    "_ASK_LABEL_QSS",
    "_ASK_MARGINS",
    "_ASK_TEXT_QSS",
    "_BTN_GHOST_PILL",
    "_BTN_PRIMARY_PILL",
    "_BTN_QUIET_LINK",
    "_PILL_PX",
    "_done_row",
    "_pill",
    "editable_fields",
    "plain_sentence",
]

_done_row = done_row


_STATUS_TITLE_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BASE}px; font-weight: bold; color: {{colour}};"
    " background: transparent; border: none; }"
)
_MESSAGE_TEXT_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; color: {INK};"
    " background: transparent; border: none; }"
)


def _message_row(parent: QWidget, kind: str, text: str, title: bool = False) -> QWidget:
    """A message card's first row: the kind's glyph in its colour, then the text."""


    colour = msg_kind_colour(kind)
    qss = _STATUS_TITLE_QSS.replace("{colour}", colour) if title else _MESSAGE_TEXT_QSS
    return glyph_row(parent, msg_kind_icon(kind), QColor(colour), text, qss,
                     "cardTitle" if title else "cardText")
















_EDIT_MAX_LEN = 200
_EDIT_VISIBLE_ROWS = 4


def _is_scalar(value) -> bool:
    if isinstance(value, bool) or value is None:
        return isinstance(value, bool)
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and len(value) <= _EDIT_MAX_LEN


def editable_fields(args, sentence: str = "") -> list:
    """[(path, label, value)] the reader may correct, the ones the sentence already names first: those are the ones they are looking at."""

    if not isinstance(args, dict):
        return []
    found = []
    for key in args:
        value = args[key]
        if _is_scalar(value):
            found.append(((str(key),), str(key), value))
        elif isinstance(value, dict):
            for sub in value:
                if _is_scalar(value[sub]):
                    found.append(((str(key), str(sub)), str(sub), value[sub]))
    lowered = (sentence or "").lower()
    found.sort(key=lambda row: (row[1].lower().replace("_", " ") not in lowered,))
    return found


def _value_text(value) -> str:
    """A value as the reader should see it in a field."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _retyped(text: str, original):
    """``text`` back in the type it replaces, or as typed when it will not go."""





    if isinstance(original, bool):
        return text.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(original, int):
        try:
            return int(_ungrouped(text))
        except (TypeError, ValueError):
            return text
    if isinstance(original, float):
        return _as_float(text)
    return text


def _ungrouped(text: str) -> str:
    """``text`` without the spaces used to group digits."""






    raw = str(text or "").strip()
    for space in (" ", "\u00a0", "\u202f", "\u2009"):
        raw = raw.replace(space, "")
    return raw


def _as_float(text: str):
    """``text`` as a float, or as typed when the comma in it is ambiguous."""











    raw = _ungrouped(text)
    if "," in raw:
        if "." in raw:
            raw = raw.replace(",", "")
        else:
            head, _, tail = raw.rpartition(",")
            if "," in head or not (1 <= len(tail) <= 2) or not tail.isdigit():
                return text
            raw = f"{head}.{tail}"
    try:
        return float(raw)
    except (TypeError, ValueError):
        return text


class PermissionCard(_Card, FoldMixin):
    """Inline, never modal: the approval card with Deny and Allow."""








    decided = pyqtSignal(str, str, object)

    _DECISION_TEXT = {
        "allow": QT_TRANSLATE_NOOP("PermissionCard", "Allowed"),

        "allow_project": QT_TRANSLATE_NOOP("PermissionCard", "Allowed for this project"),
        "deny": QT_TRANSLATE_NOOP("PermissionCard", "Denied"),
    }

    def __init__(self, tool_call_id: str, sentence: str, args=None, parent=None, name: str = ""):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_OUTER)


        self.tool_call_id = tool_call_id
        self.sentence = plain_sentence(sentence, args, name)
        self.decision: str | None = None
        self._closed = False
        self.setToolTip(self.tr("Needs your approval"))

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACE_OUTER)
        head = ask_head(self._body, self.sentence)
        self._sentence = head.text_label
        body.addWidget(head)

        self.args = dict(args or {})
        self._fields = editable_fields(self.args, self.sentence)
        self._editors: list = []
        self._form = None
        self._edit_btn = None
        if self._fields:
            self._form = self._build_form()
            self._form.hide()
            body.addWidget(self._form)

        footer = AnswerFooter(self._body)
        if self._fields:
            self._edit_btn = self._button(self.tr("Edit values"), _BTN_QUIET_LINK,
                                          self._toggle_form)
        deny = _pill(self._button(self.tr("Deny"), _BTN_GHOST_PILL,
                                  lambda: self._decide("deny")))
        self._allow_btn = _pill(self._button(self.tr("Allow"), _BTN_PRIMARY_PILL,
                                             lambda: self._decide("allow")))
        footer.set_widgets(self._edit_btn, [deny, self._allow_btn])
        body.addWidget(footer)
        self._col.addWidget(self._body)

        self._decision_row = done_row(self, "check", qcolor(INK_3), "")
        self._decision_line = self._decision_row.line_label
        self._decision_row.hide()
        self._col.addWidget(self._decision_row)



    def _toggle_form(self) -> None:
        """Show or hide the values."""

        if self._form is None:
            return
        showing = not self._form.isVisible()
        self._form.setVisible(showing)
        if self._edit_btn is not None:
            self._edit_btn.setText(self.tr("Hide values") if showing else self.tr("Edit values"))

    def _build_form(self) -> QWidget:
        """One row per correctable value: the name beside the value as a field."""




        host = QWidget(self._body)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_CARD)
        hidden: list = []
        for index, (path, label, value) in enumerate(self._fields):
            row = QWidget(host)
            lines = QHBoxLayout(row)
            lines.setContentsMargins(0, 0, 0, 0)
            lines.setSpacing(SPACE_OUTER)
            name = ElidedLabel(label, row)
            name.setStyleSheet(_ASK_LABEL_QSS)
            name.setFixedWidth(scale_px_length(92))
            name.setToolTip(".".join(path))
            lines.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)
            editor = QLineEdit(_value_text(value), row)
            editor.setStyleSheet(_ASK_INPUT_QSS)
            editor.setFixedHeight(scale_px_length(_PILL_PX))
            editor.setClearButtonEnabled(False)
            lines.addWidget(editor)
            col.addWidget(row)
            self._editors.append((path, value, editor))
            if index >= _EDIT_VISIBLE_ROWS:
                row.hide()
                hidden.append(row)
        if hidden:


            more = self._button(
                self.tr("Show {n} more").format(n=len(hidden)), _BTN_QUIET_LINK,
                lambda: self._show_rest(hidden, more))
            col.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
        return host

    @staticmethod
    def _show_rest(rows, button) -> None:
        for row in rows:
            row.show()
        button.hide()

    def edits(self) -> dict:
        """Only what the reader actually changed, in the type it replaces."""




        changed: dict = {}
        for path, original, editor in self._editors:
            typed = _retyped(editor.text(), original)
            if typed == original and type(typed) is type(original):
                continue
            if len(path) == 1:
                changed[path[0]] = typed
            else:
                holder = changed.get(path[0])
                if not isinstance(holder, dict):
                    holder = dict(self.args.get(path[0]) or {})
                    changed[path[0]] = holder
                holder[path[1]] = typed
        return changed

    def stop_asking(self) -> None:
        """The run that asked has ended: the card no longer decides."""





        self._closed = True
        if self.decision is None:
            self._body.setEnabled(False)

    def _decide(self, decision: str) -> None:
        if self.decision is not None or getattr(self, "_closed", False):
            return

        changed = self.edits() if decision != "deny" else {}
        self.collapse(decision)
        self.decided.emit(self.tool_call_id, decision, changed or None)

    def decision_text(self, decision: str | None = None) -> str:
        key = decision or self.decision or ""
        return self.tr(self._DECISION_TEXT[key]) if key in self._DECISION_TEXT else key

    def collapse(self, decision: str) -> None:
        """Fold the card shut, then one line stating the decision."""
        self.decision = decision
        denied = decision == "deny"
        self._decision_row.icon_label.setPixmap(
            pixmap_for(self, "close" if denied else "check", 11,
                       qcolor(RED) if denied else qcolor(INK_3)))
        self._decision_line.setText(f"{self.decision_text()} · {self.sentence}")
        self._body.setEnabled(False)
        self.fold_body(self._body, self._show_decision)

    def _show_decision(self) -> None:
        self._body.hide()
        self._decision_row.show()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def to_markdown(self) -> str:
        decision = self.decision_text() if self.decision else self.tr("pending")
        return f"- **{self.tr('Permission')}:** {self.sentence} ({decision})"






_SERVER_SAYS_IT_BETTER = ("execute_code",)


def plain_sentence(sentence: str, args=None, name: str = "") -> str:
    """The permission sentence in plain words: the tool describer when the tool is known, else the server's sentence unless it is a raw call."""













    sentence = (sentence or "").strip()
    raw = not sentence or sentence.startswith(("Call ", "Run processing algorithm"))

    keep = name in _SERVER_SAYS_IT_BETTER or "credits" in sentence
    if name and (raw or not keep):
        sentence = describe_tool_call(name, args) or sentence
    if sentence and not sentence.endswith((".", "?", "!")):
        sentence += "."
    return sentence or tr("The agent wants to run this action.")


class ErrorCard(_Card):
    """A failure: a red cross, the message in plain text, Retry as a ghost pill when it can help, Report as a quiet link."""








    retry_requested = pyqtSignal(str)
    continue_requested = pyqtSignal(str)

    def __init__(self, run_id: str | None, code: str, message: str,
                 retryable: bool, details: str = "", parent=None, continuable: bool = False):
        super().__init__("error", parent)
        self.run_id = run_id or ""
        self.code = code or ""
        self.message = message or ""
        self.details = details or ""
        self._col.addWidget(_message_row(self, "error", self.message))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_CARD)




        if continuable:
            row.addWidget(_pill(self._button(self.tr("Continue"), _BTN_GHOST_PILL, self._on_continue)))
        elif retryable:
            row.addWidget(_pill(self._button(self.tr("Retry"), _BTN_GHOST_PILL, self._on_retry)))



        if self.run_id:
            row.addWidget(self._button(self.tr("Report"), _BTN_QUIET_LINK, self._on_report))
        row.addStretch(1)
        self._col.addLayout(row)

    def _on_report(self) -> None:
        try:
            from .error_report_dialog import show_error_report
            show_error_report(self, self.message, self.run_id)
        except Exception:  # noqa: BLE001 - a report that will not open must not take the panel with it
            return

    def _on_retry(self) -> None:
        self.retry_requested.emit(self.run_id)

    def _on_continue(self) -> None:
        self.continue_requested.emit(self.run_id)

    def to_markdown(self) -> str:
        head = f"> **{self.tr('Error')}**"
        if self.code:
            head += f" `{self.code}`"
        lines = [f"{head}: {self.message}"]
        if self.details:
            lines.append(fence(self.details[-2000:]))
        return "\n".join(lines)


class RestoreWarningCard(_Card):
    """Inline, never modal: a restore would drop work, so it asks once."""







    confirmed = pyqtSignal(str, bool)
    cancelled = pyqtSignal()

    def __init__(self, checkpoint_id: str, discard: bool = False, edits: bool = False,
                 whole: bool = True, parent=None):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_OUTER)
        self.checkpoint_id = checkpoint_id
        self.discard = bool(discard)
        if discard and not whole:


            text = self.tr("This goes back to the oldest state still kept, not to the very start: "
                           "the earlier ones were cleared to save disk space.")
        elif discard and edits:
            text = self.tr("This drops everything the agent did in this chat, and your own changes since.")
        elif discard:
            text = self.tr("This drops everything the agent did in this chat.")
        else:
            text = self.tr("Manual changes since this point will be lost.")
        body = QWidget(self)
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(SPACE_OUTER)
        sentence = QLabel(text, body)
        sentence.setObjectName("askText")
        sentence.setStyleSheet(_ASK_TEXT_QSS)
        sentence.setWordWrap(True)
        sentence.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        grid.addWidget(sentence, 0, 0)
        buttons = QWidget(body)
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_CARD)
        row.addWidget(_pill(self._button(self.tr("Cancel"), _BTN_GHOST_PILL, self._cancel)))




        label = self.tr("Discard") if discard else self.tr("Restore")
        style = _BTN_DANGER_GHOST if discard else _BTN_PRIMARY_PILL
        row.addWidget(_pill(self._button(label, style, self._confirm)))
        grid.addWidget(buttons, 1, 0, Qt.AlignmentFlag.AlignRight)
        self._col.addWidget(body)

    def _cancel(self) -> None:
        self.hide()
        self.deleteLater()
        self.cancelled.emit()

    def _confirm(self) -> None:
        self.hide()
        self.deleteLater()
        self.confirmed.emit(self.checkpoint_id, self.discard)


class QuotaPauseCard(_Card):
    """The run paused on a quota: an amber triangle, the message, Resume."""

    resume_requested = pyqtSignal(str)

    def __init__(self, run_id: str, message: str, parent=None):
        super().__init__("warning", parent)
        self.run_id = run_id
        self.message = message or self.tr("The run is paused.")
        self._col.addWidget(_message_row(self, "warning", self.message))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(_pill(self._button(self.tr("Resume"), _BTN_GHOST_PILL,
                                         lambda: self.resume_requested.emit(self.run_id))))
        row.addStretch(1)
        self._col.addLayout(row)

    def to_markdown(self) -> str:
        return f"> **{self.tr('Paused')}:** {self.message}"


class RunSummaryCard(_Card):
    """What a run changed, in GIS words, once the answer is on screen."""









    _STATUS = {
        "done": ("success", QT_TRANSLATE_NOOP("RunSummaryCard", "Done")),
        "cancelled": ("neutral", QT_TRANSLATE_NOOP("RunSummaryCard", "Stopped")),
        "failed": ("error", QT_TRANSLATE_NOOP("RunSummaryCard", "Failed")),
        "quota": ("warning", QT_TRANSLATE_NOOP("RunSummaryCard", "Out of runs")),
    }

    def __init__(self, status: str, summary: str = "", usage=None,
                 verification=None, duration_s=None, steps=None, parent=None):
        super().__init__(None, parent)
        self.status = status
        self.summary = summary or ""
        self.usage = usage if isinstance(usage, dict) else {}
        self.verification = verification
        self.duration_s = duration_s
        self.steps = steps
        kind, word = self._STATUS.get(status, ("neutral", QT_TRANSLATE_NOOP("RunSummaryCard", "Ended")))
        self._status_word = self.tr(word)
        self.set_kind(kind if kind != "neutral" else None)
        self._col.addWidget(_message_row(self, kind, self._status_word, title=True))
        if self.summary:
            text = plain_label(self.summary, self)
            text.setObjectName("cardText")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._col.addWidget(text)
        for line in self.lines():
            label = plain_label(f"\u2022  {line}", self)
            label.setObjectName("cardText")
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._col.addWidget(label)

    def lines(self) -> list:
        return self._verification_lines(self.verification)

    @staticmethod
    def _verification_lines(verification, with_changes: bool = True) -> list:
        """The card's bullet lines."""

        if not verification:
            return []
        if isinstance(verification, (list, tuple)):
            return [str(v) for v in verification if v]
        if isinstance(verification, dict):
            for key in ("lines", "checks", "items"):
                if isinstance(verification.get(key), (list, tuple)):
                    lines = [str(v) for v in verification[key] if v]
                    if with_changes and isinstance(verification.get("changes"), (list, tuple)):
                        lines += [str(v) for v in verification["changes"] if v]
                    return lines
            return [f"{humanise_tool_name(str(k))}: {v}" for k, v in verification.items()
                    if v not in (None, "")]
        return [str(verification)]

    def to_markdown(self) -> str:
        lines = [f"**{self._status_word}**" + (f": {self.summary}" if self.summary else "")]
        lines.extend(f"- {c}" for c in self.lines())
        return "\n".join(lines)

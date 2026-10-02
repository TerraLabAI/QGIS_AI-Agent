# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

from urllib.parse import urlsplit

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from .card_base import (
    _UNBOUNDED_PX,
    _Card,
    glyph_row,
    humanise_tool_name,
    mono_font,
    msg_kind_colour,
    msg_kind_icon,
    plain_label,
)
from .card_controls import (
    _ASK_CARD_QSS,
    _ASK_DONE_LINE_QSS,
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
from .code_block import CodeBlock
from .font_scale import scale_px_length, scale_qss_font_px
from .shared import tr
from .style import (
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    INK,
    INK_2,
    INSET,
    LINE,
    RADIUS_CONTROL,
    SPACE_CARD,
    SPACE_OUTER,
)
from .tool_describe import call_title, card_effect, source_text
from .transcript import fence
from .widgets import ChatLabel, ElidedLabel


__all__ = [
    "ErrorCard",
    "PermissionCard",
    "QuotaPauseCard",
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
    "title_and_reasons",
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



    colour = msg_kind_colour(kind)
    qss = _STATUS_TITLE_QSS.replace("{colour}", colour) if title else _MESSAGE_TEXT_QSS
    return glyph_row(parent, msg_kind_icon(kind), QColor(colour), text, qss,
                     "cardTitle" if title else "cardText")
















_EDIT_MAX_LEN = 200
_EDIT_VISIBLE_ROWS = 4






_CODE_COLLAPSE_LINES = 12

_ADDRESSES_SHOWN = 8
_ADDRESS_MAX_CHARS = 600

_REASON_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_BODY}px; font-weight: 400; color: {INK_2};"
    " background: transparent; border: none; }"
)

_ADDRESS_QSS = scale_qss_font_px(
    f"QLabel {{ font-size: {FONT_HINT}px; color: {INK_2}; background: {INSET};"
    f" border: 1px solid {LINE}; border-radius: {RADIUS_CONTROL}px; padding: 6px 8px; }}"
)


_CODE_EXPANDED_LINES = 30


def _is_scalar(value) -> bool:
    if isinstance(value, bool) or value is None:
        return isinstance(value, bool)
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and len(value) <= _EDIT_MAX_LEN






_ALGORITHM_KEYS = ("algorithm_id", "algorithm")
_NOT_A_VALUE = ("TEMPORARY_OUTPUT",)


def _algorithm_inputs(algorithm_id: str) -> dict | None:


    if not algorithm_id:
        return None
    try:
        from qgis.core import QgsApplication

        alg = QgsApplication.processingRegistry().algorithmById(algorithm_id)
        if alg is None:
            return None
        return {d.name(): (d.description() or d.name()) for d in alg.parameterDefinitions()
                if not getattr(d, "isDestination", lambda: False)()}
    except Exception:  # noqa: BLE001
        return None


def _is_input_value(value) -> bool:
    if not _is_scalar(value):
        return False
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(_NOT_A_VALUE) or "://" in text:
            return False
    return True


def editable_fields(args, sentence: str = "") -> list:








    if not isinstance(args, dict):
        return []
    algorithm = next((str(args.get(k) or "") for k in _ALGORITHM_KEYS if args.get(k)), "")
    inputs = _algorithm_inputs(algorithm) if algorithm else None
    found = []
    for key in args:
        value = args[key]
        if key in _ALGORITHM_KEYS and algorithm:
            continue
        if _is_input_value(value):
            found.append(((str(key),), humanise_tool_name(str(key)), value))
        elif isinstance(value, dict):
            for sub in value:
                if not _is_input_value(value[sub]):
                    continue
                if inputs is not None and key == "parameters":
                    if sub not in inputs:
                        continue
                    label = inputs[sub]
                else:
                    label = humanise_tool_name(str(sub))
                found.append(((str(key), str(sub)), label, value[sub]))
    lowered = (sentence or "").lower()
    found.sort(key=lambda row: (row[1].lower() not in lowered,))
    return found


def _value_text(value) -> str:

    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _retyped(text: str, original):






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







    raw = str(text or "").strip()
    for space in (" ", "\u00a0", "\u202f", "\u2009"):
        raw = raw.replace(space, "")
    return raw


def _as_float(text: str):












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









    decided = pyqtSignal(str, str, object)

    quiet = False

    _DECISION_TEXT = {
        "allow": QT_TRANSLATE_NOOP("PermissionCard", "Allowed"),

        "allow_project": QT_TRANSLATE_NOOP("PermissionCard", "Allowed for this project"),

        "allow_run": QT_TRANSLATE_NOOP("PermissionCard", "Allowed for this run"),
        "deny": QT_TRANSLATE_NOOP("PermissionCard", "Denied"),
    }

    def __init__(self, tool_call_id: str, sentence: str, args=None, parent=None, name: str = "",
                 addresses=None, grant: str = "", group: str = ""):
        super().__init__(None, parent, frame_qss=_ASK_CARD_QSS)
        self.set_margins(*_ASK_MARGINS)
        self._col.setSpacing(SPACE_OUTER)


        self.tool_call_id = tool_call_id


        self.group = str(group or "")
        self._grant = grant




        self._calls: list[dict] = []
        self._open: list[str] = []
        self.decision: str | None = None





        self._rebuild_timer: QTimer | None = None
        self.setToolTip(self.tr("Needs your approval"))
        self._remember(tool_call_id, sentence, args, name, addresses)
        self._body = self._build_body()
        self._col.addWidget(self._body)


        self._decision_row = QWidget(self)
        line = QHBoxLayout(self._decision_row)
        line.setContentsMargins(2, 0, 2, 0)
        self._decision_line = ElidedLabel("", self._decision_row, mode=Qt.TextElideMode.ElideMiddle)
        self._decision_line.setObjectName("decisionLine")
        self._decision_line.setStyleSheet(_ASK_DONE_LINE_QSS)
        line.addWidget(self._decision_line, 1)
        self._decision_row.hide()
        self._col.addWidget(self._decision_row)



    def _remember(self, tool_call_id: str, sentence: str, args, name: str, addresses) -> None:




        urls: list[str] = []
        for url in addresses or []:
            url = str(url or "").strip()[:_ADDRESS_MAX_CHARS]
            if url and url not in urls:
                urls.append(url)
        args = dict(args or {})
        said = plain_sentence(sentence, args, name)
        self._calls.append({"id": tool_call_id, "sentence": said, "args": args, "name": name,
                            "addresses": urls[:_ADDRESSES_SHOWN],
                            "fields": editable_fields(args, said)})
        self._open.append(tool_call_id)

    def tool_call_ids(self) -> list[str]:

        return list(self._open)

    def is_open(self) -> bool:

        return self.decision is None and bool(self._open)

    def add_call(self, tool_call_id: str, sentence: str, args=None, name: str = "",
                 addresses=None, group: str = "") -> bool:


        if (not self.group or str(group or "") != self.group or not self.is_open()
                or tool_call_id in [c["id"] for c in self._calls]):
            return False
        self._remember(tool_call_id, sentence, args, name, addresses)
        self._schedule_rebuild()
        return True

    def resolve(self, tool_call_id: str) -> bool:






        if tool_call_id not in self._open:
            return not self._open
        self._open.remove(tool_call_id)
        if self.decision is None and self._open:
            self._calls = [c for c in self._calls if c["id"] != tool_call_id]
            self.tool_call_id = self._open[0]
            self._schedule_rebuild()
        return not self._open

    def sentence_for(self, tool_call_id: str) -> str:

        for call in self._calls:
            if call["id"] == tool_call_id:
                return call["sentence"]
        return self.sentence

    def _schedule_rebuild(self) -> None:









        if self._rebuild_timer is None:
            self._rebuild_timer = QTimer(self)
            self._rebuild_timer.setSingleShot(True)
            self._rebuild_timer.setInterval(0)
            self._rebuild_timer.timeout.connect(self._flush_rebuild)
        if not self._rebuild_timer.isActive():
            self._rebuild_timer.start()

    def _flush_rebuild(self) -> None:



        if self.decision is None:
            self._rebuild()

    def _rebuild(self) -> None:
        old = self._body
        self._body = self._build_body()
        self._col.replaceWidget(old, self._body)
        old.hide()
        old.setParent(None)
        old.deleteLater()

    def cleanup(self) -> None:


        super().cleanup()
        if self._rebuild_timer is not None:
            self._rebuild_timer.stop()

    def _headline(self) -> str:
        sentences = [c["sentence"] for c in self._calls]
        if len(sentences) == 1:
            return sentences[0]
        if len(set(sentences)) == 1:
            return self.tr("{action}, {n} times.").format(action=sentences[0].rstrip(". "), n=len(sentences))
        return self.tr("{n} actions wait for your approval.").format(n=len(sentences))

    def _build_body(self) -> QWidget:

        grouped = len(self._calls) > 1
        first = self._calls[0]
        self.sentence = self._headline()
        self.addresses = []
        for call in self._calls:
            self.addresses.extend(u for u in call["addresses"] if u not in self.addresses)
        self.args = first["args"]


        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACE_OUTER)


        title, reasons = title_and_reasons(self.sentence)
        head = ask_head(self._body, title)
        self._sentence = head.text_label
        body.addWidget(head)
        notes = [reasons] if reasons else []
        offers = str(self._grant or "").split()
        if "answer" in offers:
            notes.append(self.tr("Your answer also counts for the next calls of this kind in this answer."))
        for note in notes:
            line = ChatLabel(note, self._body, wrap=True, selectable=True)
            line.setObjectName("permissionReason")
            line.setStyleSheet(_REASON_QSS)
            body.addWidget(line)
        if grouped and len({c["sentence"] for c in self._calls}) > 1:

            for call in self._calls:
                line = ChatLabel("· " + call["sentence"], self._body, wrap=True, selectable=True)
                line.setObjectName("permissionCallLine")
                line.setStyleSheet(_REASON_QSS)
                body.addWidget(line)
        self._address_well = None
        self._address_btn = None
        if self.addresses:
            body.addWidget(self._build_address_section())







        self.code_block: CodeBlock | None = None
        self._code_expanded = False
        self._code_toggle_btn = None
        self._code_open_btn = None
        self._code_open = False
        self._code_lines = 0
        code, language = ("", "") if grouped else source_text(first["name"], self.args)
        if code:
            body.addWidget(self._build_code_section(code, language))

        self._fields = [f for call in self._calls for f in call["fields"]]
        self._editors: list = []
        self._form = None
        self._edit_btn = None
        if self._fields:
            self._form = self._build_form()
            self._form.hide()
            body.addWidget(self._form)

        footer = AnswerFooter(self._body)
        if "file_writes" in offers and not grouped:



            self._edit_btn = self._button(self.tr("Allow file writes in this project"), _BTN_QUIET_LINK,
                                          lambda: self._decide("allow_project"))
            self._edit_btn.setObjectName("agentPermissionAllowProject")
        elif self._fields:
            self._edit_btn = self._button(self.tr("Edit values"), _BTN_QUIET_LINK,
                                          self._toggle_form)
        deny = _pill(self._button(self.tr("Deny"), _BTN_GHOST_PILL,
                                  lambda: self._decide("deny")))
        self._allow_btn = _pill(self._button(self.tr("Allow"), _BTN_PRIMARY_PILL,
                                             lambda: self._decide("allow")))
        answers = [(deny, "deny")]
        self._run_btn = None
        if "run" in offers and not grouped:


            self._run_btn = _pill(self._button(self.tr("Allow for this run"), _BTN_GHOST_PILL,
                                               lambda: self._decide("allow_run")))
            answers.append((self._run_btn, "allow_run"))
        answers.append((self._allow_btn, "allow"))
        for button, action in answers:
            button.setObjectName("agentPermission" + "".join(p.capitalize() for p in action.split("_")))
            button.setProperty("agentAction", action)
            button.setProperty("agentRequestId", self.tool_call_id)

            button.setProperty("agentRequestIds", ",".join(self._open))
            button.setProperty("agentCardKind", "permission")
        footer.set_widgets(self._edit_btn, [button for button, _action in answers])
        body.addWidget(footer)
        return self._body



    def _build_address_section(self) -> QWidget:



        host = QWidget(self._body)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_CARD)
        hosts = []
        for url in self.addresses:
            name = (urlsplit(url).hostname or url) if "://" in url else url
            if name not in hosts:
                hosts.append(name)
        if len(hosts) == 1:
            reason = self.tr("{host}: a site you did not name, in no known catalog").format(host=hosts[0])
        else:
            reason = self.tr("{hosts}: sites you did not name, in no known catalog").format(
                hosts=", ".join(hosts))
        line = ChatLabel(reason, host, wrap=True, selectable=True)
        line.setObjectName("permissionReason")
        line.setStyleSheet(_REASON_QSS)
        col.addWidget(line)
        self._address_well = ChatLabel("\n".join(self.addresses), host, wrap=True, selectable=True)
        self._address_well.setObjectName("permissionAddress")
        self._address_well.setFont(mono_font(FONT_HINT))
        self._address_well.setStyleSheet(_ADDRESS_QSS)
        self._address_well.hide()
        self._address_btn = self._button(self._address_link_text(False), _BTN_QUIET_LINK,
                                         self._toggle_address)
        self._address_btn.setObjectName("permissionShowAddress")
        col.addWidget(self._address_btn, 0, Qt.AlignmentFlag.AlignLeft)
        col.addWidget(self._address_well)
        return host

    def _address_link_text(self, showing: bool) -> str:
        if showing:
            return self.tr("Hide the address") if len(self.addresses) == 1 else \
                self.tr("Hide the addresses")
        return self.tr("Show the address") if len(self.addresses) == 1 else \
            self.tr("Show the {n} addresses").format(n=len(self.addresses))

    def _toggle_address(self) -> None:
        showing = not self._address_well.isVisible()
        self._address_well.setVisible(showing)
        self._address_btn.setText(self._address_link_text(showing))



    def _build_code_section(self, code: str, language: str) -> QWidget:











        name = "script.py" if language == "python" else self.tr("expression")


        self.code_block = CodeBlock(code, name, language, self._body, line_cap=None, wrap=True)

        host = QWidget(self._body)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_CARD)
        self._code_lines = len(code.splitlines()) or 1
        self._code_open_btn = self._button(self._code_link_text(False), _BTN_QUIET_LINK,
                                           self._toggle_code_open)
        self._code_open_btn.setObjectName("permissionShowCode")
        col.addWidget(self._code_open_btn, 0, Qt.AlignmentFlag.AlignLeft)
        col.addWidget(self.code_block)
        self.code_block.set_visible_lines(_CODE_COLLAPSE_LINES)
        self.code_block.hide()

        if self._code_lines > _CODE_COLLAPSE_LINES:
            self._code_toggle_btn = self._button(
                self.tr("Show all {n} lines").format(n=self._code_lines), _BTN_QUIET_LINK,
                self._toggle_code)
            self._code_toggle_btn.hide()
            col.addWidget(self._code_toggle_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return host

    def _code_link_text(self, showing: bool) -> str:
        if showing:
            return self.tr("Hide the code")
        return self.tr("View the code ({n} lines)").format(n=self._code_lines)

    def _toggle_code_open(self) -> None:

        if self.code_block is None:
            return



        self._code_open = not self._code_open
        showing = self._code_open
        self.code_block.setVisible(showing)
        if self._code_toggle_btn is not None:
            self._code_toggle_btn.setVisible(showing)
        if showing:
            self.code_block.scroll_to_start()
        self._code_open_btn.setText(self._code_link_text(showing))

    def _toggle_code(self) -> None:
        self._code_expanded = not self._code_expanded
        if self._code_expanded:
            self.code_block.set_visible_lines(_CODE_EXPANDED_LINES)
            self._code_toggle_btn.setText(self.tr("Show fewer lines"))
        else:
            self.code_block.set_visible_lines(_CODE_COLLAPSE_LINES)
            self._code_toggle_btn.setText(
                self.tr("Show all {n} lines").format(n=self._code_lines))



    def _toggle_form(self) -> None:


        if self._form is None:
            return
        showing = not self._form.isVisible()
        self._form.setVisible(showing)
        if self._edit_btn is not None:
            self._edit_btn.setText(self.tr("Hide values") if showing else self.tr("Edit values"))

    def _build_form(self) -> QWidget:







        host = QWidget(self._body)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(SPACE_CARD)
        grouped = len(self._calls) > 1
        for call in self._calls:
            if not call["fields"]:
                continue
            if grouped:
                title = ChatLabel(call["sentence"], host, wrap=True, selectable=True)
                title.setObjectName("permissionFormCall")
                title.setStyleSheet(_REASON_QSS)
                col.addWidget(title)
            hidden: list = []
            for index, (path, label, value) in enumerate(call["fields"]):
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
                self._editors.append((call["id"], path, value, editor))
                if index >= _EDIT_VISIBLE_ROWS:
                    row.hide()
                    hidden.append(row)
            if hidden:


                more = self._button(
                    self.tr("Show {n} more").format(n=len(hidden)), _BTN_QUIET_LINK, lambda: None)
                more.clicked.connect(lambda _=False, rows=hidden, button=more: self._show_rest(rows, button))
                col.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
        return host

    @staticmethod
    def _show_rest(rows, button) -> None:
        for row in rows:
            row.show()
        button.hide()

    def edits(self, tool_call_id: str = "") -> dict:






        tool_call_id = tool_call_id or self._calls[0]["id"]
        args = next((c["args"] for c in self._calls if c["id"] == tool_call_id), {})
        changed: dict = {}
        for call_id, path, original, editor in self._editors:
            if call_id != tool_call_id:
                continue
            typed = _retyped(editor.text(), original)
            if typed == original and type(typed) is type(original):
                continue
            if len(path) == 1:
                changed[path[0]] = typed
            else:
                holder = changed.get(path[0])
                if not isinstance(holder, dict):
                    holder = dict(args.get(path[0]) or {})
                    changed[path[0]] = holder
                holder[path[1]] = typed
        return changed

    def _decide(self, decision: str) -> None:
        if self.decision is not None or not self._open:
            return

        changed = {cid: (self.edits(cid) if decision != "deny" else {}) for cid in self._open}
        self.collapse(decision)



        for tool_call_id in list(self._open):
            if tool_call_id in self._open:
                self.decided.emit(tool_call_id, decision, changed.get(tool_call_id) or None)

    def decision_text(self, decision: str | None = None) -> str:
        key = decision or self.decision or ""
        return self.tr(self._DECISION_TEXT[key]) if key in self._DECISION_TEXT else key

    def collapse(self, decision: str) -> None:


        self.decision = decision
        titles = [title_and_reasons(call["sentence"])[0] for call in self._calls] or [self.sentence]
        if len(titles) == 1:
            title = titles[0]
        elif len(set(titles)) == 1:
            title = self.tr("{action}, {n} times").format(action=titles[0], n=len(titles))
        else:
            title = self.tr("{n} actions").format(n=len(titles))
        self._decision_line.setText(f"{self.decision_text()} · {title}")
        self._body.setEnabled(False)
        self.fold_body(self._body, self._show_decision)

    def _show_decision(self) -> None:
        self._body.hide()
        if self.decision != "deny" or self.quiet:
            self.hide()
            return
        self._decision_row.show()
        self.set_frame(_ASK_DONE_QSS)
        self.set_margins(0, 0, 0, 0)
        self.setMaximumWidth(_UNBOUNDED_PX)

    def to_markdown(self) -> str:
        decision = self.decision_text() if self.decision else self.tr("pending")
        if len(self._calls) == 1:
            where = "".join(f" `{url}`" for url in self.addresses)
            return f"- **{self.tr('Permission')}:** {self.sentence}{where} ({decision})"
        lines = [f"- **{self.tr('Permission')}:** {self.sentence} ({decision})"]
        for call in self._calls:
            where = "".join(f" `{url}`" for url in call["addresses"])
            lines.append(f"  - {call['sentence']}{where}")
        return "\n".join(lines)






_SERVER_SAYS_IT_BETTER = ("execute_code",)


def title_and_reasons(sentence: str) -> tuple:






    text = " ".join(str(sentence or "").split())
    reasons = ""
    head, dot, rest = text.partition(". ")
    if dot and rest:
        text, reasons = head, rest
    text = text.rstrip(". ")
    if text.endswith(")") and " (" in text:
        text, _, inner = text[:-1].partition(" (")
        reasons = f"{inner[:1].upper()}{inner[1:]}" + (f". {reasons}" if reasons else "")
    return text, reasons


def plain_sentence(sentence: str, args=None, name: str = "") -> str:














    sentence = (sentence or "").strip()
    raw = not sentence or sentence.startswith(("Call ", "Run processing algorithm"))

    keep = name in _SERVER_SAYS_IT_BETTER or "credits" in sentence
    if name and (raw or not keep):
        sentence = call_title(name, args) or sentence
    if sentence and not sentence.endswith((".", "?", "!")):
        sentence += "."
    effect = card_effect(name, args)
    if sentence and effect:
        sentence = f"{sentence} {effect}"
    return sentence or tr("The agent wants to run this action.")


class ErrorCard(_Card):









    retry_requested = pyqtSignal(str)
    continue_requested = pyqtSignal(str)
    undo_retry_requested = pyqtSignal(str)

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




        if self.code == "PLUGIN_UPDATE_REQUIRED":


            row.addWidget(_pill(self._button(self.tr("Update the plugin"), _BTN_GHOST_PILL, self._on_update)))
        elif continuable:
            row.addWidget(_pill(self._button(self.tr("Continue"), _BTN_GHOST_PILL, self._on_continue)))
        elif retryable:
            row.addWidget(_pill(self._button(self.tr("Retry"), _BTN_GHOST_PILL, self._on_retry)))


        self._undo_retry = None
        if retryable and self.run_id and self.code != "PLUGIN_UPDATE_REQUIRED" and not continuable:
            self._undo_retry = _pill(self._button(self.tr("Undo and retry"), _BTN_GHOST_PILL,
                                                  lambda: self.undo_retry_requested.emit(self.run_id)))
            self._undo_retry.hide()
            row.addWidget(self._undo_retry)



        if self.run_id:
            row.addWidget(self._button(self.tr("Report"), _BTN_QUIET_LINK, self._on_report))
        row.addStretch(1)
        self._col.addLayout(row)

    def _on_report(self) -> None:
        try:
            from .error_report_dialog import show_error_report
            show_error_report(self, self.message, self.run_id)
        except Exception:  # noqa: BLE001
            return

    def _on_retry(self) -> None:
        self.retry_requested.emit(self.run_id)

    def set_undo_retry(self, available: bool) -> None:
        if self._undo_retry is not None:
            self._undo_retry.setVisible(bool(available))

    def _on_update(self) -> None:
        try:
            from .cross_plugin_discovery import open_plugin_manager_later
            open_plugin_manager_later("AI Agent by TerraLab", "https://terra-lab.ai/ai-agent")
        except Exception:  # noqa: BLE001
            return

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


class QuotaPauseCard(_Card):


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

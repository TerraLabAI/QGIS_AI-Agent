# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






























from __future__ import annotations

import json
import re

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QPainter
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .card_base import format_duration
from .cards_tool import ToolCard
from .font_scale import widget_pixel_ratio
from .icons import pixmap_for
from .source_marks import source_host, source_mark_pixmap
from .style import INK_2, INK_3, SPACE_TIGHT, qcolor
from .tool_describe import (
    _with_layer_names,
    call_subject,
    count_text,
    describe_tool_call,
    is_background_job,
    result_facts,
)
from .trace_rows import (
    _HINT_QSS,
    _LABEL_QSS,
    _MEDIUM_QSS,
    _SECOND_QSS,
    GLYPH_PX,
    GLYPH_SLOT_PX,
    ROW_MIN_PX,
    Chevron,
    HoverRow,
    _row_layout,
)
from .widgets import ElidedLabel, Spinner



_RESULT_MARK_PX = 14
_RESULTS_SHOWN = 5
_CHEVRON_PX = 12

_VERB_MIN_PX = 130
_QWIDGETSIZE_MAX = (1 << 24) - 1
_OUTCOME_MIN_PX = 90

_SOURCE_MIN_PX = 56



_PERCENT_RE = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s?%")


MEASURE_HEAD_RE = re.compile(r"^.*?\d[\d.,]*(?:\u00a0\S+)?")


SEARCH_TOOLS = frozenset({
    "search_open_data", "geocode", "reverse_geocode", "search_stac_items",
    "search_earthdata_collections", "search_earthdata_granules", "search_gee_catalog",
    "search_web", "find_data_on_web", "qgis_docs", "search_plugin_repository", "list_stac_collections",
})
CODE_TOOLS = frozenset({"execute_code", "run_processing", "evaluate_expression", "run_code",
                        "run_python", "run_model"})

_LIST_KEYS = ("results", "items", "datasets", "collections", "granules", "matches",
              "sections", "plugins", "sources", "hits")
_NAME_KEYS = ("title", "name", "display_name", "label", "id")
_SOURCE_KEYS = ("provider", "source", "portal", "publisher", "domain", "host", "organisation",
                "organization", "collection", "url", "href", "link")


def is_search_tool(name: str, args=None) -> bool:

    name = str(name or "")
    return name in SEARCH_TOOLS or name.startswith(("search_", "find_"))


def is_code_tool(name: str) -> bool:
    return name in CODE_TOOLS


def search_results(detail: str) -> list[tuple[str, str]]:

    text = str(detail or "").strip()
    if not text or text[0] not in "[{":
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    items = data if isinstance(data, list) else None
    if isinstance(data, dict):
        for key in _LIST_KEYS:
            if isinstance(data.get(key), list):
                items = data[key]
                break
    if not items:
        return []
    rows = []
    for item in items:
        if isinstance(item, str):
            rows.append((item.strip(), ""))
            continue
        if not isinstance(item, dict):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        name = next((str(item.get(k) or props.get(k)) for k in _NAME_KEYS
                     if item.get(k) or props.get(k)), "")
        source = next((source_host(item.get(k) or props.get(k)) for k in _SOURCE_KEYS
                       if isinstance(item.get(k) or props.get(k), str)), "")
        if name.strip():
            rows.append((" ".join(name.split()), source))
    return rows


def _same_subject(chip: str, subject: str) -> bool:


    chip = " ".join(chip.replace("...", "\u2026").split()).lower()
    subject = " ".join(subject.replace("...", "\u2026").split()).lower()
    if not chip or not subject:
        return False
    if chip == subject:
        return True
    head_a, head_b = chip.split("\u2026", 1)[0].strip(), subject.split("\u2026", 1)[0].strip()
    return bool(head_a and head_b and (head_a.startswith(head_b) or head_b.startswith(head_a)))


def secondary_of(card: ToolCard) -> str:



    source = card.source_text() if callable(getattr(card, "source_text", None)) else ""
    head = card.head_text() if callable(getattr(card, "head_text", None)) else ""
    if source:



        repeated = getattr(card, "connector", None) is not None and source.lower() in head.lower()
        return "" if repeated else source
    subject = card.subject_text() if callable(getattr(card, "subject_text", None)) else ""
    if not subject and getattr(card, "connector", None) is not None:
        subject, _ = call_subject(card.name, card.args)
    chip = str(card.chip_text() or "")
    label = str(card.label_text() if callable(getattr(card, "label_text", None)) else "")
    text = subject or chip
    if text and label and _same_subject(text, label):
        return ""
    return text


class _RowSpinner(Spinner):









    def __init__(self, diameter: int, color, parent=None):
        super().__init__(diameter, color=color, parent=parent)
        self._held = False
        self._still = None

    def start(self) -> None:
        self._held = False
        super().start()
        self.update()

    def stop(self) -> None:


        self._held = self._held or self._wanted
        super().stop()
        self.update()

    def finish(self) -> None:

        self._held = False
        super().stop()

    def paintEvent(self, event):  # noqa: N802
        if not self._held:
            super().paintEvent(event)
            return
        try:
            if self._still is None:
                self._still = pixmap_for(self, "clock", GLYPH_PX, qcolor(INK_3))
            painter = QPainter(self)
            painter.drawPixmap(0, 0, self._still)
            painter.end()
        except Exception:  # noqa: BLE001
            return


class _ResultRow(QWidget):



    def __init__(self, name: str, host: str, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        mark = QLabel(self)
        mark.setFixedSize(GLYPH_SLOT_PX, GLYPH_SLOT_PX)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setPixmap(source_mark_pixmap(host or name, _RESULT_MARK_PX, widget_pixel_ratio(self)))
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        title = ElidedLabel(name, self)
        title.setObjectName("resultName")
        title.setStyleSheet(_LABEL_QSS)
        title.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        row.addWidget(title, 0, Qt.AlignmentFlag.AlignVCenter)
        if host:
            domain = ElidedLabel(host, self)
            domain.setObjectName("resultHost")
            domain.setStyleSheet(_HINT_QSS)
            domain.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
            row.addWidget(domain, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self.setFixedHeight(24)


class ActivityRow(QWidget):


    def __init__(self, card: ToolCard, parent=None):
        super().__init__(parent)
        self.cards: list[ToolCard] = []
        self.key = card.stack_key()
        self._open = False
        self._opens = False
        self._results: list[tuple[str, str]] = []


        self._job_over = False
        self._job_percent = ""
        self._second_is_source = False
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        self._head = HoverRow(self)
        self._head.setFixedHeight(ROW_MIN_PX)
        row = _row_layout(self._head)
        self._icon = QLabel(self._head)
        self._icon.setFixedSize(GLYPH_SLOT_PX, GLYPH_SLOT_PX)
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignVCenter)
        self._spinner = _RowSpinner(GLYPH_PX, INK_3, self._head)
        row.addWidget(self._spinner, 0, Qt.AlignmentFlag.AlignVCenter)




        self._label = ElidedLabel("", self._head, Qt.TextElideMode.ElideRight)
        self._label.setObjectName("activityLabel")
        self._label.setStyleSheet(_MEDIUM_QSS)
        self._label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        row.addWidget(self._label, 0, Qt.AlignmentFlag.AlignVCenter)
        self._secondary = ElidedLabel("", self._head, Qt.TextElideMode.ElideMiddle)
        self._secondary.setObjectName("activitySubject")
        self._secondary.setStyleSheet(_HINT_QSS)
        self._secondary.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        row.addWidget(self._secondary, 0, Qt.AlignmentFlag.AlignVCenter)
        self._count = QLabel(self._head)
        self._count.setObjectName("activityCount")
        self._count.setStyleSheet(_HINT_QSS)
        self._count.hide()
        row.addWidget(self._count, 0, Qt.AlignmentFlag.AlignVCenter)


        self._outcome = ElidedLabel("", self._head)
        self._outcome.setObjectName("activityOutcome")
        self._outcome.setStyleSheet(_HINT_QSS)
        self._outcome.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self._outcome.hide()
        row.addWidget(self._outcome, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self._chevron = Chevron(_CHEVRON_PX, self._head)
        self._chevron.hide()
        row.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self._head.hovered.connect(self._on_hover)
        self._head.clicked.connect(self.toggle)
        col.addWidget(self._head)





        self._body = QWidget(self)
        self._body_col = QVBoxLayout(self._body)
        self._body_col.setContentsMargins(0, 0, 0, SPACE_TIGHT)
        self._body_col.setSpacing(0)
        self._results_host = QWidget(self._body)
        self._results_col = QVBoxLayout(self._results_host)
        self._results_col.setContentsMargins(0, 0, 0, 0)
        self._results_col.setSpacing(0)
        self._results_host.hide()
        self._body_col.addWidget(self._results_host)
        self._body.hide()
        col.addWidget(self._body)
        self.add(card)



    def add(self, card: ToolCard) -> None:

        card.set_bare(True)
        card.setParent(self._body)
        card.hide()
        self._body_col.addWidget(card)
        self.cards.append(card)
        self._job_over = False
        self._job_percent = ""
        card.state_changed.connect(self.refresh)
        card.expanded_changed.connect(self._on_card_expanded)
        self.refresh()

    def _on_card_expanded(self, card: ToolCard, expanded: bool) -> None:
        card.setVisible(bool(expanded) and card.opens())
        self._sync_open()

    @property
    def repeats(self) -> int:
        return sum(int(getattr(card, "repeats", 1) or 1) for card in self.cards)

    def label(self) -> str:
        return self._label.full_text()

    def secondary(self) -> str:
        return self._secondary.full_text()

    def _fit_verb(self) -> None:











        verb_min = min(_VERB_MIN_PX, self._label.sizeHint().width())
        measure = MEASURE_HEAD_RE.match(self._label.full_text())
        if measure:
            head = self._label.fontMetrics().horizontalAdvance(measure.group(0) + "\u2026")
            verb_min = min(max(verb_min, head), self._label.sizeHint().width())
        self._label.setMinimumWidth(verb_min)
        if not self._secondary.isVisibleTo(self):
            self._secondary.setMinimumWidth(0)
            self._secondary.setMaximumWidth(_QWIDGETSIZE_MAX)
            return
        if self._second_is_source:
            verb = self._label.sizeHint().width()
            shown = min(self._secondary.sizeHint().width(), self._secondary.maximumWidth())
            others = self._head.sizeHint().width() - shown - verb
            room = self._head.width() - others - min(_VERB_MIN_PX, verb)
            whole = self._secondary.sizeHint().width()
            keep = max(0, min(whole, room))
            if keep < min(whole, _SOURCE_MIN_PX):


                keep = 0
            self._secondary.setMinimumWidth(keep)
            self._secondary.setMaximumWidth(keep)
            return
        self._secondary.setMinimumWidth(0)

        shown = min(self._secondary.sizeHint().width(), self._secondary.maximumWidth())
        others = self._head.sizeHint().width() - shown
        self._secondary.setMaximumWidth(max(0, self._head.width() - others))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_verb()

    def line(self) -> str:




        label = self.label()
        n = self.repeats
        if n > 1:
            return f"{label} \u00d7 {n}".strip()
        last = self.cards[-1]
        if callable(getattr(last, "source_text", None)) and last.source_text():
            return label
        second = self.secondary()
        if label and second and second.lower().startswith(label.lower()):
            return second
        return " ".join(part for part in (label, second) if part).strip()



    def job_live(self) -> bool:


        last = self.cards[-1]
        return not self._job_over and last.ok is True and \
            is_background_job(last.summary, getattr(last, "detail", ""))

    def set_job_status(self, text: str) -> None:

        if not self.job_live():
            return
        found = _PERCENT_RE.findall(str(text or ""))
        percent = f"{found[-1]}\u202f%" if found else ""
        if percent != self._job_percent:
            self._job_percent = percent
            self.refresh()

    def end_job(self) -> None:

        if self._job_over:
            return
        self._job_over = True
        if self.cards[-1].ok is True and is_background_job(
                self.cards[-1].summary, getattr(self.cards[-1], "detail", "")):
            self.refresh()

    def refresh(self) -> None:
        first = self.cards[0]
        last = self.cards[-1]
        calling = any(card.ok is None and not getattr(card, "ended", "") for card in self.cards)
        running = calling or self.job_live()
        self._spinner.setVisible(running)
        self._icon.setVisible(not running)
        if running:
            if not self._spinner._held:
                self._spinner.start()
        else:
            self._spinner.finish()
        head = first.head_text() if callable(getattr(first, "head_text", None)) else ""
        self._label.setText(head or describe_tool_call(first.name, first.args))
        second = secondary_of(last)
        self._second_is_source = bool(second) and callable(getattr(last, "source_text", None)) \
            and second == last.source_text()
        self._secondary.setText(second)
        self._secondary.setVisible(bool(second))
        ended = "" if running else str(getattr(last, "ended", "") or "")


        failures = 0 if running else self.failures()
        failed = not running and last.ok is False and ended not in ("denied", "stopped") \
            and failures >= self.repeats
        declined = failed or ended in ("denied", "stopped")
        self._label.setStyleSheet(_SECOND_QSS if declined else _MEDIUM_QSS)
        colour = first.glyph_colour() if callable(getattr(first, "glyph_colour", None)) \
            else qcolor(INK_2)
        if declined:

            mark = "lu.x" if failed else "lu.minus"
            self._icon.setPixmap(pixmap_for(self, mark, GLYPH_PX, qcolor(INK_3)))
        else:
            self._icon.setPixmap(pixmap_for(self, str(first.glyph or "lu.cog"), GLYPH_PX, colour))
        n = self.repeats
        self._count.setText(f"\u00d7 {n}")
        self._count.setVisible(n > 1)
        self._results = [] if calling else self._search_results(last)
        self._sync_results()
        self._sync_outcome(running, failed, ended, failures if failures and not failed else 0)
        self._fit_verb()
        self._opens = not calling and (bool(self._results) or any(card.opens() for card in self.cards))
        self._head.setCursor(Qt.CursorShape.PointingHandCursor if self._opens
                             else Qt.CursorShape.ArrowCursor)
        if not self._opens and self._open:
            self.set_open(False)


        lines = []
        for card in self.cards:
            text = card.line()
            source = card.source_text() if callable(getattr(card, "source_text", None)) else ""
            if source and source not in text:
                text = f"{text} · {source}"
            if card.ok is not None and getattr(card, "duration_s", 0):
                text = f"{text} \u00b7 {format_duration(card.duration_s)}"
            lines.append(text)
        self._head.setToolTip("\n".join(lines))
        self._sync_open()

    @staticmethod
    def _search_results(card: ToolCard) -> list:
        if not is_search_tool(card.name) or not card.ok:
            return []
        return search_results(card.detail)

    def _sync_results(self) -> None:
        while self._results_col.count():
            item = self._results_col.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for name, host in self._results[:_RESULTS_SHOWN]:
            self._results_col.addWidget(_ResultRow(name, host, self._results_host))
        more = len(self._results) - _RESULTS_SHOWN
        if more > 0:
            label = QLabel(self.tr("+{n} more").format(n=more), self._results_host)
            label.setObjectName("resultMore")
            label.setStyleSheet(_HINT_QSS)
            label.setFixedHeight(22)
            self._results_col.addWidget(label)
        self._results_host.setVisible(bool(self._results))

    def _sync_outcome(self, running: bool, failed: bool, ended: str = "", some_failed: int = 0) -> None:

        text = ""
        if failed:
            text = self.tr("did not work")
        elif ended == "denied":
            text = self.tr("denied")
        elif ended == "stopped":
            text = self.tr("stopped")
        elif running:
            text = self._job_percent if self.job_live() else ""
        elif some_failed:
            text = self.tr("{failed} of {total} did not work").format(
                failed=some_failed, total=self.repeats)
        elif self._results:
            count = len(self._results)
            text = self.tr("1 result") if count == 1 else self.tr("{n} results").format(n=count)
        else:
            last = self.cards[-1]
            facts = result_facts(last.summary)
            if facts["count"] is not None:
                text = count_text(last.summary, getattr(last, "detail", ""))
            elif facts["layer"] and self._made_layer(last, facts["layer"]):
                text = f"\u2192 {facts['layer']}"
        self._outcome.setText(text)
        self._outcome.setMinimumWidth(min(_OUTCOME_MIN_PX, self._outcome.sizeHint().width()) if text else 0)
        self._outcome.setVisible(bool(text))

    @staticmethod
    def _made_layer(card: ToolCard, layer: str) -> bool:




        wanted = " ".join(str(layer).split()).lower()
        args = card.args if isinstance(card.args, dict) else {}
        args = {k: v for k, v in args.items() if k != "output_name"}
        values = list(_with_layer_names(args).values()) + list(args.values())
        params = args.get("parameters")
        if isinstance(params, dict):
            values += [v for k, v in params.items() if str(k).upper() != "OUTPUT"]
        for value in values:


            if isinstance(value, str) and wanted and " ".join(value.split()).lower().startswith(wanted):
                return False
        return True

    def failures(self) -> int:

        total = 0
        for card in self.cards:
            count = getattr(card, "failures", None)
            if not isinstance(count, int):
                count = int(getattr(card, "repeats", 1) or 1) if card.ok is False \
                    and getattr(card, "ended", "") not in ("denied", "stopped") else 0
            total += count
        return total



    def _on_hover(self, hot: bool) -> None:
        self._chevron.set_hot(hot)
        self._chevron.setVisible(self._opens and (hot or self._open))

    def opens(self) -> bool:
        return self._opens

    def toggle(self) -> None:
        if self._opens:
            self.set_open(not self._open)

    def set_open(self, open_: bool) -> None:
        open_ = bool(open_) and self._opens
        for card in self.cards:
            card.set_expanded(open_)
        self._sync_open()

    def _sync_open(self) -> None:


        self._open = self._opens and any(card.is_expanded() for card in self.cards)
        self._body.setVisible(self._open)
        self._chevron.set_open(self._open, 0)
        self._chevron.setVisible(self._opens and (self._open or self._head.is_hot()))

    def is_open(self) -> bool:
        return self._open

    def failed(self) -> bool:

        if not self.cards:
            return False
        last = self.cards[-1]
        return last.ok is False and getattr(last, "ended", "") not in ("denied", "stopped")

    def cleanup(self) -> None:
        self._spinner.finish()
        self._chevron.cleanup()
        for card in self.cards:



            for signal in (card.state_changed, card.expanded_changed):
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass
            cleanup = getattr(card, "cleanup", None)
            if callable(cleanup):
                cleanup()

    def to_markdown(self) -> str:
        return "\n".join(card.to_markdown() for card in self.cards)


__all__ = ["CODE_TOOLS", "SEARCH_TOOLS", "ActivityRow", "is_code_tool", "is_search_tool",
           "search_results", "secondary_of"]

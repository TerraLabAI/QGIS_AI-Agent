# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





































from __future__ import annotations

import json
import re
import time

from qgis.PyQt.QtCore import QCoreApplication, Qt, QTimer
from qgis.PyQt.QtGui import QPainter
from qgis.PyQt.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .card_base import format_duration
from .cards_tool import ToolCard
from .font_scale import widget_pixel_ratio
from .icons import pixmap_for
from .source_marks import _clipped as clipped_mark
from .source_marks import connector_row, family_pixmap, source_host, source_mark_pixmap
from .style import _BTN_QUIET, INK_2, INK_3, SPACE_TIGHT, qcolor
from .tool_describe import (
    _with_layer_names,
    call_subject,
    count_text,
    describe_tool_call,
    is_background_job,
    result_facts,
)
from .tool_describe import source_text as _source_text
from .trace_rows import (
    _HINT_QSS,
    _LABEL_QSS,
    _MEDIUM_QSS,
    _ROW_GAP,
    _ROW_PAD_X,
    _ROW_PAD_Y,
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

_VERB_WHOLE_OUTCOME_PX = 56

_SOURCE_MIN_PX = 56



_PERCENT_RE = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s?%")
_JOB_LOOK_MS = 2000


_CALL_CLOCK_FROM_S = 3
_CALL_TICK_MS = 1000
_CALL_BAR_PX = 2
_CALL_BAR_W = 44
_SERVER_FRESH_S = 5.0


def progress_words(percent=None, eta_s=None, done_bytes=None, total_bytes=None) -> str:


    def tr(text):
        return QCoreApplication.translate("ActivityRow", text)

    def mb(size) -> str:
        size /= 1024 * 1024
        return f"{size:.1f}" if size < 10 else f"{size:.0f}"

    parts = []
    if isinstance(done_bytes, (int, float)) and done_bytes > 0:
        if isinstance(total_bytes, (int, float)) and total_bytes >= done_bytes:
            parts.append(tr("{done} of {total} MB").format(done=mb(done_bytes), total=mb(total_bytes)))
        else:
            parts.append(tr("{done} MB").format(done=mb(done_bytes)))
    elif isinstance(percent, (int, float)) and not isinstance(percent, bool) and 0 <= percent <= 100:
        parts.append(f"{int(percent)}\u202f%")
    if isinstance(eta_s, (int, float)) and not isinstance(eta_s, bool) and eta_s > 0:
        if eta_s < 60:
            parts.append(tr("about {n} s left").format(n=int(max(10, round(eta_s / 10.0) * 10))))
        else:
            parts.append(tr("about {n} min left").format(n=int(round(eta_s / 60.0))))
    return " \u00b7 ".join(parts)


def stage_words(stage: str, done=None, total=None) -> str:


    def tr(text):
        return QCoreApplication.translate("ActivityRow", text)

    words = {"place": tr("Finding the place"), "query": tr("Querying the source"),
             "clip": tr("Cutting to the area"), "layer": tr("Adding the layer")}.get(str(stage or ""), "")
    if words and isinstance(done, int) and isinstance(total, int) and 0 <= done <= total and total > 1:
        words = tr("{words} · {done} of {total}").format(words=words, done=done, total=total)
    return words


def seconds_words(seconds: float) -> str:

    whole = int(max(0, seconds))
    if whole < 60:
        return QCoreApplication.translate("ActivityRow", "{n} s").format(n=whole)
    return QCoreApplication.translate("ActivityRow", "{m} min {s} s").format(m=whole // 60, s=f"{whole % 60:02d}")


def _task_id_of(card) -> str:

    try:
        value = json.loads(str(getattr(card, "detail", "") or ""))
    except (TypeError, ValueError):
        return ""
    return str(value.get("task_id") or "") if isinstance(value, dict) else ""


def _task_state(task_id: str):

    if not task_id:
        return None
    try:
        from ..tools.processing_run import task_state
    except Exception:  # noqa: BLE001
        return None
    return task_state(task_id)


MEASURE_HEAD_RE = re.compile(r"^.*?\d[\d.,]*(?:\u00a0\S+)?")


SEARCH_TOOLS = frozenset({
    "search_open_data", "geocode", "reverse_geocode", "search_stac_items",
    "search_earthdata_collections", "search_earthdata_granules", "search_gee_catalog",
    "search_web", "find_data_on_web", "qgis_docs", "search_plugin_repository", "list_stac_collections",
})

_LIST_KEYS = ("results", "items", "datasets", "collections", "granules", "matches",
              "sections", "plugins", "sources", "hits")
_NAME_KEYS = ("title", "name", "display_name", "label", "id")
_LINK_KEYS = ("url", "page_url", "href", "link", "landing_page", "homepage")
_SOURCE_KEYS = ("provider", "source", "portal", "publisher", "domain", "host", "organisation",
                "organization", "collection", "url", "href", "link")


def is_search_tool(name: str, args=None) -> bool:

    name = str(name or "")
    return name in SEARCH_TOOLS or name.startswith(("search_", "find_"))


def search_results(detail: str) -> list[tuple[str, str, dict, str]]:







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
            rows.append((item.strip(), "", {}, ""))
            continue
        if not isinstance(item, dict):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        name = next((str(item.get(k) or props.get(k)) for k in _NAME_KEYS
                     if item.get(k) or props.get(k)), "")
        source = next((source_host(item.get(k) or props.get(k)) for k in _SOURCE_KEYS
                       if isinstance(item.get(k) or props.get(k), str)), "")
        if name.strip():
            owners = ("provider", "publisher", "organisation", "organization")
            provider = next((str(item.get(k) or props.get(k)) for k in owners if item.get(k) or props.get(k)), "")
            family = connector_row(str(item.get("connector_id") or item.get("family") or ""), source, provider)
            logo = str(item.get("logo_url") or "")
            if not family and logo.startswith("https://"):
                family = {"logo_url": logo}
            link = next((str(item.get(k) or props.get(k)) for k in _LINK_KEYS
                         if str(item.get(k) or props.get(k) or "").startswith(("https://", "http://"))), "")
            rows.append((" ".join(name.split()), source, family, link))
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


class _MoreRow(QLabel):


    def __init__(self, text: str, on_click, parent=None):
        super().__init__(text, parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):  # noqa: N802
        event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self._on_click()
        event.accept()


class _ResultRow(QWidget):



    def __init__(self, name: str, host: str, family: dict | None = None, parent=None, link: str = ""):
        super().__init__(parent)
        self._link = link
        if link:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(link)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._mark = QLabel(self)
        self._mark.setFixedSize(GLYPH_SLOT_PX, GLYPH_SLOT_PX)
        self._mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key = host or name
        self._family = family or {}
        self._logo_url = str(self._family.get("logo_url") or "")
        self._paint_mark()
        if self._logo_url:

            from .library.pictures import store

            store().ready.connect(self._on_logo_ready)
        row.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignVCenter)
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
        if link:

            for child in self.findChildren(QLabel):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def _paint_mark(self) -> None:
        ratio = widget_pixel_ratio(self)


        if self._family.get("id"):
            self._mark.setPixmap(family_pixmap(self._family, _RESULT_MARK_PX, ratio))
            return
        if self._logo_url:

            from .library.pictures import store
            from .logo_tile import logo_pixmap

            path = store().want_file(self._logo_url)
            chip = logo_pixmap(path, _RESULT_MARK_PX, ratio) if path else None
            if chip is not None:
                self._mark.setPixmap(clipped_mark(chip, _RESULT_MARK_PX, ratio, True))
                return
        self._mark.setPixmap(source_mark_pixmap(self._key, _RESULT_MARK_PX, ratio))

    def _on_logo_ready(self, url: str) -> None:
        if url == self._logo_url:
            self._paint_mark()

    def mousePressEvent(self, event) -> None:
        if self._link and event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._link and event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            from .external_links import open_external_url

            open_external_url(self._link, parent=self.window())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ActivityRow(QWidget):


    def __init__(self, card: ToolCard, parent=None):
        super().__init__(parent)
        self.cards: list[ToolCard] = []
        self.key = card.stack_key()
        self._open = False
        self._opens = False
        self._results: list[tuple[str, str, dict, str]] = []
        self._results_open = False
        self._icon_logo = ""
        self._logo_hooked = False




        self._job_over = False
        self._progress_text = ""
        self._job_percent = None
        self._server_at = 0.0
        self._job_looked = False
        self.on_job_end = None
        self._job_timer = QTimer(self)
        self._job_timer.setInterval(_JOB_LOOK_MS)
        self._job_timer.timeout.connect(self._look_at_job)
        self._call_stage = ""
        self._call_source = ""
        self._call_count = (None, None, False)
        self._call_started = 0.0
        self._call_tick = QTimer(self)
        self._call_tick.setInterval(_CALL_TICK_MS)
        self._call_tick.timeout.connect(self._on_call_tick)
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




        self._sub = QWidget(self)
        sub = QHBoxLayout(self._sub)
        sub.setContentsMargins(_ROW_PAD_X + GLYPH_SLOT_PX + _ROW_GAP, 0, _ROW_PAD_X, _ROW_PAD_Y)
        sub.setSpacing(_ROW_GAP)
        self._bar = QProgressBar(self._sub)
        self._bar.setObjectName("activityProgress")
        self._bar.setRange(0, 1000)
        self._bar.setTextVisible(False)
        self._bar.setFixedSize(_CALL_BAR_W, _CALL_BAR_PX)
        self._bar.setStyleSheet(
            "QProgressBar { background: rgba(128,128,128,0.18); border: none; border-radius: 1px; }"
            f"QProgressBar::chunk {{ background: {INK_3}; border-radius: 1px; }}")
        self._bar.hide()
        sub.addWidget(self._bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self._sub_label = ElidedLabel("", self._sub, Qt.TextElideMode.ElideRight)
        self._sub_label.setObjectName("activityProgressText")
        self._sub_label.setStyleSheet(_HINT_QSS)
        sub.addWidget(self._sub_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self._sub.hide()
        col.addWidget(self._sub)



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



        self._tries_shown = False
        self._tries_count = 0
        self._tries_btn = QPushButton("", self._body)
        self._tries_btn.setObjectName("activityTries")
        self._tries_btn.setStyleSheet(_BTN_QUIET)
        self._tries_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._tries_btn.setAutoDefault(False)
        self._tries_btn.clicked.connect(self._toggle_tries)
        self._tries_btn.hide()
        self._body_col.addWidget(self._tries_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self._body.hide()
        col.addWidget(self._body)
        self.add(card)

    def add(self, card: ToolCard) -> None:

        card.set_bare(True)
        card.setParent(self._body)
        card.hide()
        self._body_col.insertWidget(self._body_col.indexOf(self._tries_btn), card)
        self.cards.append(card)
        self._job_over = False
        self._job_looked = False
        self._progress_text = ""
        self._job_percent = None
        card.state_changed.connect(self.refresh)
        card.expanded_changed.connect(self._on_card_expanded)
        self.refresh()

    def _on_card_expanded(self, card: ToolCard, expanded: bool) -> None:
        self._sync_card(card, expanded)
        self._sync_open()

    def _sync_card(self, card: ToolCard, expanded: bool) -> None:


        recovered = bool(getattr(card, "recovered", False))
        card.setVisible(bool(expanded) and card.opens() and (self._tries_shown or not recovered))
        show_tries = getattr(card, "show_tries", None)
        if callable(show_tries):
            show_tries(bool(expanded) and self._tries_shown)

    def _toggle_tries(self) -> None:
        self._tries_shown = not self._tries_shown
        self._sync_tries_button()
        for card in self.cards:
            self._sync_card(card, card.is_expanded())

    def _sync_tries_button(self) -> None:
        n = self._tries_count
        if self._tries_shown:
            text = self.tr("Hide the try that did not work") if n == 1 \
                else self.tr("Hide the {n} tries that did not work").format(n=n)
        else:
            text = self.tr("Show the try that did not work") if n == 1 \
                else self.tr("Show the {n} tries that did not work").format(n=n)
        self._tries_btn.setText(text)
        self._tries_btn.setVisible(n > 0)

    @staticmethod
    def _tries_of(card) -> list:

        tries = getattr(card, "_tries", None)
        if isinstance(tries, list):
            return [bool(worked) for worked, _ in tries]
        if card.ok is None or getattr(card, "ended", "") in ("denied", "stopped", "unfinished"):
            return []
        return [bool(card.ok)]

    def _sync_retries(self) -> tuple[int, int]:




        flags = [self._tries_of(card) for card in self.cards]
        seq = [worked for tries in flags for worked in tries]
        last_ok = max((i for i, worked in enumerate(seq) if worked), default=-1)
        recovered_total = sum(1 for worked in seq[:max(last_ok, 0)] if not worked)
        trailing = sum(1 for worked in seq[last_ok + 1:] if not worked)
        failed_before = False
        for index, card in enumerate(self.cards):
            later_worked = any(any(tries) for tries in flags[index + 1:])
            recovered = card.ok is False and later_worked
            after_failure = card.ok is True and failed_before
            setter = getattr(card, "set_retry_state", None)
            if callable(setter):
                setter(recovered, after_failure)
            failed_before = failed_before or not all(flags[index])
        return recovered_total, trailing

    @property
    def repeats(self) -> int:
        return sum(int(getattr(card, "repeats", 1) or 1) for card in self.cards)

    def label(self) -> str:
        return self._label.full_text()

    def secondary(self) -> str:
        return self._secondary.full_text()

    def _fit_verb(self) -> None:



















        label_whole = self._label.sizeHint().width()
        verb_min = min(_VERB_MIN_PX, label_whole)
        measure = MEASURE_HEAD_RE.match(self._label.full_text())
        if getattr(self, "_outcome_whole", False):
            verb_min = min(_VERB_WHOLE_OUTCOME_PX, label_whole)
        elif measure:
            head = self._label.fontMetrics().horizontalAdvance(measure.group(0) + "\u2026")
            verb_min = min(max(verb_min, head), label_whole)
        shown = self._outcome.isVisibleTo(self._head)
        outcome_min = getattr(self, "_outcome_floor", 0) if shown else 0
        outcome_whole = self._outcome.sizeHint().width() if shown else 0
        room = self._text_room()
        second_min = 0
        second_max = _QWIDGETSIZE_MAX
        if self._secondary.isVisibleTo(self._head):
            whole = self._secondary.sizeHint().width()
            if self._second_is_source:
                keep = max(0, min(whole, room - outcome_whole - min(_VERB_MIN_PX, label_whole)))
                keep = max(0, min(keep, room - verb_min - outcome_min))
                if keep < min(whole, _SOURCE_MIN_PX):


                    keep = 0
                second_min = second_max = keep
            else:
                keep = max(0, room - label_whole - outcome_whole)
                if getattr(self, "_outcome_whole", False) and keep < min(whole, _SOURCE_MIN_PX):


                    keep = 0
                second_max = keep
        over = verb_min + second_min + outcome_min - room
        if over > 0:
            cut = min(verb_min, over)
            verb_min -= cut
            outcome_min = max(0, outcome_min - (over - cut))
        self._label.setMinimumWidth(verb_min)
        self._secondary.setMinimumWidth(second_min)
        self._secondary.setMaximumWidth(second_max)
        self._outcome.setMinimumWidth(outcome_min)

    def _text_room(self) -> int:



        lay = self._head.layout()
        margins = lay.contentsMargins()
        fixed = 0
        shown = 0
        for widget in (self._icon, self._spinner, self._count, self._chevron,
                       self._label, self._secondary, self._outcome):
            if not widget.isVisibleTo(self._head):
                continue
            shown += 1
            if widget in (self._label, self._secondary, self._outcome):
                continue
            if widget.minimumWidth() == widget.maximumWidth():
                fixed += widget.minimumWidth()
            else:
                fixed += widget.sizeHint().width()
        gaps = max(0, shown - 1) * max(0, lay.spacing())
        return max(0, self._head.width() - margins.left() - margins.right() - fixed - gaps)

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

    def set_job_status(self, text: str, progress=None) -> None:


        if not self.job_live():
            return
        progress = progress if isinstance(progress, dict) else {}
        percent = progress.get("percent")
        if percent is None:
            found = _PERCENT_RE.findall(str(text or ""))
            try:
                percent = int(float(found[-1].replace(",", "."))) if found else None
            except ValueError:
                percent = None
        self._server_at = time.monotonic()
        self._job_percent = percent
        self._set_progress(progress_words(percent, progress.get("eta_s")))

    def set_call_progress(self, done, total=None, stage: str = "") -> None:


        if not self._calling():
            return
        if stage.startswith("wait:"):

            self._call_source = stage[5:]
        elif stage:
            self._call_stage = stage
            self._call_count = (done, total, False)
        else:
            self._call_count = (done, total, True)
        self._show_call()

    def _calling(self) -> bool:
        return any(card.ok is None and not getattr(card, "ended", "") for card in self.cards)

    def _call_words(self) -> str:
        done, total, is_bytes = self._call_count
        if is_bytes:
            size = progress_words(done_bytes=done, total_bytes=total)
            known = isinstance(total, (int, float)) and total >= (done or 0) > 0

            words = (size if known else self.tr("Downloading {size}").format(size=size)) if size \
                else stage_words(self._call_stage)
        else:
            words = stage_words(self._call_stage, done, total)
        spent = time.monotonic() - self._call_started if self._call_started else 0.0
        clock = seconds_words(spent) if spent >= _CALL_CLOCK_FROM_S else ""
        if not words and clock and self._call_source:
            words = self.tr("Waiting for {host}").format(host=self._call_source)
        return " \u00b7 ".join(part for part in (words, clock) if part)

    def _show_call(self) -> None:

        self._progress_text = self._call_words()
        self._sync_sub(True)

    def _sync_sub(self, running: bool) -> None:

        text = self._progress_text if running else ""
        share = None
        if running and self._calling():
            done, total, _ = self._call_count
            if isinstance(done, (int, float)) and isinstance(total, (int, float)) and total > 0:
                share = done / total
        elif running and isinstance(self._job_percent, (int, float)):
            share = self._job_percent / 100.0
        if share is not None:
            self._bar.setValue(int(1000 * min(1.0, max(0.0, share))))
        self._bar.setVisible(share is not None)
        if self._sub_label.text() != text:
            self._sub_label.setText(text)
        self._sub.setVisible(bool(text) or share is not None)

    def _on_call_tick(self) -> None:
        if not self._calling():
            self._call_tick.stop()
            return
        self._show_call()

    def _set_progress(self, text: str) -> None:
        if text != self._progress_text:
            self._progress_text = text
            self.refresh()

    def _look_at_job(self) -> None:


        if not self.job_live():
            self._job_timer.stop()
            return
        state = _task_state(_task_id_of(self.cards[-1]))
        if state is None:


            self._job_timer.stop()
            return
        if state[0] != "running":
            self.end_job()
            return
        percent = state[1]
        if percent != self._job_percent and time.monotonic() - self._server_at > _SERVER_FRESH_S:
            self._job_percent = percent
            self._set_progress(progress_words(percent))

    def follows_job(self) -> bool:

        return self._job_timer.isActive()

    def end_job(self) -> None:

        if self._job_over:
            return
        self._job_over = True
        self._job_timer.stop()
        if self.cards[-1].ok is True and is_background_job(
                self.cards[-1].summary, getattr(self.cards[-1], "detail", "")):
            self.refresh()
        if callable(self.on_job_end):
            self.on_job_end()

    def refresh(self) -> None:
        first = self.cards[0]
        last = self.cards[-1]
        calling = any(card.ok is None and not getattr(card, "ended", "") for card in self.cards)
        job = not calling and self.job_live()
        running = calling or job
        if not running:
            self._progress_text = ""
        if calling and not self._call_started:
            self._call_started = time.monotonic()
            self._call_tick.start()
        elif not calling and self._call_started:
            self._call_started = 0.0
            self._call_stage = ""
            self._call_source = ""
            self._call_count = (None, None, False)
            self._call_tick.stop()
        if job and not self._job_timer.isActive() and not self._job_looked and _task_id_of(last):
            self._job_looked = True
            self._job_timer.start()
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
        recovered, trailing = self._sync_retries()
        failed = not running and last.ok is False and ended not in ("denied", "stopped") \
            and failures >= self.repeats
        self._tries_count = 0 if calling else recovered
        self._sync_tries_button()
        declined = failed or ended in ("denied", "stopped")
        self._label.setStyleSheet(_SECOND_QSS if declined else _MEDIUM_QSS)
        colour = first.glyph_colour() if callable(getattr(first, "glyph_colour", None)) \
            else qcolor(INK_2)
        if declined:


            mark = ("lock" if self.blocked() else "lu.x") if failed else "lu.minus"
            self._icon.setPixmap(pixmap_for(self, mark, GLYPH_PX, qcolor(INK_3)))
        else:
            logo = self._logo_url(first)
            if logo != self._icon_logo:
                self._icon_logo = logo
                if logo and not self._logo_hooked:
                    from .library.pictures import store

                    store().ready.connect(self._on_logo_ready)
                    self._logo_hooked = True
            self._paint_icon(first, colour)
        n = self.repeats
        self._count.setText(f"\u00d7 {n}")
        self._count.setVisible(n > 1)
        self._results = [] if calling else self._search_results(last)
        self._sync_results()


        self._sync_outcome(running, failed, ended, trailing if trailing and not failed else 0)
        self._fit_verb()
        self._opens = not calling and (
            bool(self._results) or self._tries_count > 0
            or any(card.opens() and not getattr(card, "recovered", False) for card in self.cards))
        self._head.setCursor(Qt.CursorShape.PointingHandCursor if self._opens
                             else Qt.CursorShape.ArrowCursor)
        if not self._opens and self._open:
            self.set_open(False)
        for card in self.cards:


            self._sync_card(card, card.is_expanded())


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
    def _logo_url(card: ToolCard) -> str:

        connector = getattr(card, "connector", None)
        url = str(connector.get("logo_url") or "") if isinstance(connector, dict) else ""
        return url if url.startswith("https://") else ""

    def _paint_icon(self, card: ToolCard, colour) -> None:


        connector = getattr(card, "connector", None)
        if isinstance(connector, dict) and connector.get("id"):
            self._icon.setPixmap(family_pixmap(connector, GLYPH_SLOT_PX, widget_pixel_ratio(self), False))
            return
        self._icon.setPixmap(pixmap_for(self, str(card.glyph or "lu.cog"), GLYPH_PX, colour))

    def _on_logo_ready(self, url: str) -> None:
        if url and url == self._icon_logo and self.cards and self._icon.isVisible():
            self.refresh()

    @staticmethod
    def _search_results(card: ToolCard) -> list:
        if card.name == "fetch_text" and card.ok:

            link = str((card.args or {}).get("url") or "") if isinstance(card.args, dict) else ""
            if not link.startswith(("https://", "http://")):
                return []
            try:
                title = str((json.loads(card.detail or "{}") or {}).get("title") or "")
            except (ValueError, AttributeError):
                title = ""
            host = source_host(link)
            return [(" ".join(title.split()) or host, host if title else "", {}, link)]
        if not is_search_tool(card.name) or not card.ok:
            return []

        connector = getattr(card, "connector", None)
        own = connector if isinstance(connector, dict) and connector.get("id") else {}
        return [(name, host, family or own, link) for name, host, family, link in search_results(card.detail)]

    def _sync_results(self) -> None:
        while self._results_col.count():
            item = self._results_col.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        shown = self._results if self._results_open else self._results[:_RESULTS_SHOWN]
        for name, host, family, link in shown:
            self._results_col.addWidget(_ResultRow(name, host, family, self._results_host, link))
        more = len(self._results) - _RESULTS_SHOWN
        if more > 0:
            text = self.tr("Show less") if self._results_open else self.tr("+{n} more").format(n=more)
            label = _MoreRow(text, self._toggle_results, self._results_host)
            label.setObjectName("resultMore")
            label.setStyleSheet(_HINT_QSS)
            label.setFixedHeight(22)
            self._results_col.addWidget(label)
        self._results_host.setVisible(bool(self._results))

    def _toggle_results(self) -> None:
        self._results_open = not self._results_open

        QTimer.singleShot(0, self._sync_results)

    def _sync_outcome(self, running: bool, failed: bool, ended: str = "", some_failed: int = 0) -> None:

        text = ""
        if failed and self.blocked():
            text = self.tr("Blocked to protect your credentials")
        elif failed:
            text = self.tr("did not work")
        elif ended == "denied":
            text = self.tr("denied")
        elif ended == "stopped":
            text = self.tr("stopped")
        elif running:
            text = ""
        elif some_failed:
            text = self.tr("{failed} of {total} did not work").format(
                failed=some_failed, total=self.repeats)
        elif self._results and is_search_tool(self.cards[-1].name):
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


        self._outcome_whole = bool(text) and (running or (failed and self.blocked()))
        whole = self._outcome.sizeHint().width()

        self._outcome_floor = (whole if self._outcome_whole else min(_OUTCOME_MIN_PX, whole)) if text else 0
        self._outcome.setMinimumWidth(self._outcome_floor)
        self._outcome.setVisible(bool(text))
        self._sync_sub(running)

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

    def ended_well(self) -> bool:


        return bool(self.cards) and self.cards[-1].ok is True

    def described(self) -> str:


        last = self.cards[-1] if self.cards else None
        if last is None or not _source_text(last.name, last.args)[0]:
            return ""
        return self.secondary()

    def blocked(self) -> bool:

        return bool(self.cards) and bool(getattr(self.cards[-1], "blocked", False))

    def failed(self) -> bool:

        if not self.cards:
            return False
        last = self.cards[-1]
        return last.ok is False and getattr(last, "ended", "") not in ("denied", "stopped")

    def cleanup(self) -> None:
        self._spinner.finish()
        if self._logo_hooked:
            from .library.pictures import store

            try:
                store().ready.disconnect(self._on_logo_ready)
            except (RuntimeError, TypeError):
                pass
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


__all__ = ["SEARCH_TOOLS", "ActivityRow", "is_search_tool",
           "search_results", "secondary_of"]

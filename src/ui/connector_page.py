# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

































from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QPainter
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .connectors_page import mark_box
from .external_links import open_external_url
from .icons import pixmap_for
from .library import common as C
from .library.common import link_html
from .library.parts import Breadcrumb, edge, label, paragraph, primary_button
from .library.pictures import watch_scroll
from .library.pressable import Pressable, labels_through
from .widgets import ElidedLabel


_BOX_PX, _BOX_RADIUS = 64, 14

_DATASET_H = 40

_PROMPTS = 3
_FACT_W = 140

_NARROW_W = 460


_EXAMPLES = 3


class ConnectorPage(QWidget):



    crumb_requested = pyqtSignal(int)

    prompt_chosen = pyqtSignal(str, object)
    example_opened = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._id = ""
        self._coverage = ""
        self._url = ""
        self._name = ""
        self._chip: dict = {}
        self._tiles: list = []
        self._cases: list = []
        self._examples_host: QWidget | None = None
        self._head_row: QHBoxLayout | None = None
        self._head_words: QVBoxLayout | None = None
        self._head_button: QPushButton | None = None
        self._datasets_host: QWidget | None = None
        self._datasets: list = []
        self._prompt_rows: list = []
        self._reply = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(C.scroll_qss())
        C.keep_gutter(self._scroll)
        self._body = QWidget(self._scroll)
        self._page = QVBoxLayout(self._body)
        self._page.setSpacing(0)
        self._crumbs = Breadcrumb(self._body)
        self._crumbs.crumb.connect(self.crumb_requested.emit)
        self._page.addWidget(self._crumbs)
        self._column = QWidget(self._body)
        self._col = QVBoxLayout(self._column)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)
        self._page.addWidget(self._column)
        self._page.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)
        self._scroll.viewport().installEventFilter(self)
        watch_scroll(self, self._scroll.verticalScrollBar())

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._fit()
        return super().eventFilter(obj, event)

    def _viewport_width(self) -> int:



        viewport = self._scroll.viewport().width()
        if not self._scroll.isVisible() or viewport < C.px(200):
            viewport = self.width() - C.px(10)
        return viewport

    def _fit(self) -> None:
        viewport = self._viewport_width()
        room = viewport - 2 * C.px(C.SPACE_4)
        width = max(C.px(240), min(room, C.px(C.COLUMN_W)))
        side = max(C.px(C.SPACE_4), (viewport - width) // 2)

        edge_px = C.px(C.EDGE)
        self._page.setContentsMargins(side - edge_px, 0, side - edge_px,
                                      C.px(C.SPACE_5))
        if self._tiles:
            gap = C.px(C.TILE_GAP)
            tile_w = max(1, (width - (_EXAMPLES - 1) * gap) // _EXAMPLES)
            for tile in self._tiles:
                tile.set_tile_width(tile_w)
        self._place_button(width < C.px(_NARROW_W))

    def _place_button(self, narrow: bool) -> None:

        button, row, words = self._head_button, self._head_row, self._head_words
        if button is None or row is None or words is None:
            return
        under = words.indexOf(button) >= 0
        if narrow == under:
            return
        (words if under else row).removeWidget(button)
        if narrow:
            words.addSpacing(C.px(C.SPACE_2))
            words.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        else:

            last = words.itemAt(words.count() - 1)
            if last is not None and last.spacerItem() is not None:
                words.removeItem(last)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)



    def _clear(self) -> None:
        self._tiles = []
        self._prompt_rows = []
        self._cases = []
        self._examples_host = None
        self._datasets_host = None
        self._datasets = []
        self._datasets_state = ""
        from ..core import source_rows

        source_rows.abort(self._reply)
        self._reply = None
        self._head_row = self._head_words = self._head_button = None
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _gap(self, space: int) -> None:
        self._col.addSpacing(C.px(space))

    def _head(self, detail: dict, subtitle: str, button: QPushButton | None) -> None:

        name = str(detail.get("name") or detail.get("id") or "")
        self._crumbs.set_trail([self.tr("Data sources"), name])
        box = QWidget(self._column)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(C.px(C.SPACE_2))
        row.addWidget(mark_box(box, detail, _BOX_PX, _BOX_RADIUS), 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(C.px(2))
        words.addStretch(1)
        words.addWidget(label(box, name, C.TITLE_PX, C.T.text, C.MEDIUM, wrap=True))
        if subtitle:
            words.addWidget(label(box, subtitle, C.LEAD_PX, C.T.text_2, wrap=True))
        words.addStretch(1)
        row.addLayout(words, 1)
        if button is not None:
            button.setParent(box)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        self._head_row, self._head_words, self._head_button = row, words, button
        self._col.addWidget(edge(box))

    def _site(self, detail: dict) -> str:

        for key in ("homepage", "url"):
            value = str(detail.get(key) or "")
            if value.startswith("https://"):
                return value
        return ""

    def set_trail(self, names: list) -> None:

        self._crumbs.set_trail(names)

    def _chip_for(self, detail: dict) -> dict:
        value = str(detail.get("id") or "")
        if not value or value == "__other__":
            return {}
        return {"kind": "source", "label": str(detail.get("name") or value), "value": value,
                "glyph": str(detail.get("glyph") or "globe")}

    def _try_button(self, first: str) -> QPushButton | None:


        if not self._chip:
            return None
        button = primary_button(self._column, self.tr("Try in chat"))
        button.setToolTip(first or self.tr("Puts @{name} in the chat box, for a question of your own.")
                          .format(name=self._name))
        button.clicked.connect(lambda _c=False, t=first: self.prompt_chosen.emit(t, dict(self._chip)))
        return button

    def _prompts_panel(self, prompts: list) -> QFrame:

        panel = QFrame(self._column)
        panel.setObjectName("sourcePrompts")
        panel.setStyleSheet(f"QFrame#sourcePrompts {{ background: {C.T.panel}; border: none;"
                            f" border-radius: {C.px(C.RADIUS_HERO)}px; }}")
        col = QVBoxLayout(panel)
        col.setContentsMargins(C.px(8), C.px(8), C.px(8), C.px(8))
        col.setSpacing(0)
        for text in prompts:
            item = _PromptRow(text, panel)
            item.clicked.connect(lambda t=text: self.prompt_chosen.emit(t, dict(self._chip)))
            col.addWidget(item)
            self._prompt_rows.append(item)
        return panel

    def _paragraph(self, text: str) -> QLabel:

        return edge(label(self._column, text, C.BODY_PX, C.T.text_2, wrap=True))

    def _about(self, summary: str) -> QWidget:

        return edge(paragraph(self._column, summary))

    def set_connector(self, detail: dict, cases: list | None = None) -> None:

        self._clear()
        detail = dict(detail or {})
        self._id = str(detail.get("id") or "")
        self._url = str(detail.get("url") or "")
        self._name = str(detail.get("name") or self._id)
        self._coverage = str(detail.get("coverage") or "")
        self._chip = self._chip_for(detail)
        self._scroll.verticalScrollBar().setValue(0)




        cases = [c for c in (cases or [])
                 if str((list(getattr(c, "connectors", ()) or []) or [""])[0]) == self._id]
        self._cases = cases


        prompts = [] if cases else [
            str(p).strip() for p in (detail.get("prompts") or []) if str(p).strip()][:_PROMPTS]
        first = str(getattr(cases[0], "prompt", "") or "") if cases else (prompts[0] if prompts else "")
        tagline = str(detail.get("tagline") or "").strip()
        subtitle = tagline or str(detail.get("coverage") or "").strip() or _host(self._site(detail))
        self._head(detail, subtitle, self._try_button(first))
        if cases:
            self._gap(C.SPACE_5)
            self._col.addWidget(self._examples(cases))
        elif prompts and self._chip:
            self._gap(C.SPACE_5)
            self._col.addWidget(self._prompts_panel(prompts))
        summary = str(detail.get("summary") or "").strip()
        if summary and summary != subtitle:
            self._gap(C.SPACE_5)
            self._col.addWidget(self._about(summary))
        facts = self._facts(detail)
        if facts is not None:
            self._gap(C.SPACE_5)
            self._col.addWidget(facts)
        self._datasets_section(int(detail.get("datasets") or 0))
        self._fit()

    def _datasets_section(self, count: int) -> None:





        from ..core import source_rows

        if not self._id or not source_rows.available():
            return
        self._gap(C.SPACE_5)
        toggle = _Disclosure(self.tr("Datasets ({n})").format(n=count) if count
                             else self.tr("Datasets"), self._column)
        toggle.setObjectName("connectorDatasets")
        self._col.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self._datasets_host = QWidget(self._column)
        holder = QVBoxLayout(self._datasets_host)
        holder.setContentsMargins(0, C.px(4), 0, 0)
        holder.setSpacing(0)
        self._datasets_host.hide()
        self._col.addWidget(self._datasets_host)
        self._datasets_state = "loading"
        self._fetch_datasets()

        def flip() -> None:
            showing = not self._datasets_host.isVisible()
            self._datasets_host.setVisible(showing)
            toggle.set_open(showing)
            self._fit()

        toggle.clicked.connect(flip)

    def _fetch_datasets(self) -> None:
        from ..core import source_rows

        self._datasets_state = "loading"
        self._fill_datasets()
        family = self._id
        self._reply = source_rows.fetch(
            lambda rows, error, f=family: self._on_datasets(f, rows, error), family=family)

    def _empty_datasets_host(self):
        layout = self._datasets_host.layout()
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        return layout

    def _fill_datasets(self) -> None:

        if self._datasets_host is None:
            return
        self._datasets_host.setUpdatesEnabled(False)
        try:
            layout = self._empty_datasets_host()
            if self._datasets_state == "loading":
                for _ in range(5):
                    layout.addWidget(_SkeletonRow(self._datasets_host))
            elif self._datasets_state == "error":
                line = label(self._datasets_host, "", C.BODY_PX, C.T.text_2)
                line.setTextFormat(Qt.TextFormat.RichText)
                line.setText(self.tr("The datasets could not be loaded.") + " "
                             + link_html("retry:", self.tr("Retry")))
                line.linkActivated.connect(lambda _h: self._fetch_datasets())
                layout.addWidget(edge(line))
            elif not self._datasets:
                text = self._paragraph(self.tr("No dataset listed yet."))
                text.setParent(self._datasets_host)
                layout.addWidget(text)
            for row in self._datasets if self._datasets_state == "ready" else []:
                item = _DatasetRow(row, self._datasets_host, self._coverage)
                example = str(row.get("example") or "").strip()
                if example:
                    item.clicked.connect(lambda e=example: self.prompt_chosen.emit(e, dict(self._chip)))
                layout.addWidget(item)
        finally:
            self._datasets_host.setUpdatesEnabled(True)
        self._fit()

    def _on_datasets(self, family: str, rows: list, error: str) -> None:
        if family != self._id or self._datasets_host is None:
            return
        self._reply = None
        from ..core import source_rows

        country, _language = source_rows.user_place()
        self._datasets = sorted(rows or [], key=lambda r: source_rows.relevance(r, country)[:2])
        self._datasets_state = "error" if error else "ready"
        self._fill_datasets()

    def _examples(self, cases: list) -> QWidget:


        from .library.cards import ExampleTile

        host = QWidget(self._column)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(C.px(C.SPACE_2))
        grid_host = edge(QWidget(host))
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))
        grid.setVerticalSpacing(C.px(C.SPACE_3))
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        extra = []
        for index, case in enumerate(cases):
            tile = ExampleTile(case, grid_host)
            tile.clicked.connect(lambda c=case: self.example_opened.emit(c))
            grid.addWidget(tile, index // _EXAMPLES, index % _EXAMPLES,
                           Qt.AlignmentFlag.AlignTop)
            self._tiles.append(tile)
            if index >= _EXAMPLES:
                tile.hide()
                extra.append(tile)
        col.addWidget(grid_host)
        if extra:
            toggle = _Disclosure(self.tr("See all %n examples", "", len(cases)), host)
            toggle.setObjectName("connectorExamplesAll")

            def flip() -> None:
                showing = not extra[0].isVisible()
                for tile in extra:
                    tile.setVisible(showing)
                toggle.set_open(showing)
                self._fit()

            toggle.clicked.connect(flip)
            col.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
        return host

    def _facts(self, detail: dict) -> QWidget | None:


        site = self._site(detail)
        first = [
            (self.tr("Coverage"), str(detail.get("coverage") or ""), False),
            (self.tr("Licence"), str(detail.get("licence") or ""), False),
            (self.tr("Website"), link_html(site, _bare_host(site)) if site else "", True),
        ]
        more = [
            (self.tr("Attribution"), str(detail.get("attribution") or ""), False),
            (self.tr("What it needs"), self._status_line(detail), False),
            (self.tr("Key"), self.tr("Held by TerraLab's server, you need none")
             if detail.get("key_held") else "", False),
            (self.tr("Good to know"), str(detail.get("caveat") or ""), False),
        ]
        terms = str(detail.get("terms_url") or "")
        if terms.startswith("https://") and terms.rstrip("/") not in {
                self._url.rstrip("/"), site.rstrip("/")}:
            more.append((self.tr("Licence and terms"), link_html(terms, _host(terms)), True))
        first = [r for r in first if str(r[1]).strip()]
        more = [r for r in more if str(r[1]).strip()]
        if not first and not more:
            return None
        host = QWidget(self._column)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        if first:
            col.addWidget(edge(self._fact_table(first, host)))
        if more:
            table = edge(self._fact_table(more, host))
            if not first:
                col.addWidget(table)
                return host
            table.hide()
            toggle = _Disclosure(self.tr("More details"), host)
            toggle.setObjectName("connectorMoreDetails")

            def flip() -> None:
                showing = not table.isVisible()
                table.setVisible(showing)
                toggle.set_open(showing)
                self._fit()

            toggle.clicked.connect(flip)
            col.addSpacing(C.px(4))
            col.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
            col.addWidget(table)
        return host

    def _fact_table(self, rows: list, parent: QWidget) -> QWidget:
        host = QWidget(parent)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        for index, (name, value, rich) in enumerate(rows):
            if index:
                rule = QFrame(host)
                rule.setFixedHeight(1)
                rule.setStyleSheet(f"background: {C.T.border}; border: none;")
                col.addWidget(rule)
            line = QHBoxLayout()
            line.setContentsMargins(0, C.px(10), 0, C.px(10))
            line.setSpacing(C.px(C.SPACE_2))
            key = label(host, name, C.READ_PX, C.T.text_2, wrap=True)
            key.setFixedWidth(C.px(_FACT_W))
            line.addWidget(key, 0, Qt.AlignmentFlag.AlignTop)


            text = paragraph(host, str(value), rich=rich)
            text.setMaximumWidth(16777215)
            text.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            if rich:
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                text.setOpenExternalLinks(False)
                text.linkActivated.connect(self._open_link)
            line.addWidget(text, 1)
            col.addLayout(line)
        return host

    def _open_link(self, href: str) -> None:
        if str(href or "").startswith("https://"):
            open_external_url(str(href), parent=self)

    def _status_line(self, detail: dict) -> str:






        status = str(detail.get("status") or "").strip()
        served = str(detail.get("status_label") or "").strip()
        if served:
            return served[:200]
        from .shared import served_label_map

        served_map = served_label_map("status_labels")
        if status in served_map:
            return served_map[status]
        return {
            "ready": self.tr("Nothing. Open data, no account and no key."),
            "key": self.tr("Your own account with the provider: it will ask for a key."),
            "community": self.tr("Nothing, but it is run by volunteers. Expect it to be slower or "
                                 "stricter about how much you can ask for."),
        }.get(status, "")



    def tiles(self) -> list:

        return list(self._prompt_rows) + [t for t in self._tiles if not t.isHidden()]

    def connector_id(self) -> str:
        return self._id


def _host(url: str) -> str:

    try:
        from urllib.parse import urlsplit

        return urlsplit(str(url)).hostname or str(url)
    except ValueError:
        return str(url)


def _bare_host(url: str) -> str:
    host = _host(url) if url else ""
    return host[4:] if host.startswith("www.") else host


class _PromptRow(Pressable):


    def __init__(self, text: str, parent=None):
        super().__init__(parent, radius=C.px(10), hover=lambda: C.T.selected, press=lambda: C.T.press)
        self.setToolTip(self.tr("Puts this request in the chat box, ready to edit."))
        row = QHBoxLayout(self)
        row.setContentsMargins(C.px(12), C.px(10), C.px(12), C.px(10))
        row.setSpacing(C.px(C.SPACE_2))
        words = label(self, text, C.BODY_PX, C.T.text, wrap=True)
        row.addWidget(words, 1)
        arrow = QLabel(self)
        arrow.setPixmap(pixmap_for(arrow, "chevron_right", 16, QColor(C.T.text_2)))
        arrow.setStyleSheet("background: transparent;")
        row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self.setAccessibleName(text)


class _Disclosure(Pressable):


    def __init__(self, text: str, parent=None):
        super().__init__(parent, radius=C.px(8))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(C.px(C.EDGE), C.px(4), C.px(C.EDGE), C.px(4))
        row.setSpacing(C.px(4))
        row.addWidget(label(self, text, C.BODY_PX, C.T.text, C.MEDIUM))
        self._chevron = QLabel(self)
        self._chevron.setStyleSheet("background: transparent;")
        row.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self.setAccessibleName(text)
        self.set_open(False)

    def set_open(self, open_: bool) -> None:
        self._chevron.setPixmap(pixmap_for(self._chevron, "chevron_down" if open_ else "chevron_right",
                                           16, QColor(C.T.text_2)))


class _SkeletonRow(QWidget):


    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(C.px(_DATASET_H))

    def paintEvent(self, _event):  # noqa: N802
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(C.T.tile))
            bar = C.px(12)
            painter.drawRoundedRect(C.px(C.EDGE), (self.height() - bar) // 2,
                                    int(self.width() * 0.5), bar, bar // 2, bar // 2)
        finally:
            painter.end()


class _DatasetRow(Pressable):





    def __init__(self, row: dict, parent=None, source_coverage: str = ""):
        super().__init__(parent, radius=C.px(8))
        from .connectors_page import short_licence

        self.setFixedHeight(C.px(_DATASET_H))
        line = QHBoxLayout(self)
        line.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        line.setSpacing(C.px(C.SPACE_2))
        title = str(row.get("title") or row.get("id") or "")
        name = ElidedLabel(title, self)
        name.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text))
        line.addWidget(name, 1, Qt.AlignmentFlag.AlignVCenter)
        coverage = str(row.get("coverage") or "").strip()
        same = coverage.casefold() == str(source_coverage or "").strip().casefold()
        facts = [coverage if coverage and not same and len(coverage) <= 24 else "",
                 short_licence(row.get("licence"))]
        note = " · ".join(f for f in facts if f)
        publisher = str(row.get("publisher") or "").strip()
        if publisher:

            line.addWidget(label(self, publisher, C.BODY_PX, C.T.text_2), 0,
                           Qt.AlignmentFlag.AlignVCenter)
        elif note:
            line.addWidget(label(self, note, C.SMALL_PX, C.T.text_2), 0,
                           Qt.AlignmentFlag.AlignVCenter)
        if str(row.get("example") or "").strip():
            self.setToolTip(self.tr("Puts this request in the chat box, ready to edit."))
        labels_through(self)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        try:
            painter.setPen(QColor(C.T.border))
            y = self.height() - 1
            painter.drawLine(C.px(C.EDGE), y, self.width() - C.px(C.EDGE), y)
        finally:
            painter.end()


__all__ = ["ConnectorPage"]



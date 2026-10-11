# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





























from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QSize, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .connectors_page import accent_of
from .external_links import open_external_url
from .icons import icon_for
from .library import common as C
from .library.common import link_html
from .library.parts import Breadcrumb, InfoTable, PageHeader, label, primary_button, section_title
from .library.pictures import watch_scroll

_MARK_PX = 52

_NARROW_W = 460

_EXAMPLES = 6

_ABOUT_CHARS = 220


class ConnectorPage(QWidget):



    crumb_requested = pyqtSignal(int)

    prompt_chosen = pyqtSignal(str, object)
    example_opened = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._id = ""
        self._url = ""
        self._name = ""
        self._chip: dict = {}
        self._tiles: list = []
        self._cases: list = []
        self._examples_host: QWidget | None = None
        self._head_row: QHBoxLayout | None = None
        self._head_words: QVBoxLayout | None = None
        self._head_button: QPushButton | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(C.scroll_qss())
        self._body = QWidget(self._scroll)
        self._page = QVBoxLayout(self._body)
        self._page.setSpacing(0)
        self._crumbs = Breadcrumb(self._body)
        self._crumbs.crumb.connect(self.crumb_requested.emit)
        self._page.addWidget(self._crumbs)
        self._page.addSpacing(C.px(C.SPACE_3))
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
        self._page.setContentsMargins(side, C.px(C.SPACE_3), side, C.px(C.SPACE_5))
        if self._tiles:
            gap = C.px(C.TILE_GAP)
            tile_w = max(1, (width - gap) // 2)
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
        self._cases = []
        self._examples_host = None
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

    def set_back_label(self, _text: str = "") -> None:
        pass

    def _mark(self, detail: dict) -> QLabel:
        from .library.pictures import wear_source_mark

        mark = QLabel(self._column)
        wear_source_mark(mark, detail, accent_of(detail.get("category")), C.px(_MARK_PX))
        return mark

    def _head(self, detail: dict, subtitle: str, button: QPushButton | None) -> None:


        name = str(detail.get("name") or detail.get("id") or "")
        self._crumbs.set_trail([self.tr("Data sources"), name])
        box = QWidget(self._column)
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(C.px(C.SPACE_3))
        row.addWidget(self._mark(detail), 0, Qt.AlignmentFlag.AlignTop)
        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(C.px(4))
        header = PageHeader(box, name, subtitle, None)
        words.addWidget(header)
        site = self._site(detail)
        if site:

            line = QHBoxLayout()
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(self._site_button(site))
            line.addStretch(1)
            words.addLayout(line)
        row.addLayout(words, 1)
        if button is not None:
            button.setParent(box)
            row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        self._head_row, self._head_words, self._head_button = row, words, button
        self._col.addWidget(box)

    def _site(self, detail: dict) -> str:

        for key in ("homepage", "url"):
            value = str(detail.get(key) or "")
            if value.startswith("https://"):
                return value
        return ""

    def _site_button(self, site: str) -> QPushButton:

        host = _host(site)
        button = QPushButton(host[4:] if host.startswith("www.") else host)
        button.setFlat(True)
        button.setIcon(icon_for(button, "external", 14, QColor(C.T.text_2)))
        button.setIconSize(QSize(C.px(14), C.px(14)))
        button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.setToolTip(self.tr("Open {host} in your browser").format(host=_host(site)))
        button.setStyleSheet(
            f"QPushButton {{ border: none; background: transparent; padding: 0;"
            f" color: {C.T.text_2}; font-size: {C.px(C.BODY_PX)}px; text-align: left; }}"
            f"QPushButton:hover {{ color: {C.T.text}; text-decoration: underline; }}")
        button.clicked.connect(lambda _c=False, url=site: self._open_link(url))
        return button

    def set_trail(self, names: list) -> None:

        self._crumbs.set_trail(names)

    def _chip_for(self, detail: dict) -> dict:
        value = str(detail.get("id") or "")
        if not value:
            return {}
        return {"kind": "source", "label": str(detail.get("name") or value), "value": value,
                "glyph": str(detail.get("glyph") or "globe")}

    def _mention_button(self) -> QPushButton | None:


        if not self._chip:
            return None
        button = primary_button(self._column, self.tr("Use in chat"))
        button.setToolTip(self.tr("Puts @{name} in the chat box, for a question of your own.")
                          .format(name=self._name))
        button.clicked.connect(lambda _c=False: self.prompt_chosen.emit("", dict(self._chip)))
        return button

    def _paragraph(self, text: str) -> QLabel:
        return label(self._column, text, C.BODY_PX, C.T.text_2, wrap=True)

    def _about(self, summary: str) -> QWidget:

        short = _lead(summary, _ABOUT_CHARS)
        if short == summary:
            return self._paragraph(summary)
        host = QWidget(self._column)
        box = QVBoxLayout(host)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(C.px(C.SPACE_2))
        text = self._paragraph(short)
        text.setParent(host)
        box.addWidget(text)
        toggle = QPushButton(self.tr("Show more"), host)
        toggle.setObjectName("connectorAboutMore")
        toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        toggle.setStyleSheet(C.ghost_qss())
        toggle.setAutoDefault(False)
        box.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)

        def flip(_checked=False):
            showing = toggle.text() == self.tr("Show more")
            text.setText(summary if showing else short)
            toggle.setText(self.tr("Show less") if showing else self.tr("Show more"))
            self._fit()

        toggle.clicked.connect(flip)
        return host

    def set_connector(self, detail: dict, cases: list | None = None) -> None:

        self._clear()
        detail = dict(detail or {})
        self._id = str(detail.get("id") or "")
        self._url = str(detail.get("url") or "")
        self._name = str(detail.get("name") or self._id)
        self._chip = self._chip_for(detail)
        self._scroll.verticalScrollBar().setValue(0)



        cases = list(cases or [])
        self._cases = cases

        tagline = str(detail.get("tagline") or "").strip() or str(detail.get("summary") or "").strip()
        self._head(detail, tagline, self._mention_button())
        if cases:
            self._gap(C.SPACE_4)
            self._col.addWidget(section_title(self._column, self.tr("Examples")))
            self._gap(12)
            self._examples_host = QWidget(self._column)
            holder = QVBoxLayout(self._examples_host)
            holder.setContentsMargins(0, 0, 0, 0)
            holder.setSpacing(C.px(C.SPACE_2))
            holder.addWidget(self._examples(cases[:_EXAMPLES]))
            if len(cases) > _EXAMPLES:
                more = QPushButton(self.tr("Show all %n examples", "", len(cases)),
                                   self._examples_host)
                more.setCursor(Qt.CursorShape.PointingHandCursor)
                more.setStyleSheet(C.ghost_qss())
                more.setAutoDefault(False)
                more.clicked.connect(self._show_all_examples)
                holder.addWidget(more, 0, Qt.AlignmentFlag.AlignLeft)
            self._col.addWidget(self._examples_host)
        summary = str(detail.get("summary") or "").strip()
        if summary and summary != tagline:
            self._gap(C.SPACE_5)
            self._col.addWidget(section_title(self._column, self.tr("About")))
            self._gap(12)
            self._col.addWidget(self._about(summary))
        self._gap(C.SPACE_5)
        self._col.addWidget(section_title(self._column, self.tr("Information")))
        self._gap(12)
        self._col.addWidget(self._information(detail))
        self._fit()

    def _show_all_examples(self) -> None:

        host = self._examples_host
        if host is None:
            return
        layout = host.layout()
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._tiles = []
        layout.addWidget(self._examples(self._cases, host))
        self._fit()

    def _examples(self, cases: list, parent: QWidget | None = None) -> QWidget:
        from .library.cards import ExampleRow

        host = QWidget(parent or self._column)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))
        grid.setVerticalSpacing(C.px(4))
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for index, case in enumerate(cases):
            tile = ExampleRow(case, host)
            tile.clicked.connect(lambda c=case: self.example_opened.emit(c))
            grid.addWidget(tile, index // 2, index % 2)
            self._tiles.append(tile)
        return host

    def _information(self, detail: dict) -> QWidget:




        host = QWidget(self._column)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(C.px(C.SPACE_2))
        first = self._table(host)
        for name, value in ((self.tr("Licence"), str(detail.get("licence") or "")),
                            (self.tr("Coverage"), str(detail.get("coverage") or ""))):
            if value.strip():
                first.add(name, value)
        count = int(detail.get("datasets") or 0)
        more = self._table(host)
        for name, value in (
            (self.tr("Datasets"), (self.tr("1 ready dataset") if count == 1
                                   else self.tr("%n ready datasets", "", count)) if count else ""),
            (self.tr("What it needs"), self._status_line(detail)),
            (self.tr("Attribution"), str(detail.get("attribution") or "")),
            (self.tr("Good to know"), str(detail.get("caveat") or "")),
        ):
            if str(value).strip():
                more.add(name, str(value))
        terms = str(detail.get("terms_url") or "")
        if terms.startswith("https://") and terms.rstrip("/") not in {
                self._url.rstrip("/"), self._site(detail).rstrip("/")}:
            more.add(self.tr("Licence and terms"), link_html(terms, _host(terms)), rich=True)
        if not first.rows():

            first.hide()
            col.addWidget(more)
            return host
        col.addWidget(first)
        if more.rows():
            toggle = QPushButton(self.tr("More details"), host)
            toggle.setObjectName("connectorMoreDetails")
            toggle.setCursor(Qt.CursorShape.PointingHandCursor)
            toggle.setStyleSheet(C.ghost_qss())
            toggle.setAutoDefault(False)
            more.hide()

            def flip() -> None:
                showing = not more.isVisible()
                more.setVisible(showing)
                toggle.setText(self.tr("Fewer details") if showing else self.tr("More details"))
                self._fit()

            toggle.clicked.connect(flip)
            col.addWidget(toggle, 0, Qt.AlignmentFlag.AlignLeft)
            col.addWidget(more)
        return host

    def _table(self, parent: QWidget) -> InfoTable:
        table = InfoTable(parent)
        table.link_activated.connect(self._open_link)
        return table

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

        return list(self._tiles)

    def connector_id(self) -> str:
        return self._id


def _host(url: str) -> str:

    try:
        from urllib.parse import urlsplit

        return urlsplit(str(url)).hostname or str(url)
    except ValueError:
        return str(url)


__all__ = ["ConnectorPage"]


def _lead(text: str, limit: int) -> str:


    if len(text) <= limit:
        return text
    ends = [i + 1 for i, ch in enumerate(text[:-1]) if ch in ".!?" and text[i + 1] == " "]
    fitting = [i for i in ends if i <= limit]
    if fitting:
        return text[:fitting[-1]]
    return text[:ends[0]] if ends else text

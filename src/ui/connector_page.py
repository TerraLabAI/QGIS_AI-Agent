# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



























from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .connectors_page import accent_of
from .external_links import open_external_url
from .library import common as C
from .library.common import link_html
from .library.parts import Breadcrumb, InfoTable, PageHeader, label, primary_button, section_title

_MARK_PX = 56

_EXAMPLES = 6


class ConnectorPage(QWidget):



    back_requested = pyqtSignal()

    prompt_chosen = pyqtSignal(str, object)
    example_opened = pyqtSignal(object)

    def __init__(self, parent=None, root: str = ""):
        super().__init__(parent)

        self._root = root
        self._id = ""
        self._url = ""
        self._name = ""
        self._chip: dict = {}
        self._tiles: list = []
        self._cases: list = []
        self._examples_host: QWidget | None = None

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
        self._crumbs.crumb.connect(lambda _i: self.back_requested.emit())
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
            tile_w = max(1, (width - 2 * gap) // 3)
            for tile in self._tiles:
                tile.set_tile_width(tile_w)



    def _clear(self) -> None:
        self._tiles = []
        self._cases = []
        self._examples_host = None
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
        self._crumbs.set_trail([self._root or self.tr("Data sources"), name])
        self._col.addWidget(self._mark(detail))
        self._gap(C.SPACE_2)
        self._col.addWidget(PageHeader(self._column, name, subtitle, button))

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
            self._col.addWidget(self._paragraph(summary))
        self._gap(C.SPACE_5)
        self._col.addWidget(section_title(self._column, self.tr("Information")))
        self._gap(12)
        self._col.addWidget(self._information(detail))
        if str(detail.get("logo_url") or "").strip():
            self._gap(C.SPACE_1)
            self._col.addWidget(label(
                self._column, self.tr("The logo belongs to its owner, who does not endorse "
                                      "AI Agent."), C.SMALL_PX, C.T.text_2, wrap=True))
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
        from .library.cards import ExampleTile

        host = QWidget(parent or self._column)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))
        grid.setVerticalSpacing(C.px(C.SPACE_3))
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for index, case in enumerate(cases):
            tile = ExampleTile(case, host)
            tile.clicked.connect(lambda c=case: self.example_opened.emit(c))
            grid.addWidget(tile, index // 3, index % 3)
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
        if self._url.startswith("https://"):
            more.add(self.tr("Website"), link_html(self._url, _host(self._url)), rich=True)
        terms = str(detail.get("terms_url") or "")
        if terms.startswith("https://") and terms != self._url:
            more.add(self.tr("Terms"), link_html(terms, _host(terms)), rich=True)
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



    def connector_id(self) -> str:
        return self._id


def _host(url: str) -> str:

    try:
        from urllib.parse import urlsplit

        return urlsplit(str(url)).hostname or str(url)
    except ValueError:
        return str(url)


__all__ = ["ConnectorPage"]

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



























from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QBoxLayout,
    QFrame,
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..shared import tr
from ..use_cases import match_cases
from . import common as C
from . import keys
from .cards import ExampleRow, ExampleTile
from .parts import (
    Breadcrumb,
    EmptyState,
    PageHeader,
    SectionHead,
    label,
    search_pill,
)
from .pictures import watch_scroll



_SEARCH_DEBOUNCE_MS = 120





COLUMNS = 2
_SHELF_TILES = 4


class Carousel(QWidget):


    def __init__(self, title: str, cases: list, opener, see_all, parent=None):
        super().__init__(parent)
        self.tiles: list = []
        self._width = 0
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(C.px(C.SPACE_2))
        head = SectionHead(title, self)
        head.clicked.connect(see_all)
        col.addWidget(head)
        holder = QWidget(self)
        grid = QGridLayout(holder)
        grid.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))

        grid.setVerticalSpacing(0)
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for index, case in enumerate(cases[:_SHELF_TILES]):
            tile = ExampleTile(case, holder)
            tile.clicked.connect(lambda c=case: opener(c))
            grid.addWidget(tile, index // COLUMNS, index % COLUMNS)
            self.tiles.append(tile)
        col.addWidget(holder)

    def set_width(self, width: int) -> None:

        if width == self._width:
            return
        self._width = width
        gap = C.px(C.TILE_GAP)
        tile_w = max(1, (width - (COLUMNS - 1) * gap) // COLUMNS)
        for tile in self.tiles:
            tile.set_tile_width(tile_w)




_FEATURED_SHARE = 0.6
_FEATURED_SIDE_W = 600
_FEATURED_LIST = 3


class FeaturedShelf(QWidget):


    def __init__(self, title: str, cases: list, opener, see_all, parent=None):
        super().__init__(parent)
        self.tiles: list = []
        self._width = 0
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(C.px(C.SPACE_2))
        head = SectionHead(title, self)
        head.clicked.connect(see_all)
        col.addWidget(head)
        body = QWidget(self)
        self._row = QBoxLayout(QBoxLayout.Direction.LeftToRight, body)
        self._row.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        self._row.setSpacing(C.px(C.TILE_GAP))
        self._lead = ExampleTile(cases[0], body)
        self._lead.clicked.connect(lambda c=cases[0]: opener(c))
        self._row.addWidget(self._lead, 0, Qt.AlignmentFlag.AlignTop)
        self.tiles.append(self._lead)
        self._list = QWidget(body)
        rows = QVBoxLayout(self._list)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(C.px(4))
        for case in cases[1:1 + _FEATURED_LIST]:
            item = ExampleRow(case, self._list)
            item.clicked.connect(lambda c=case: opener(c))
            rows.addWidget(item)
            self.tiles.append(item)
        rows.addStretch(1)
        self._row.addWidget(self._list, 1, Qt.AlignmentFlag.AlignTop)
        col.addWidget(body)

    def set_width(self, width: int) -> None:
        if width == self._width:
            return
        self._width = width
        side = width >= C.px(_FEATURED_SIDE_W) and len(self.tiles) > 1
        self._row.setDirection(QBoxLayout.Direction.LeftToRight if side
                               else QBoxLayout.Direction.TopToBottom)
        gap = C.px(C.TILE_GAP)
        lead = round((width - gap) * _FEATURED_SHARE) if side else width
        self._lead.set_tile_width(lead)
        self._list.setFixedWidth(width - gap - lead if side else width)


class ExamplesHome(QWidget):


    case_opened = pyqtSignal(object)
    query_changed = pyqtSignal(str)
    ask_requested = pyqtSignal(str)
    group_requested = pyqtSignal(str)

    def __init__(self, cases: list, groups: list, parent=None, looks: dict | None = None):
        super().__init__(parent)
        self._cases = list(cases)
        self._looks = dict(looks or {})
        self._groups = list(groups)
        self._group = ""
        self._carousels: list = []
        self._grid_tiles: list = []
        self._columns = COLUMNS

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("libraryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(C.scroll_qss())
        C.keep_gutter(self._scroll)
        self._body = QWidget(self._scroll)
        self._page = QVBoxLayout(self._body)
        self._page.setSpacing(0)
        self._crumbs = Breadcrumb(self._body)
        self._crumbs.crumb.connect(lambda _i: self.group_requested.emit(""))
        self._crumbs.hide()
        self._page.addWidget(self._crumbs)
        self._search = search_pill(self._body, "librarySearch", tr("Search examples"))
        self._header = PageHeader(self._body, tr("Examples"), "", self._search)
        self._page.addWidget(self._header)
        self._page.addSpacing(C.px(C.SPACE_5))
        self._content = QWidget(self._body)
        self._content_col = QVBoxLayout(self._content)
        self._content_col.setContentsMargins(0, 0, 0, 0)


        self._content_col.setSpacing(C.px(C.SPACE_3))
        self._page.addWidget(self._content)
        self._page.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)
        self._scroll.viewport().installEventFilter(self)
        watch_scroll(self, self._scroll.verticalScrollBar())

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self.repaint_tiles)
        self._search.textChanged.connect(self._debounce.start)
        self._built_width = 0

    def search_field(self):
        return self._search

    def query(self) -> str:
        return self._search.text().strip()

    def group(self) -> str:
        return self._group

    def set_group(self, key: str) -> None:

        self._group = str(key or "")
        if self.query():
            self._search.blockSignals(True)
            self._search.clear()
            self._search.blockSignals(False)
            self._debounce.stop()
        self.repaint_tiles()

    def clear_search(self) -> bool:
        if not self._search.text():
            return False
        self._search.clear()
        self._debounce.stop()
        self.repaint_tiles()
        return True

    def tiles(self) -> list:
        if self._carousels:
            return [tile for carousel in self._carousels for tile in carousel.tiles]
        return list(self._grid_tiles)

    def reveal(self, tile) -> None:
        keys.reveal(tile)

    def _label_of(self, key: str) -> str:
        return next((text for k, text in self._groups if k == key), key)

    def repaint_tiles(self) -> None:
        self._clear()
        self._crumbs.setVisible(bool(self._group) and not self.query())


        self._header.set_hero(not self._group or bool(self.query()))
        if self._group:
            self._crumbs.set_trail([tr("Examples"), self._label_of(self._group)])
        query = self.query()
        self.query_changed.emit(query)
        if query:
            hits = match_cases(self._cases, query)
            self._header.set_text(tr("Examples"), self._home_line())
            if hits:
                title = tr("1 result") if len(hits) == 1 else tr("%n results", "", len(hits))
                self._grid(title, hits)
            else:
                self._content_col.addWidget(self._no_hit(query))
        elif self._group:
            cases = [c for c in self._cases if c.group == self._group and c.listed]
            count = tr("1 example") if len(cases) == 1 else tr("%n examples", "", len(cases))
            self._header.set_text(self._label_of(self._group), count)
            self._grid("", cases)
        else:
            self._header.set_text(tr("Examples"), self._home_line())
            for key, text in self._groups:
                cases = [c for c in self._cases if c.group == key and c.listed]
                if not cases:
                    continue
                shelf = FeaturedShelf if not self._carousels else Carousel
                carousel = shelf(text, cases, self.case_opened.emit,
                                 lambda k=key: self.group_requested.emit(k), self._content)
                self._content_col.addWidget(carousel)
                self._carousels.append(carousel)
            if not self._carousels:
                empty = QLabel(tr("No examples match"), self._content)
                empty.setObjectName("libEmpty")
                empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._content_col.addWidget(empty)
        self._built_width = 0
        self._fit()
        self._scroll.verticalScrollBar().setValue(0)

    def _no_hit(self, query: str) -> QWidget:
        actions = [(tr("Clear search"), self._clear_and_focus)]
        first = next((key for key, _ in self._groups
                      if any(c.group == key and c.listed for c in self._cases)), "")
        if first:
            actions.append((tr("Browse {group}").format(group=self._label_of(first)),
                            lambda k=first: self.group_requested.emit(k)))
        actions.append((tr("Try in chat"), lambda q=query: self.ask_requested.emit(q)))
        return EmptyState(self._content, query,
                          tr("No example matches these words. The AI agent may still do it."), actions)

    def _clear_and_focus(self) -> None:
        self.clear_search()
        self._search.setFocus()

    def _home_line(self) -> str:
        return tr("Real tasks the AI agent does from one sentence.")

    def _grid(self, title: str, cases: list) -> None:
        holder = QWidget(self._content)
        col = QVBoxLayout(holder)
        col.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        col.setSpacing(C.px(C.SPACE_2))
        if title:
            col.addWidget(label(holder, title, C.SECTION_PX, C.T.text, C.MEDIUM))
        host = QWidget(holder)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))
        grid.setVerticalSpacing(0)
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for index, case in enumerate(cases):
            tile = ExampleTile(case, host)
            tile.clicked.connect(lambda c=case: self.case_opened.emit(c))
            grid.addWidget(tile, index // self._columns, index % self._columns)
            self._grid_tiles.append(tile)
        col.addWidget(host)
        self._content_col.addWidget(holder)

    def _clear(self) -> None:
        self._carousels = []
        self._grid_tiles = []
        while self._content_col.count():
            item = self._content_col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def column_width(self) -> int:
        room = self._scroll.viewport().width() - 2 * C.px(C.SPACE_4)
        return max(C.px(C.TILE_MIN_W), min(room, C.px(C.COLUMN_W)))

    def _fit(self) -> None:

        viewport = self._scroll.viewport().width()
        width = self.column_width()
        side = max(C.px(C.SPACE_4), (viewport - width) // 2) - C.px(C.EDGE)
        top = 0 if self._crumbs.isVisibleTo(self) else C.px(C.TOP_BAR_H)
        self._page.setContentsMargins(side, top, side, C.px(C.SPACE_5))
        if width == self._built_width:
            return
        self._built_width = width
        for carousel in self._carousels:
            carousel.set_width(width)
        if self._grid_tiles:
            gap = C.px(C.TILE_GAP)
            tile_w = max(1, (width - (self._columns - 1) * gap) // self._columns)
            for tile in self._grid_tiles:
                tile.set_tile_width(tile_w)

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Type.Resize and watched is self._scroll.viewport():
            self._fit()
        return super().eventFilter(watched, event)

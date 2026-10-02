# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..shared import tr
from ..use_cases import match_cases
from . import common as C
from .cards import ExampleTile, tile_height
from .parts import ArrowButton, PageHeader, category_badge, search_pill, section_title, text_button



_SEARCH_DEBOUNCE_MS = 120


class _SideScroll(QScrollArea):


    def wheelEvent(self, event):  # noqa: N802
        delta = event.angleDelta()
        if abs(delta.x()) > abs(delta.y()):
            super().wheelEvent(event)
            return
        event.ignore()


class Carousel(QWidget):


    def __init__(self, title: str, cases: list, opener, see_all, parent=None,
                 look: tuple = ("", "")):
        super().__init__(parent)
        self.tiles: list = []
        self._width = 0
        self._page = 0
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(C.px(12))
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(C.px(8))
        head.addWidget(category_badge(self, look[0], look[1], C.px(24)), 0,
                       Qt.AlignmentFlag.AlignVCenter)
        head.addSpacing(C.px(2))
        head.addWidget(section_title(self, title), 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        more = text_button(self, tr("See all"))
        more.clicked.connect(lambda _c=False: see_all())
        head.addWidget(more, 0, Qt.AlignmentFlag.AlignVCenter)
        self._prev = ArrowButton("chevron_left", self)
        self._prev.setToolTip(tr("Previous"))
        self._prev.clicked.connect(self.previous_page)
        self._next = ArrowButton("chevron_right", self)
        self._next.setToolTip(tr("Next"))
        self._next.clicked.connect(self.next_page)
        head.addWidget(self._prev, 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self._next, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(head)
        self._scroll = _SideScroll(self)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setWidgetResizable(False)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._strip = QWidget()
        self._strip.setStyleSheet("background: transparent;")
        row = QHBoxLayout(self._strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(C.px(C.TILE_GAP))
        for case in cases:
            tile = ExampleTile(case, self._strip)
            tile.clicked.connect(lambda c=case: opener(c))
            row.addWidget(tile)
            self.tiles.append(tile)
        self._scroll.setWidget(self._strip)
        self._scroll.viewport().setStyleSheet("background: transparent;")
        col.addWidget(self._scroll)
        bar = self._scroll.horizontalScrollBar()
        bar.valueChanged.connect(self._arrows)
        bar.rangeChanged.connect(self._arrows)

    def set_width(self, width: int) -> None:

        if width == self._width:
            return
        self._width = width
        gap = C.px(C.TILE_GAP)
        tile_w = max(1, (width - 2 * gap) // 3)
        for tile in self.tiles:
            tile.set_tile_width(tile_w)
        count = len(self.tiles)
        self._strip.setFixedSize(count * tile_w + max(0, count - 1) * gap, tile_height(tile_w))
        self._scroll.setFixedHeight(tile_height(tile_w))
        self._page = 3 * (tile_w + gap)
        self._arrows()

    def _arrows(self, *_args) -> None:
        bar = self._scroll.horizontalScrollBar()
        more = bar.maximum() > 0
        self._prev.setVisible(more)
        self._next.setVisible(more)
        self._prev.setEnabled(bar.value() > bar.minimum())
        self._next.setEnabled(bar.value() < bar.maximum())

    def next_page(self) -> None:
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(self._snap(bar.value() + self._page))

    def previous_page(self) -> None:
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(self._snap(bar.value() - self._page))

    def _snap(self, value: int) -> int:

        step = self._page // 3 if self._page else 1
        return max(0, round(value / step) * step)

    def reveal(self, tile) -> None:

        bar = self._scroll.horizontalScrollBar()
        left, right = tile.x(), tile.x() + tile.width()
        if left < bar.value():
            bar.setValue(left)
        elif right > bar.value() + self._scroll.viewport().width():
            bar.setValue(right - self._scroll.viewport().width())


class ExamplesHome(QWidget):


    case_opened = pyqtSignal(object)
    query_changed = pyqtSignal(str)

    group_requested = pyqtSignal(str)

    def __init__(self, cases: list, groups: list, parent=None, looks: dict | None = None):
        super().__init__(parent)
        self._cases = list(cases)
        self._looks = dict(looks or {})
        self._groups = list(groups)
        self._group = ""
        self._carousels: list = []
        self._grid_tiles: list = []
        self._columns = 3

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("libraryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(C.scroll_qss())
        self._body = QWidget(self._scroll)
        self._page = QVBoxLayout(self._body)
        self._page.setSpacing(0)
        self._search = search_pill(self._body, "librarySearch", tr("Search examples"))
        self._header = PageHeader(self._body, tr("Examples"), "", self._search)
        self._page.addWidget(self._header)
        self._page.addSpacing(C.px(C.SPACE_4))
        self._content = QWidget(self._body)
        self._content_col = QVBoxLayout(self._content)
        self._content_col.setContentsMargins(0, 0, 0, 0)


        self._content_col.setSpacing(C.px(C.SPACE_2))
        self._page.addWidget(self._content)
        self._page.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)
        self._scroll.viewport().installEventFilter(self)

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
        for carousel in self._carousels:
            if tile in carousel.tiles:
                carousel.reveal(tile)
                self._scroll.ensureWidgetVisible(carousel, 0, C.px(24))
                return
        self._scroll.ensureWidgetVisible(tile, 0, C.px(40))



    def _label_of(self, key: str) -> str:
        return next((text for k, text in self._groups if k == key), key)

    def repaint_tiles(self) -> None:
        self._clear()
        query = self.query()
        self.query_changed.emit(query)
        if query:
            hits = match_cases(self._cases, query)
            self._header.set_badge(None)
            self._header.set_text(tr("Examples"), self._home_line())
            if hits:
                title = tr("1 result") if len(hits) == 1 else tr("%n results", "", len(hits))
                self._grid(title, hits)
            else:
                empty = QLabel(tr("No examples match"), self._content)
                empty.setObjectName("libEmpty")
                empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._content_col.addWidget(empty)
        elif self._group:
            cases = [c for c in self._cases if c.group == self._group and c.listed]
            count = tr("1 example") if len(cases) == 1 else tr("%n examples", "", len(cases))
            self._header.set_badge(self._looks.get(self._group, ("", "")))
            self._header.set_text(self._label_of(self._group), count)
            self._grid("", cases)
        else:
            self._header.set_badge(None)
            self._header.set_text(tr("Examples"), self._home_line())
            for key, text in self._groups:
                cases = [c for c in self._cases if c.group == key and c.listed]
                if not cases:
                    continue
                carousel = Carousel(text, cases, self.case_opened.emit,
                                    lambda k=key: self.group_requested.emit(k), self._content,
                                    self._looks.get(key, ("", "")))
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

    def _home_line(self) -> str:
        return tr("Real tasks the agent runs from one sentence. Open one to see its prompt.")

    def _grid(self, title: str, cases: list) -> None:
        holder = QWidget(self._content)
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(C.px(12))
        if title:
            col.addWidget(section_title(holder, title))
        host = QWidget(holder)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))
        grid.setVerticalSpacing(C.px(C.SPACE_3))
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
        side = max(C.px(C.SPACE_4), (viewport - width) // 2)
        self._page.setContentsMargins(side, C.px(C.SPACE_5), side, C.px(C.SPACE_5))
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

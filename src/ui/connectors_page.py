# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






































from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import catalog
from ..core.connector_locale import sort_by_locale
from .library import common as C
from .library.cards import has_picture
from .library.parts import EmptyState, PageHeader, label, search_pill
from .library.pictures import watch_scroll
from .library.pressable import Pressable, labels_through


POPULAR_KEY = "__popular__"


_NATIONAL_KEY = "national"



CATEGORY_ACCENTS = {
    "worldwide": "#3E86D6",
    "national": "#7C6CD0",
    "imagery": "#2F8FB5",
    "terrain": "#8D6E63",
    "nature": "#B5654A",
    "people": "#C96A8C",
    "catalogs": "#C98A2E",
}
_DEFAULT_ACCENT = "#3E86D6"

_MARK_PX = 40

_CARD_H = 64

_COLUMNS = 2


def accent_of(category: str) -> str:
    return CATEGORY_ACCENTS.get(str(category or ""), _DEFAULT_ACCENT)


def sources_by_id() -> dict:


    from .shared import get_connectors

    return {str(r.get("id")): r for r in get_connectors() if r.get("id")}


def cases_of(key: str, cases) -> list:


    mine = [c for c in cases or [] if key in (getattr(c, "connectors", ()) or ())]
    return sorted(mine, key=lambda c: (getattr(c, "listed", True), not has_picture(c)))


def shelves(sources) -> list:







    order: list = []
    labels: dict = {}
    for row in sources or []:
        key = str(row.get("category") or "")
        if key and key not in labels:
            order.append(key)
            labels[key] = str(row.get("category_label") or key)
    served = [key for key in catalog.shelf_order() if key in labels]
    if served:
        order = served + [key for key in order if key not in served]
    else:
        order = ([key for key in order if key != _NATIONAL_KEY]
                 + [key for key in order if key == _NATIONAL_KEY])
    out = []
    if any(int(r.get("popular") or 0) for r in sources or []):

        out.append((POPULAR_KEY, QCoreApplication.translate("ConnectorsPage", "Popular")))
    return out + [(key, labels[key]) for key in order]


class SourceCard(Pressable):





    def __init__(self, row: dict, parent=None):
        super().__init__(parent, radius=C.RADIUS_TILE)
        self.key = str(row.get("id") or "")
        self._name_text = str(row.get("name") or self.key)
        self._note_text = str(row.get("tagline") or "").strip() or str(row.get("summary") or "").strip()
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(C.px(_CARD_H))
        line = QHBoxLayout(self)
        line.setContentsMargins(C.px(8), 0, C.px(8), 0)
        line.setSpacing(C.px(12))
        self._mark = QLabel(self)
        from .library.pictures import wear_source_mark

        wear_source_mark(self._mark, row, accent_of(row.get("category")), C.px(_MARK_PX))
        line.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addStretch(1)
        self._name = label(self, self._name_text, C.BODY_PX, C.T.text, C.MEDIUM)
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self._name)
        self._note = label(self, self._note_text, C.SMALL_PX, C.T.text_2)
        self._note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._note.setVisible(bool(self._note_text))
        text.addWidget(self._note)
        text.addStretch(1)
        line.addLayout(text, 1)
        labels_through(self)
        self.setAccessibleName(self._name_text)
        self._laid = -1

    def set_card_width(self, width: int) -> None:
        width = max(1, int(width))
        self.setFixedWidth(width)
        if width == self._laid:
            return
        self._laid = width
        room = max(1, width - C.px(_MARK_PX) - C.px(12) - 2 * C.px(8))
        self._name.setText(self._name.fontMetrics().elidedText(
            self._name_text, Qt.TextElideMode.ElideRight, room))
        self._note.setText(self._note.fontMetrics().elidedText(
            self._note_text, Qt.TextElideMode.ElideRight, room))


class ConnectorsPage(QWidget):


    connector_opened = pyqtSignal(str)

    ask_requested = pyqtSignal(str)

    shelf_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._title = self.tr("Data sources")
        self._sources: list = []
        self._cases: list = []
        self._filter = ""
        self._cards: list = []
        self._keep_scroll = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(C.scroll_qss())
        body = QWidget(self._scroll)
        self._body = body
        self._page = QVBoxLayout(body)
        self._page.setSpacing(0)
        self._search = search_pill(body, "connectorSearch", self.tr("Search data sources"))
        self._search.textChanged.connect(self._on_search)
        self._header = PageHeader(body, self._title, "", self._search)
        self._page.addWidget(self._header)
        self._page.addSpacing(C.px(C.SPACE_3))

        self._list = QWidget(body)
        self._list_col = QVBoxLayout(self._list)
        self._list_col.setContentsMargins(0, 0, 0, 0)
        self._list_col.setSpacing(0)
        self._page.addWidget(self._list)
        self._page.addStretch(1)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, 1)
        self._scroll.viewport().installEventFilter(self)
        watch_scroll(self, self._scroll.verticalScrollBar())



    def set_data(self, sources, cases=(), shelf: str | None = None) -> None:



        self._sources = [dict(r) for r in list(sources or [])[:500]
                         if isinstance(r, dict) and r.get("id")]
        self._cases = list(cases or [])
        self._header.set_text(self._title, self._tally())
        if shelf is not None:
            self._filter = str(shelf)
        keys = [key for key, _ in shelves(self._sources)]
        if self._filter not in keys:
            self._filter = keys[0] if keys else ""
        self._paint()
        if shelf is not None:
            self._scroll.verticalScrollBar().setValue(0)

    def _tally(self) -> str:

        total = len(self._sources)
        if not total:
            return ""
        datasets = sum(int(row.get("datasets") or 0) for row in self._sources)
        return self.tr("%n data sources", "", total) + (
            self.tr(", {n} ready datasets").format(n=datasets) if datasets else "") + (
            self.tr(", free with no account or key"))



    def _on_filter(self, key: str) -> None:
        self._filter = str(key)
        self._paint()
        self._scroll.verticalScrollBar().setValue(0)

    def _on_search(self, _text: str) -> None:
        self._paint()

    def shelf(self) -> str:
        return self._filter

    def _query(self) -> str:
        return " ".join(self._search.text().lower().split())

    @staticmethod
    def _matches(row: dict, query: str) -> bool:
        if not query:
            return True
        hay = " ".join(str(row.get(key) or "") for key in
                       ("name", "tagline", "category_label", "summary", "licence",
                        "coverage")).lower()
        hay += " " + " ".join(str(k) for k in (row.get("kinds") or []))
        return all(word in hay for word in query.split())

    def _paint(self) -> None:

        self._list.setUpdatesEnabled(False)
        try:
            self._clear()
            query = self._query()
            want = self._filter
            if query:
                rows = [r for r in self._sources if self._matches(r, query)]
                if rows:
                    self._grid(rows)
                else:
                    self._no_hit(self._search.text().strip())
            elif want == POPULAR_KEY:
                self._grid(sorted((r for r in self._sources if int(r.get("popular") or 0)),
                                  key=lambda r: int(r["popular"])))
            elif want:
                rows = [r for r in self._sources if str(r.get("category") or "") == want]
                self._grid(self._shelf_order(want, rows))
            else:
                self._empty(self.tr("The list arrives when the panel connects."))
        finally:
            self._list.setUpdatesEnabled(True)
        self._fit()
        self._restore_scroll()

    def _empty(self, text: str) -> None:
        empty = QLabel(text, self._list)
        empty.setObjectName("libEmpty")
        empty.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text_2) + "padding: 40px 16px;")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        self._list_col.addWidget(empty)

    def _no_hit(self, query: str) -> None:
        actions = [(self.tr("Clear search"), self._clear_and_focus)]
        if any(int(r.get("popular") or 0) for r in self._sources):
            actions.append((self.tr("Browse Popular"), self._browse_popular))
        if self.receivers(self.ask_requested) > 0:
            actions.append((self.tr("Ask the agent"), lambda q=query: self.ask_requested.emit(q)))
        self._list_col.addWidget(EmptyState(
            self._list, query, self.tr("No data source matches these words. The agent may "
                                       "still find the data."), actions))

    def _clear_and_focus(self) -> None:
        self.clear_search()
        self._search.setFocus()

    def _browse_popular(self) -> None:
        self._search.blockSignals(True)
        self._search.clear()
        self._search.blockSignals(False)
        self._on_filter(POPULAR_KEY)
        self.shelf_changed.emit(POPULAR_KEY)

    def clear_search(self) -> bool:

        if not self._search.text():
            return False
        self._search.clear()
        return True

    def search_field(self):
        return self._search

    def _grid(self, rows: list) -> None:
        host = QWidget(self._list)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))
        grid.setVerticalSpacing(C.px(4))
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for index, row in enumerate(rows):
            card = self._card(row, host)
            grid.addWidget(card, index // _COLUMNS, index % _COLUMNS)
            self._cards.append(card)
        self._list_col.addWidget(host)

    def _card(self, row: dict, parent: QWidget) -> SourceCard:
        key = str(row.get("id") or "")
        card = SourceCard(row, parent)
        card.clicked.connect(lambda k=key: self.connector_opened.emit(k))
        return card

    def _clear(self) -> None:
        self._cards = []
        while self._list_col.count():
            item = self._list_col.takeAt(0)
            widget = item.widget()
            if widget is not None:


                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _shelf_order(self, key: str, rows: list) -> list:


        if key != _NATIONAL_KEY:
            return rows
        try:
            return sort_by_locale(rows)
        except Exception:  # noqa: BLE001
            return rows

    def remember_scroll(self) -> None:

        try:
            self._keep_scroll = int(self._scroll.verticalScrollBar().value())
        except (AttributeError, RuntimeError):
            self._keep_scroll = -1

    def _restore_scroll(self) -> None:
        value, self._keep_scroll = self._keep_scroll, -1
        if value < 0:
            return
        bar = self._scroll.verticalScrollBar()
        bar.setValue(min(value, bar.maximum()))



    def _fit(self) -> None:
        viewport = self._scroll.viewport().width()
        room = viewport - 2 * C.px(C.SPACE_4)
        width = max(C.px(240), min(room, C.px(C.COLUMN_W)))
        side = max(C.px(C.SPACE_4), (viewport - width) // 2)
        self._page.setContentsMargins(side, C.px(C.SPACE_5), side, C.px(C.SPACE_5))
        gap = C.px(C.TILE_GAP)
        card_w = max(1, (width - (_COLUMNS - 1) * gap) // _COLUMNS)
        for card in self._cards:
            card.set_card_width(card_w)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._fit()
        return super().eventFilter(obj, event)

    def focus_search(self) -> None:
        self._search.setFocus()

    def cards(self) -> list:
        return list(self._cards)

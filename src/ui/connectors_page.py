# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





































from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
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
from .library.parts import PageHeader, label, search_pill
from .library.pictures import PictureView
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

_MARK_PX = 20

_CARD_TEXT_H = 70


def connector_note(row: dict, tr) -> str:





    tagline = str(row.get("tagline") or "").strip()
    if tagline:
        return tagline
    count = int(row.get("datasets") or 0)
    bits = []
    if count:
        bits.append(tr("1 ready source") if count == 1 else tr("%n ready sources", "", count))
    licence = str(row.get("licence") or "").strip()
    if licence:
        bits.append(licence)
    return " · ".join(bits)


def accent_of(category: str) -> str:
    return CATEGORY_ACCENTS.get(str(category or ""), _DEFAULT_ACCENT)


def cases_of(key: str, cases) -> list:


    mine = [c for c in cases or [] if key in (getattr(c, "connectors", ()) or ())]
    return sorted(mine, key=lambda c: (getattr(c, "listed", True), not getattr(c, "image", "")))


class SourceCard(Pressable):





    def __init__(self, row: dict, image: str, examples: int, parent=None):
        super().__init__(parent, radius=C.RADIUS_TILE, hover=lambda: None, press=lambda: None)
        self.key = str(row.get("id") or "")
        self._name_text = str(row.get("name") or self.key)
        self._note_text = str(row.get("tagline") or "").strip() or str(row.get("summary") or "").strip()
        self._count_text = (self.tr("1 example") if examples == 1
                            else self.tr("%n examples", "", examples)) if examples else ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._picture = PictureView(image, owner=self, parent=self, radius=C.RADIUS_TILE,
                                    label="" if image else self._name_text)
        col.addWidget(self._picture)
        col.addSpacing(C.px(C.SPACE_1))
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(C.px(8))
        self._mark = QLabel(self)
        from .library.pictures import wear_source_mark

        wear_source_mark(self._mark, row, accent_of(row.get("category")), C.px(_MARK_PX))
        head.addWidget(self._mark, 0, Qt.AlignmentFlag.AlignVCenter)
        self._name = label(self, self._name_text, C.BODY_PX, C.T.text, C.MEDIUM)
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        head.addWidget(self._name, 1, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(head)
        col.addSpacing(2)
        self._note = label(self, self._note_text, C.SMALL_PX, C.T.text_2)
        self._note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._note.setVisible(bool(self._note_text))
        col.addWidget(self._note)
        self._count = label(self, self._count_text, C.SMALL_PX, C.T.text_2)
        self._count.setVisible(bool(self._count_text))
        col.addWidget(self._count)
        col.addStretch(1)
        labels_through(self)
        self.setAccessibleName(self._name_text)
        self.setToolTip("\n".join(t for t in (self._name_text, self._note_text) if t))
        self._laid = -1

    def _state_changed(self) -> None:
        self._picture.set_shade(1.0 if self._pressed else 0.6 if self._hovered else 0.0)

    def set_card_width(self, width: int) -> None:
        width = max(1, int(width))
        self.setFixedWidth(width)
        picture = round(width * 9 / 16)
        self._picture.setFixedHeight(picture)
        self.setFixedHeight(picture + C.px(_CARD_TEXT_H))
        if width == self._laid:
            return
        self._laid = width
        name_room = width - C.px(_MARK_PX) - C.px(8)
        self._name.setText(self._name.fontMetrics().elidedText(
            self._name_text, Qt.TextElideMode.ElideRight, name_room))
        self._note.setText(self._note.fontMetrics().elidedText(
            self._note_text, Qt.TextElideMode.ElideRight, width))


class _Tab(Pressable):


    def __init__(self, key: str, text: str, parent=None):
        super().__init__(parent, radius=C.px(18),
                         rest=lambda: C.T.selected if self.chosen else None,
                         hover=lambda: C.T.selected if self.chosen else C.T.hover)
        self.key = key
        self.chosen = False
        self.setFixedHeight(C.px(36))
        row = QHBoxLayout(self)
        row.setContentsMargins(C.px(14), 0, C.px(14), 0)
        row.addWidget(label(self, text, C.BODY_PX, C.T.text), 0, Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self.setAccessibleName(text)

    def set_chosen(self, chosen: bool) -> None:
        self.chosen = bool(chosen)
        self.update()


class _EdgeFade(QWidget):




    def __init__(self, parent, right: bool):
        super().__init__(parent)
        self._right = right
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def paintEvent(self, _event):  # noqa: N802
        from qgis.PyQt.QtGui import QLinearGradient, QPainter

        painter = QPainter(self)
        try:
            ground = QColor(C.T.bg)
            clear = QColor(ground)
            clear.setAlpha(0)
            wash = QLinearGradient(0, 0, self.width(), 0)
            wash.setColorAt(0.0, clear if self._right else ground)
            wash.setColorAt(1.0, ground if self._right else clear)
            painter.fillRect(self.rect(), wash)
        finally:
            painter.end()


class _TabScroll(QScrollArea):


    def __init__(self, parent=None):
        super().__init__(parent)
        self._fades = (_EdgeFade(self, False), _EdgeFade(self, True))
        bar = self.horizontalScrollBar()
        bar.valueChanged.connect(self.sync_fades)
        bar.rangeChanged.connect(self.sync_fades)

    def sync_fades(self, *_args) -> None:
        bar = self.horizontalScrollBar()
        width = C.px(48)
        left, right = self._fades
        left.setGeometry(0, 0, width, self.height())
        right.setGeometry(self.width() - width, 0, width, self.height())
        left.setVisible(bar.value() > bar.minimum())
        right.setVisible(bar.value() < bar.maximum())
        left.raise_()
        right.raise_()

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self.sync_fades()

    def wheelEvent(self, event):  # noqa: N802
        bar = self.horizontalScrollBar()
        if bar.maximum() <= 0:
            event.ignore()
            return
        delta = event.angleDelta()
        step = delta.x() if abs(delta.x()) > abs(delta.y()) else delta.y()
        bar.setValue(bar.value() - step // 2)
        event.accept()


class ConnectorsPage(QWidget):


    connector_opened = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sources: list = []
        self._cases: list = []
        self._filter = ""
        self._tabs: dict = {}
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
        self._header = PageHeader(body, self.tr("Data sources"), "", self._search)
        self._page.addWidget(self._header)
        self._page.addSpacing(C.px(C.SPACE_3))

        self._tab_scroll = _TabScroll(body)
        self._tab_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._tab_scroll.setWidgetResizable(False)
        self._tab_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tab_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._tab_scroll.viewport().setStyleSheet("background: transparent;")
        self._tab_scroll.setFixedHeight(C.px(36))
        self._page.addWidget(self._tab_scroll)
        self._page.addSpacing(C.px(C.SPACE_4))

        self._list = QWidget(body)
        self._list_col = QVBoxLayout(self._list)
        self._list_col.setContentsMargins(0, 0, 0, 0)
        self._list_col.setSpacing(0)
        self._page.addWidget(self._list)
        self._page.addStretch(1)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, 1)
        self._scroll.viewport().installEventFilter(self)



    def set_data(self, sources, cases=()) -> None:


        self._sources = [dict(r) for r in list(sources or [])[:500]
                         if isinstance(r, dict) and r.get("id")]
        self._cases = list(cases or [])
        self._header.set_text(self.tr("Data sources"), self._tally())
        self._paint_tabs()
        self._paint()

    def _tally(self) -> str:

        total = len(self._sources)
        if not total:
            return ""
        datasets = sum(int(row.get("datasets") or 0) for row in self._sources)
        return self.tr("%n data sources", "", total) + (
            self.tr(", {n} ready datasets").format(n=datasets) if datasets else "") + (
            self.tr(", free with no account or key"))



    def _shelves(self) -> list:





        order: list = []
        labels: dict = {}
        for row in self._sources:
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
        shelves = []
        if any(int(r.get("popular") or 0) for r in self._sources):
            shelves.append((POPULAR_KEY, self.tr("Popular")))
        shelves += [(key, labels[key]) for key in order]
        return shelves

    def _paint_tabs(self) -> None:
        shelves = self._shelves()
        keys = [key for key, _ in shelves]
        if self._filter not in keys:
            self._filter = keys[0] if keys else ""
        strip = QWidget()
        strip.setStyleSheet("background: transparent;")
        row = QHBoxLayout(strip)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(C.px(4))
        self._tabs = {}
        for key, text in shelves:
            tab = _Tab(key, text, strip)
            tab.set_chosen(key == self._filter)
            tab.clicked.connect(lambda k=key: self._on_filter(k))
            row.addWidget(tab)
            self._tabs[key] = tab
        strip.adjustSize()
        strip.setFixedSize(strip.sizeHint().width(), C.px(36))
        old = self._tab_scroll.takeWidget()
        if old is not None:
            old.deleteLater()
        self._tab_scroll.setWidget(strip)
        self._tab_scroll.setVisible(bool(shelves) and not self._query())
        self._tab_scroll.sync_fades()

    def _on_filter(self, key: str) -> None:
        self._filter = str(key)
        for name, tab in self._tabs.items():
            tab.set_chosen(name == self._filter)
        self._paint()
        self._scroll.verticalScrollBar().setValue(0)

    def _on_search(self, _text: str) -> None:
        self._tab_scroll.setVisible(bool(self._tabs) and not self._query())
        self._paint()



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
                    self._empty(self.tr("No data sources match"))
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

    def _grid(self, rows: list) -> None:
        host = QWidget(self._list)
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))
        grid.setVerticalSpacing(C.px(C.SPACE_3))
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        for index, row in enumerate(rows):
            card = self._card(row, host)
            grid.addWidget(card, index // C.COLUMNS, index % C.COLUMNS)
            self._cards.append(card)
        self._list_col.addWidget(host)

    def _card(self, row: dict, parent: QWidget) -> SourceCard:
        key = str(row.get("id") or "")
        examples = cases_of(key, self._cases)
        image = next((str(c.image) for c in examples if getattr(c, "image", "")), "")
        card = SourceCard(row, image, len(examples), parent)
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
        card_w = max(1, (width - (C.COLUMNS - 1) * gap) // C.COLUMNS)
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

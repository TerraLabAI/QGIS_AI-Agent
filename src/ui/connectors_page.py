# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















































from __future__ import annotations

from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt, QTimer, pyqtSignal
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

from ..core import catalog, source_rows
from ..core.numbers import as_int
from ..core.served_state import get_connectors
from .library import common as C
from .library.cards import has_picture
from .library.common import escape
from .library.dataset_rows import DatasetHit
from .library.parts import Breadcrumb, EmptyState, PageHeader, SectionHead, edge, label, search_pill, section_title
from .library.pictures import watch_scroll, wear_square_mark
from .library.pressable import Pressable, labels_through, mouse_through
from .mention_search import fold

POPULAR_KEY = "__popular__"
NATIONAL_KEY = "national"


_CATEGORY_ORDER = ("worldwide", "national", "imagery", "terrain", "nature", "people", "catalogs")

_BOX_PX = 36


_CARD_H = 64
_ROW_GAP = 8
_COLUMNS = 2
_COLUMN_MIN_W = 260
_SECTION_COUNT = 6
REGIONS = ("europe", "americas", "asia_pacific", "africa_middle_east")
OTHER_KEY = source_rows.OTHER
_SEARCH_WAIT_MS = 300


_COUNTRY_NAMES: dict | None = None


def country_name(code: str) -> str:

    global _COUNTRY_NAMES
    if _COUNTRY_NAMES is None:
        _COUNTRY_NAMES = {}
        try:
            from qgis.PyQt.QtCore import QLocale


            any_language = QLocale.Language.AnyLanguage
            any_script = QLocale.Script.AnyScript
            territory = getattr(QLocale, "Territory", None) or QLocale.Country
            anywhere = getattr(territory, "AnyTerritory", None) or territory.AnyCountry
            to_text = getattr(QLocale, "territoryToString", None) or QLocale.countryToString
            for place in QLocale.matchingLocales(any_language, any_script, anywhere):
                region = str(place.name()).partition("_")[2]
                if len(region) == 2 and region not in _COUNTRY_NAMES:
                    where = place.territory() if hasattr(place, "territory") else place.country()
                    _COUNTRY_NAMES[region] = str(to_text(where) or "")
        except Exception:  # nosec B110
            pass
    return _COUNTRY_NAMES.get(str(code).upper()) or str(code)


def _tr(text: str, n: int = -1) -> str:
    return QCoreApplication.translate("ConnectorsPage", text, "", n)


def other_row() -> dict:

    count = source_rows.other_count()
    return {"id": OTHER_KEY, "name": _tr("More sources"), "glyph": "globe",
            "tagline": _tr("%n datasets from other providers", count),
            "datasets": count, "countries": ["global"]}


def sources_by_id() -> dict:



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

        order = ([key for key in _CATEGORY_ORDER if key in labels]
                 + [key for key in order if key not in _CATEGORY_ORDER])
    out = []
    if any(_popular(r) for r in sources or []):
        out.append((POPULAR_KEY, _tr("Popular")))
    return out + [(key, labels[key]) for key in order]


def _popular(row: dict) -> int:
    return as_int(row.get("popular") or 0, 0)


def _by_relevance(rows: list) -> list:
    country, language = source_rows.user_place()
    return sorted(rows, key=lambda r: source_rows.relevance(r, country, language))


def section_rows(sources, key: str) -> list:

    if key == POPULAR_KEY:
        return sorted((r for r in sources if _popular(r)), key=_popular)
    return _by_relevance([r for r in sources if str(r.get("category") or "") == key])


def home_sections(sources) -> list:

    shown: set = set()
    out = []
    for key, text in shelves(sources):
        rows = [r for r in section_rows(sources, key) if str(r.get("id")) not in shown]
        rows = rows[:_SECTION_COUNT]
        shown.update(str(r.get("id")) for r in rows)
        if rows:
            out.append((key, text, rows))
    return out


def _region_label(key: str, served: str) -> str:
    if served:
        return served
    return {"europe": _tr("Europe"), "americas": _tr("Americas"),
            "asia_pacific": _tr("Asia and Pacific"),
            "africa_middle_east": _tr("Africa and Middle East")}.get(key, key)


def countries_line(row: dict) -> str:

    codes = [str(c) for c in (row.get("countries") or []) if str(c) and str(c).lower() != "global"]
    code = str(row.get("country") or "")
    if not codes and code:
        codes = [code]
    names = sorted({country_name(c) for c in codes}, key=fold)
    if code and country_name(code) in names:
        names.remove(country_name(code))
        names.insert(0, country_name(code))
    return ", ".join(names) or str(row.get("coverage") or "").strip()


def country_groups(rows: list) -> list:






    has_regions = any(str(r.get("region") or "") for r in rows)
    country, _language = source_rows.user_place()
    mine = next((str(r.get("region") or "") for r in rows
                 if country and str(r.get("country") or "").upper() == country), "")
    order = ([mine] if mine else []) + [k for k in REGIONS if k != mine]
    buckets: dict = {}
    labels: dict = {}
    for row in rows:
        region = str(row.get("region") or "") if has_regions else ""
        labels.setdefault(region, _region_label(region, str(row.get("region_label") or "")))
        buckets.setdefault(region, []).append(row)
    keys = [k for k in order if k in buckets] + sorted(k for k in buckets if k not in order)
    out = []
    for region in keys:
        members = sorted(buckets[region], key=lambda r: (fold(countries_line(r)), fold(str(r.get("name") or ""))))
        out.append((labels.get(region, "") if has_regions else "", members))
    return out





_COUNTRY_SPLIT_MIN = 6
_GROUP_ORDER = ("national", "regional", "local")


def _spans_countries(row: dict) -> bool:



    named = [c for c in (row.get("countries") or []) if str(c).lower() != "global"]
    return not str(row.get("country") or "") or len(named) >= 3


def country_blocks(members: list) -> list:




    several = [r for r in members if _spans_countries(r)]
    by_country: dict = {}
    for row in members:
        code = str(row.get("country") or "").upper()
        if code and not _spans_countries(row):
            by_country.setdefault(code, []).append(row)
    by_name = lambda r: fold(str(r.get("name") or ""))  # noqa: E731
    blocks: list = []
    if several:
        blocks.append(("", [("", sorted(several, key=by_name))]))
    for code in sorted(by_country, key=lambda c: fold(country_name(c))):
        rows = by_country[code]
        kinds = {str(r.get("group") or "") for r in rows}
        if len(rows) >= _COUNTRY_SPLIT_MIN and len(kinds) >= 2:
            keys = [k for k in _GROUP_ORDER if k in kinds] + sorted(kinds - set(_GROUP_ORDER))
            groups = []
            for key in keys:
                part = sorted((r for r in rows if str(r.get("group") or "") == key), key=by_name)
                groups.append((str(part[0].get("group_label") or ""), part))
        else:
            groups = [("", sorted(rows, key=by_name))]
        blocks.append((country_name(code), groups))
    return blocks



_LICENCE_MAX = 16


def short_licence(text: str) -> str:


    text = str(text or "").strip().split(",")[0].strip()
    if not text or len(text) > _LICENCE_MAX or text.endswith(("...", "\u2026")):
        return ""
    return text


def mark_box(parent: QWidget, row: dict, box: int = _BOX_PX, radius: int = 8) -> QLabel:




    mark = QLabel(parent)
    wear_square_mark(mark, row, C.accent_of(row.get("category")), C.px(box), C.px(radius))
    mouse_through(mark)
    return mark


class SourceCard(Pressable):






    def __init__(self, row: dict, parent=None, note: str | None = None, words: tuple = ()):
        super().__init__(parent, radius=C.RADIUS_TILE)
        self.key = str(row.get("id") or "")
        self._name_text = str(row.get("name") or self.key)
        default = str(row.get("tagline") or "").strip() or str(row.get("coverage") or "").strip()
        self._note_text = default if note is None else str(note)
        self._words = tuple(w for w in words if w)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(C.px(_CARD_H))
        line = QHBoxLayout(self)
        line.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        line.setSpacing(C.px(12))
        line.addWidget(mark_box(self, row, _BOX_PX), 0, Qt.AlignmentFlag.AlignVCenter)
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(2)
        text.addStretch(1)
        self._name = label(self, self._name_text, C.NAME_PX, C.T.text, C.MEDIUM)
        self._note = label(self, self._note_text, C.BODY_PX, C.T.text_2)
        if self._words:
            self._note.setTextFormat(Qt.TextFormat.RichText)
        for widget, value in ((self._name, self._name_text), (self._note, self._note_text)):
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            widget.setVisible(bool(value))
            text.addWidget(widget)
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
        room = max(1, width - C.px(_BOX_PX) - C.px(12) - 2 * C.px(C.EDGE))
        for widget, value in ((self._name, self._name_text), (self._note, self._note_text)):

            widget.ensurePolished()
            shown = widget.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, room)
            widget.setToolTip(value if shown != value else "")
            if widget is self._note and self._words:
                shown = _strong(shown, self._words)
            widget.setText(shown)


def _strong(text: str, words: tuple) -> str:

    import re

    pattern = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)),
                         re.IGNORECASE)
    out, last = [], 0
    for hit in pattern.finditer(text):
        out.append(escape(text[last:hit.start()]))
        out.append(f'<span style="color: {C.T.text};{C.weight_css(C.MEDIUM)}">'
                   f"{escape(hit.group(0))}</span>")
        last = hit.end()
    out.append(escape(text[last:]))
    return "".join(out)


class ConnectorsPage(QWidget):


    connector_opened = pyqtSignal(str)
    section_opened = pyqtSignal(str)
    crumb_requested = pyqtSignal(int)
    ask_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sources: list = []
        self._cases: list = []
        self._section = ""
        self._cards: list = []
        self._grids: list = []
        self._cols = _COLUMNS
        self._painted = False
        self._hits_query = ""
        self._hits: list = []
        self._hits_reply = None
        self._hits_timer = QTimer(self)
        self._hits_timer.setSingleShot(True)
        self._hits_timer.setInterval(_SEARCH_WAIT_MS)
        self._hits_timer.timeout.connect(self._ask_hits)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(C.scroll_qss())
        C.keep_gutter(self._scroll)
        body = QWidget(self._scroll)
        self._body = body
        self._page = QVBoxLayout(body)
        self._page.setSpacing(0)
        self._crumbs = Breadcrumb(body)
        self._crumbs.crumb.connect(self.crumb_requested.emit)
        self._crumbs.hide()
        self._page.addWidget(self._crumbs)
        self._search = search_pill(body, "connectorSearch", self.tr("Search sources, datasets or a country"))
        self._search.textChanged.connect(self._on_search)
        self._header = PageHeader(body, self.tr("Data sources"), "", self._search)
        self._page.addWidget(self._header)
        self._page.addSpacing(C.px(C.SPACE_5))

        self._list = QWidget(body)
        self._list_col = QVBoxLayout(self._list)
        self._list_col.setContentsMargins(0, 0, 0, 0)
        self._list_col.setSpacing(C.px(C.SPACE_5))
        self._page.addWidget(self._list)
        self._page.addStretch(1)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, 1)
        self._scroll.viewport().installEventFilter(self)
        watch_scroll(self, self._scroll.verticalScrollBar())

    def set_data(self, sources, cases=(), section: str = "") -> None:


        rows = [dict(r) for r in list(sources or [])[:500]
                if isinstance(r, dict) and r.get("id")]
        cases = list(cases or [])
        section = str(section or "")
        changed = (not self._painted or rows != self._sources or cases != self._cases
                   or section != self._section)
        self._sources, self._cases, self._section = rows, cases, section
        if changed:
            self._paint()

    def section(self) -> str:
        return self._section

    def section_label(self) -> str:
        return next((text for key, text in shelves(self._sources) if key == self._section),
                    self._section)

    def set_trail(self, names: list) -> None:
        self._crumbs.set_trail(names)

    def _subtitle(self) -> str:
        if not self._section:
            return self._figures_line()
        count = len(section_rows(self._sources, self._section))
        return self.tr("%n sources", "", count)

    def _figures_line(self) -> str:



        rows = self._sources
        datasets = sum(int(r.get("datasets") or 0) for r in rows)
        countries = {str(c).upper() for r in rows for c in (r.get("countries") or [])
                     if str(c) and str(c).lower() != "global"}
        locale = C.ui_locale()
        return self.tr("{sources} sources the AI agent finds and loads by itself: "
                       "{datasets} datasets in {countries} countries.").format(
            sources=locale.toString(len(rows)), datasets=locale.toString(int(datasets)),
            countries=locale.toString(len(countries)))

    def _on_search(self, _text: str) -> None:
        self._paint()
        self._hits_timer.start()

    def _ask_hits(self) -> None:

        query = self._query()
        source_rows.abort(self._hits_reply)
        self._hits_reply = None
        if len(query) < 2 or not source_rows.available():
            if self._hits:
                self._hits, self._hits_query = [], ""
                self._paint()
            return
        country, _language = source_rows.user_place()
        self._hits_reply = source_rows.fetch(
            lambda rows, error, q=query: self._on_hits(q, rows, error),
            query=query, country=country)

    def _on_hits(self, query: str, rows: list, error: str) -> None:
        self._hits_reply = None
        if query != self._query():
            return
        self._hits_query = query
        self._hits = [] if error else list(rows or [])
        self._paint()

    def _query(self) -> str:
        return " ".join(self._search.text().lower().split())

    def _search_rows(self, query: str, hits: list) -> list:




        words = query.split()
        best_hit: dict = {}
        for hit in hits:
            family = str(hit.get("family") or "")
            if family and family not in best_hit:
                best_hit[family] = str(hit.get("title") or "")

        def has(text) -> bool:
            text = str(text or "").lower()
            return all(w in text for w in words)

        ranked = []
        for row in _by_relevance(self._sources):
            key = str(row.get("id") or "")
            if has(row.get("name")):
                ranked.append((0, row, None))
            elif has(row.get("tagline")):
                ranked.append((1, row, None))
            elif key in best_hit:
                ranked.append((2, row, best_hit[key]))
            elif self._matches(row, query):
                ranked.append((3, row, None))
        ranked.sort(key=lambda item: item[0])
        return [(row, note) for _rank, row, note in ranked]

    @staticmethod
    def _matches(row: dict, query: str) -> bool:
        if not query:
            return True
        hay = " ".join(str(row.get(key) or "") for key in
                       ("name", "tagline", "category_label", "summary", "licence",
                        "coverage", "region_label")).lower()
        hay += " " + " ".join(str(k) for k in (row.get("kinds") or []))
        hay += " " + " ".join(country_name(c).lower() for c in (row.get("countries") or []))
        return all(word in hay for word in query.split())

    def clear_search(self) -> bool:

        if not self._search.text():
            return False
        self._search.clear()
        return True

    def search_field(self):
        return self._search

    def focus_search(self) -> None:
        self._search.setFocus()

    def cards(self) -> list:
        return list(self._cards)

    def _paint(self) -> None:

        self._list.setUpdatesEnabled(False)
        try:
            self._clear()
            self._painted = True
            self._crumbs.setVisible(bool(self._section))


            self._header.set_hero(not self._section)
            self._header.set_text(self.section_label() if self._section else self.tr("Data sources"),
                                  self._subtitle() if self._sources else "")
            query = self._query()
            hits = self._hits if query and query == self._hits_query else []
            if query:
                found = self._search_rows(query, hits)
                if found:
                    holder = self._holder()
                    holder.layout().addWidget(section_title(holder, self.tr("Sources")))
                    host = QWidget(holder)
                    grid = self._grid_layout(host)
                    for index, (row, note) in enumerate(found):
                        self._add_card(grid, host, row, index, note, tuple(query.split()))
                    self._grids.append((grid, [grid.itemAt(i).widget()
                                               for i in range(grid.count())]))
                    holder.layout().addWidget(host)
                elif not hits:
                    self._no_hit(self._search.text().strip())
                if hits:
                    self._hit_group(hits)
            elif not self._sources:
                self._empty(self.tr("The list arrives when the panel connects."))
            elif self._section == NATIONAL_KEY:
                self._paint_countries(section_rows(self._sources, NATIONAL_KEY))
            elif self._section:
                self._list_col.addWidget(self._grid(
                    section_rows(self._sources, self._section), self._list))
            else:
                for key, text, rows in home_sections(self._sources):
                    self._home_section(key, text, rows)
                if source_rows.available() and source_rows.other_count() > 0:
                    holder = self._holder()
                    holder.layout().addWidget(section_title(holder, self.tr("More sources")))
                    holder.layout().addWidget(self._grid([other_row()], holder))
        finally:
            self._list.setUpdatesEnabled(True)
        self._fit()
        self._scroll.verticalScrollBar().setValue(0)

    def _holder(self) -> QWidget:
        holder = QWidget(self._list)
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(C.px(C.SPACE_2))
        self._list_col.addWidget(holder)
        return holder

    def _home_section(self, key: str, text: str, rows: list) -> None:

        holder = self._holder()
        head = SectionHead(text, holder)
        head.setObjectName("sourcesSection")
        head.clicked.connect(lambda _c=False, k=key: self.section_opened.emit(k))
        holder.layout().addWidget(head)
        holder.layout().addWidget(self._grid(rows, holder))

    def _paint_countries(self, rows: list) -> None:



        for region, members in country_groups(rows):
            holder = self._holder()
            if region:
                holder.layout().addWidget(section_title(holder, region))
            for country, groups in country_blocks(members):
                if country:
                    holder.layout().addWidget(edge(label(holder, country, C.NAME_PX, C.T.text, C.MEDIUM)))
                for text, group_rows in groups:
                    if text:
                        holder.layout().addWidget(edge(label(holder, text, C.BODY_PX, C.T.text_2, C.MEDIUM)))
                    host = QWidget(holder)
                    grid = self._grid_layout(host)
                    for index, row in enumerate(group_rows):
                        self._add_card(grid, host, row, index, None if country else countries_line(row))
                    self._grids.append((grid, [grid.itemAt(i).widget() for i in range(grid.count())]))
                    holder.layout().addWidget(host)

    def _add_card(self, grid, host, row: dict, index: int, note=None, words: tuple = ()) -> None:
        key = str(row.get("id") or "")
        card = SourceCard(row, host, note, words)
        card.clicked.connect(lambda _c=False, k=key: self.connector_opened.emit(k))
        grid.addWidget(card, index // self._cols, index % self._cols)
        self._cards.append(card)

    def _empty(self, text: str) -> None:
        empty = QLabel(text, self._list)
        empty.setObjectName("libEmpty")
        empty.setStyleSheet(C.text_qss(C.BODY_PX, C.T.text_2) + "padding: 40px 16px;")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setWordWrap(True)
        self._list_col.addWidget(empty)

    def _no_hit(self, query: str) -> None:
        actions = [(self.tr("Clear search"), self._clear_and_focus)]
        if self.receivers(self.ask_requested) > 0:
            actions.append((self.tr("Try in chat"), lambda q=query: self.ask_requested.emit(q)))
        self._list_col.addWidget(EmptyState(
            self._list, query, self.tr("No data source matches these words. The AI agent may "
                                       "still find the data."), actions))

    def _clear_and_focus(self) -> None:
        self.clear_search()
        self._search.setFocus()

    def _hit_group(self, hits: list) -> None:


        by_id = {str(r.get("id")): r for r in self._sources}
        by_id[OTHER_KEY] = other_row()
        holder = self._holder()
        holder.layout().addWidget(section_title(holder, self.tr("Datasets")))
        host = QWidget(holder)
        grid = self._grid_layout(host)
        country, language = source_rows.user_place()
        hits = sorted(hits, key=lambda r: source_rows.relevance(r, country, language)[:2])
        for index, row in enumerate(hits):
            family = by_id.get(str(row.get("family") or "")) or {}
            card = DatasetHit(row, family, C.accent_of(family.get("category")), host)
            card.clicked.connect(lambda _c=False, k=card.family: self.connector_opened.emit(k))
            grid.addWidget(card, index // self._cols, index % self._cols)
            self._cards.append(card)
        self._grids.append((grid, [grid.itemAt(i).widget() for i in range(grid.count())]))
        holder.layout().addWidget(host)

    @staticmethod
    def _grid_layout(host: QWidget) -> QGridLayout:
        grid = QGridLayout(host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(C.px(C.TILE_GAP))
        grid.setVerticalSpacing(C.px(_ROW_GAP))
        grid.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        return grid

    def _grid(self, rows: list, parent: QWidget) -> QWidget:
        host = QWidget(parent)
        grid = self._grid_layout(host)
        for index, row in enumerate(rows):
            self._add_card(grid, host, row, index)
        self._grids.append((grid, [grid.itemAt(i).widget() for i in range(grid.count())]))
        return host

    def _clear(self) -> None:
        self._cards = []
        self._grids = []
        while self._list_col.count():
            item = self._list_col.takeAt(0)
            widget = item.widget()
            if widget is not None:


                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _fit(self) -> None:
        viewport = self._scroll.viewport().width()
        if not self._scroll.isVisible() or viewport < C.px(200):
            viewport = max(viewport, self.width() - C.px(10))
        room = viewport - 2 * C.px(C.SPACE_4)
        width = max(C.px(240), min(room, C.px(C.COLUMN_W)))
        side = max(C.px(C.SPACE_4), (viewport - width) // 2)

        top = 0 if self._section else C.px(C.TOP_BAR_H)

        edge_px = C.px(C.EDGE)
        self._page.setContentsMargins(side - edge_px, top, side - edge_px, C.px(C.SPACE_5))
        gap = C.px(C.TILE_GAP)
        inner = width + 2 * edge_px
        cols = _COLUMNS if (inner - gap) // _COLUMNS >= C.px(_COLUMN_MIN_W) else 1
        if cols != self._cols:
            self._cols = cols
            for grid, widgets in self._grids:
                for widget in widgets:
                    grid.removeWidget(widget)
                for index, widget in enumerate(widgets):
                    grid.addWidget(widget, index // cols, index % cols, Qt.AlignmentFlag.AlignTop)
        card_w = max(1, (inner - (cols - 1) * gap) // cols)
        for card in self._cards:
            card.set_card_width(card_w)

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._fit()
        return super().eventFilter(obj, event)

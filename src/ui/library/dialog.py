# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





































from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from ..connector_page import ConnectorPage
from ..connectors_page import POPULAR_KEY, ConnectorsPage, accent_of, cases_of, shelves, sources_by_id
from ..font_scale import apply_font_scale_to_tree
from ..shared import size_within_screen, tr, watch_connectors
from ..use_cases import use_case_group_looks, use_case_groups, use_cases
from . import common as C
from . import keys
from .detail import ExampleDetail
from .home import ExamplesHome
from .rail import LibraryRail

_GRID_PAGE, _DETAIL_PAGE, _SOURCES_PAGE, _SOURCE_PAGE = 0, 1, 2, 3

HOME_KEY = "__examples__"
SOURCES_KEY = "__sources__"


_SHELF_PREFIX = "__shelf__:"

_SHELF_GLYPHS = {
    "worldwide": "globe",
    "imagery": "satellite",
    "terrain": "terrain",
    "nature": "points",
    "people": "person",
    "national": "pin",
    "catalogs": "book",
}


class ExamplesDialog(QDialog):



    prompt_chosen = pyqtSignal(str, object)
    example_chosen = pyqtSignal(str)

    def __init__(self, parent=None, opened_from: str = "composer"):
        super().__init__(parent)
        self.setObjectName("AIAgentExamplesDialog")
        self.setWindowTitle(tr("Examples"))
        try:
            from ...core import telemetry
            from ...core import telemetry_events as ev

            telemetry.track(ev.LIBRARY_OPENED, {"source": str(opened_from or "composer")})
        except Exception:  # nosec B110
            pass
        self.setModal(True)
        self.setStyleSheet(C.page_qss("AIAgentExamplesDialog"))
        size_within_screen(self, C.px(C.DIALOG_W), C.px(C.DIALOG_H), C.px(760), C.px(480))

        self._cases = list(use_cases())
        self._group_rows = list(use_case_groups())
        self._groups = [key for key, _ in self._group_rows]
        self._looks = use_case_group_looks()
        self._focus = -1
        self._focus_tiles: list = []

        self._history: list = []
        self._state = (_GRID_PAGE, None)
        self._connectors = sources_by_id()

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._rail = LibraryRail([(HOME_KEY, "image", tr("Examples")),
                                  (SOURCES_KEY, "globe", tr("Data sources"))],
                                 self._group_rows, self, self._looks)
        self._rail.selected.connect(self._on_rail)
        self._set_shelves()
        self._unwatch = watch_connectors(self._on_sources_changed)
        root.addWidget(self._rail)

        right = QWidget(self)
        self._right = right
        col = QVBoxLayout(right)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._pages = QStackedWidget(right)
        self._home = ExamplesHome(self._cases, self._group_rows, right, self._looks)
        self._home.case_opened.connect(self.open_case)
        self._home.query_changed.connect(self._on_query)
        self._home.group_requested.connect(self._on_rail)
        self._home.ask_requested.connect(self._on_ask)
        self._pages.addWidget(self._home)
        self._detail = ExampleDetail(right)
        self._detail.crumb_requested.connect(self._on_detail_crumb)
        self._detail.prompt_chosen.connect(self._on_prompt)
        self._detail.connector_requested.connect(self.open_connector)
        self._pages.addWidget(self._detail)


        self._directory: ConnectorsPage | None = None
        self._source = ConnectorPage(right)
        self._source.back_requested.connect(self._to_directory)
        self._source.prompt_chosen.connect(self._on_source_prompt)
        self._source.example_opened.connect(self.open_case)
        self._pages.addWidget(QWidget(right))
        self._pages.addWidget(self._source)
        col.addWidget(self._pages, 1)
        root.addWidget(right, 1)

        self._rail.set_current(HOME_KEY)
        self._painted = False
        apply_font_scale_to_tree(self)
        self._home.search_field().setFocus()
        self._home.search_field().installEventFilter(self)
        from ..shared import QShortcut

        find = QShortcut(QKeySequence(QKeySequence.StandardKey.Find), self)
        find.activated.connect(self._focus_search)

    def eventFilter(self, watched, event):  # noqa: N802


        if event.type() == QEvent.Type.KeyPress and event.key() in (
                Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Escape):
            self.keyPressEvent(event)
            return True
        return super().eventFilter(watched, event)

    def _focus_search(self) -> None:
        field = self._search_field()
        if field is not None:
            self._drop_focus()
            field.setFocus()
            field.selectAll()

    def showEvent(self, event):  # noqa: N802


        super().showEvent(event)
        if not self._painted:
            self._painted = True
            self._home.repaint_tiles()

    def _on_query(self, query: str) -> None:
        self._drop_focus()
        if self._pages.currentIndex() == _GRID_PAGE:
            self._rail.set_current("" if query else (self._home.group() or HOME_KEY))



    def _go(self, page: int, payload=None, push: bool = True) -> None:

        if push and self._state != (page, payload):
            self._history.append(self._state)
        self._state = (page, payload)

        self._drop_focus()
        self._pages.setCurrentIndex(page)
        if page == _GRID_PAGE:
            self._home.search_field().setFocus()
        self._mark_rail()

    def _mark_rail(self) -> None:


        page, payload = self._state
        if page == _GRID_PAGE:
            self._rail.set_current("" if self._home.query() else (self._home.group() or HOME_KEY))
        elif page == _SOURCES_PAGE:
            self._rail.set_current(self._shelf_key())
        elif page == _SOURCE_PAGE:
            self._rail.set_current(self._category_key(str(payload or "")))
        elif getattr(payload, "listed", True):
            self._rail.set_current(str(getattr(payload, "group", "") or HOME_KEY))
        else:
            self._rail.set_current(self._category_key(self._source_of(payload)))

    def _category_key(self, source: str) -> str:
        category = str((self._connectors.get(source) or {}).get("category") or "")
        key = _SHELF_PREFIX + category
        rail_keys = self._rail.keys()
        return key if category and key in rail_keys else SOURCES_KEY

    def _on_sources_changed(self) -> None:


        self._connectors = sources_by_id()
        self._set_shelves()
        if self._directory is not None:
            self._paint_directory()
        self._mark_rail()

    def done(self, result: int) -> None:  # noqa: N802
        self._unwatch()
        super().done(result)

    def _back(self) -> None:

        if not self._history:
            self._go(_GRID_PAGE, push=False)
            return
        page, payload = self._history.pop()
        if page == _DETAIL_PAGE and payload is not None:
            self._show_case(payload)
        elif page == _SOURCE_PAGE and payload is not None:
            self._show_source(payload)
        elif page == _SOURCES_PAGE:
            self._ensure_directory()
            self._directory.remember_scroll()
            self._paint_directory()
        self._go(page, payload, push=False)

    def _set_shelves(self) -> None:

        rows = list(self._connectors.values())
        groups, looks = [], {}
        for key, text in shelves(rows):
            rail_key = _SHELF_PREFIX + key
            groups.append((rail_key, text))
            glyph = "sparkles" if key == POPULAR_KEY else _SHELF_GLYPHS.get(key, "layers")
            looks[rail_key] = (glyph, accent_of(key))
        self._rail.set_groups(SOURCES_KEY, groups, looks)

    def _on_rail(self, key: str) -> None:

        self._history = []
        if key == SOURCES_KEY or key.startswith(_SHELF_PREFIX):

            self._to_directory(key[len(_SHELF_PREFIX):] if key != SOURCES_KEY else "")
            self._directory.focus_search()
            return
        self._home.set_group("" if key == HOME_KEY else key)
        self._drop_focus()
        self._go(_GRID_PAGE, push=False)

    def _to_directory(self, shelf: str | None = None) -> None:

        self._ensure_directory()
        self._paint_directory(shelf)
        self._history = []
        self._go(_SOURCES_PAGE, push=False)

    def _on_detail_crumb(self, index: int) -> None:


        case = self._detail.case
        if case is not None and not case.listed:
            source = self._source_of(case)
            if index == 1 and source:
                self.open_connector(source)
            else:
                self._to_directory()
            return
        self._on_rail(str(getattr(case, "group", "")) if index == 1 and case is not None
                      else HOME_KEY)

    def _source_of(self, case) -> str:
        return next((str(k) for k in (case.connectors or ()) if str(k) in self._connectors), "")

    def _group_label(self, key: str) -> str:
        return next((text for k, text in self._group_rows if k == key), key)

    def open_sources(self) -> None:


        self._on_rail(SOURCES_KEY)

    def open_case(self, case) -> None:
        self._show_case(case)
        self._go(_DETAIL_PAGE, case)

    def _show_case(self, case) -> None:
        if not case.listed:
            source = self._connectors.get(self._source_of(case)) or {}
            self._detail.set_case(case, str(source.get("name") or ""), root=tr("Data sources"))
            return
        self._detail.set_case(case, self._group_label(str(getattr(case, "group", ""))))

    def open_connector(self, key: str) -> None:





        if str(key) not in self._connectors:
            return
        self._show_source(str(key))
        self._go(_SOURCE_PAGE, str(key))

    def _show_source(self, key: str) -> None:
        row = self._connectors.get(key)
        if row is not None:
            self._source.set_connector(row, cases_of(key, self._cases))



    def _ensure_directory(self) -> None:
        if self._directory is not None:
            return
        page = ConnectorsPage(self._right)
        page.connector_opened.connect(self.open_connector)
        page.ask_requested.connect(self._on_ask)
        page.shelf_changed.connect(lambda _key: self._mark_rail())
        page.search_field().installEventFilter(self)
        old = self._pages.widget(_SOURCES_PAGE)
        self._pages.insertWidget(_SOURCES_PAGE, page)
        self._pages.removeWidget(old)
        old.deleteLater()
        apply_font_scale_to_tree(page)
        self._directory = page

    def _paint_directory(self, shelf: str | None = None) -> None:
        if self._directory is not None:
            self._directory.set_data(list(self._connectors.values()), self._cases, shelf)

    def _shelf_key(self) -> str:

        shelf = self._directory.shelf() if self._directory is not None else ""
        return _SHELF_PREFIX + shelf if shelf else SOURCES_KEY



    def _on_prompt(self, text: str) -> None:






        case = self._detail.case
        self.accept()
        if case is not None:
            self.example_chosen.emit(case.slug)
        self.prompt_chosen.emit(str(text), {})

    def _on_ask(self, query: str) -> None:


        self.accept()
        self.prompt_chosen.emit(str(query or ""), {})

    def _on_source_prompt(self, text: str, chip) -> None:

        self.accept()
        self.prompt_chosen.emit(str(text or ""), dict(chip) if isinstance(chip, dict) else {})



    def _tiles(self) -> list:

        page = self._pages.currentIndex()
        if page == _GRID_PAGE:
            return self._home.tiles()
        if page == _SOURCES_PAGE and self._directory is not None:
            return self._directory.cards()
        if page == _SOURCE_PAGE:
            return self._source.tiles()
        return []

    def _search_field(self):
        page = self._pages.currentIndex()
        if page == _GRID_PAGE:
            return self._home.search_field()
        if page == _SOURCES_PAGE and self._directory is not None:
            return self._directory.search_field()
        return None

    def _drop_focus(self) -> None:
        for tile in self._focus_tiles:
            try:
                tile.set_focused(False)
            except RuntimeError:
                pass
        self._focus, self._focus_tiles = -1, []

    def _arrow(self, key) -> bool:

        tiles = self._tiles()
        if not tiles:
            return False
        if tiles != self._focus_tiles:
            self._drop_focus()
            self._focus_tiles = list(tiles)
        if self._focus < 0 and key != keys.DOWN and key != keys.RIGHT:
            return key == keys.UP
        index = keys.step(tiles, self._focus, key)
        if index < 0:

            self._drop_focus()
            field = self._search_field()
            if field is not None:
                field.setFocus()
            return True
        if 0 <= self._focus < len(tiles):
            tiles[self._focus].set_focused(False)
        self._focus = index
        tiles[index].set_focused(True)
        keys.reveal(tiles[index])


        self.setFocus(Qt.FocusReason.OtherFocusReason)
        return True

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        page = self._pages.currentIndex()
        tiles = self._tiles()
        ringed = 0 <= self._focus < len(tiles) and tiles == self._focus_tiles
        if key in keys.ARROWS and page != _DETAIL_PAGE:
            if self._arrow(key):
                return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and ringed:
            tiles[self._focus].clicked.emit()
            return
        text = event.text()
        field = self._search_field()
        if ringed and field is not None and text and text.isprintable() and not text.isspace():

            self._drop_focus()
            field.setFocus()
            field.insert(text)
            return
        if page != _GRID_PAGE:
            if key == Qt.Key.Key_Escape:
                if page == _SOURCES_PAGE and self._directory is not None and \
                        self._directory.clear_search():
                    return
                if page == _SOURCES_PAGE and not self._history:
                    self._on_rail(HOME_KEY)
                else:
                    self._back()
                return
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Escape:
            if self._home.clear_search():
                return
            self.reject()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if tiles and self._home.query():
                self.open_case(tiles[0].case)
            return
        super().keyPressEvent(event)

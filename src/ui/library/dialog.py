# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import QDialog, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from ..connector_page import ConnectorPage
from ..connectors_page import ConnectorsPage, cases_of
from ..font_scale import apply_font_scale_to_tree
from ..shared import get_connectors, size_within_screen, tr
from ..use_cases import use_case_groups, use_cases
from . import common as C
from .detail import ExampleDetail
from .home import ExamplesHome
from .rail import LibraryRail

_GRID_PAGE, _DETAIL_PAGE, _SOURCES_PAGE, _SOURCE_PAGE = 0, 1, 2, 3

HOME_KEY = "__examples__"
SOURCES_KEY = "__sources__"


class ExamplesDialog(QDialog):



    prompt_chosen = pyqtSignal(str, object)
    example_chosen = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AIAgentExamplesDialog")
        self.setWindowTitle(tr("Examples"))
        try:
            from ...core import telemetry
            from ...core import telemetry_events as ev

            telemetry.track(ev.LIBRARY_OPENED, {"source": "composer"})
        except Exception:  # nosec B110
            pass
        self.setModal(True)
        self.setStyleSheet(C.page_qss("AIAgentExamplesDialog"))
        size_within_screen(self, C.px(C.DIALOG_W), C.px(C.DIALOG_H), C.px(760), C.px(480))

        self._cases = list(use_cases())
        self._group_rows = list(use_case_groups())
        self._groups = [key for key, _ in self._group_rows]
        self._focus = -1

        self._history: list = []
        self._state = (_GRID_PAGE, None)
        self._connectors = {str(r.get("id")): r for r in get_connectors() if r.get("id")}

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._rail = LibraryRail([(HOME_KEY, "image", tr("Examples")),
                                  (SOURCES_KEY, "globe", tr("Data sources"))],
                                 tr("Categories"), self._group_rows, self)
        self._rail.selected.connect(self._on_rail)
        root.addWidget(self._rail)

        right = QWidget(self)
        self._right = right
        col = QVBoxLayout(right)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._pages = QStackedWidget(right)
        self._home = ExamplesHome(self._cases, self._group_rows, right)
        self._home.case_opened.connect(self.open_case)
        self._home.query_changed.connect(self._on_query)
        self._home.group_requested.connect(self._on_rail)
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

    def showEvent(self, event):  # noqa: N802


        super().showEvent(event)
        if not self._painted:
            self._painted = True
            self._home.repaint_tiles()

    def _on_query(self, query: str) -> None:
        self._focus = -1
        if self._pages.currentIndex() == _GRID_PAGE:
            self._rail.set_current("" if query else (self._home.group() or HOME_KEY))



    def _go(self, page: int, payload=None, push: bool = True) -> None:

        if push and self._state != (page, payload):
            self._history.append(self._state)
        self._state = (page, payload)

        self._pages.setCurrentIndex(page)
        if page == _GRID_PAGE:
            self._home.search_field().setFocus()
            self._rail.set_current("" if self._home.query() else (self._home.group() or HOME_KEY))
        elif page in (_SOURCES_PAGE, _SOURCE_PAGE):
            self._rail.set_current(SOURCES_KEY)
        else:
            case = payload
            listed = getattr(case, "listed", True)
            self._rail.set_current(str(getattr(case, "group", "") or HOME_KEY) if listed
                                   else SOURCES_KEY)

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

    def _on_rail(self, key: str) -> None:

        self._history = []
        if key == SOURCES_KEY:
            self._to_directory()
            self._directory.focus_search()
            return
        self._home.set_group("" if key == HOME_KEY else key)
        self._focus = -1
        self._go(_GRID_PAGE, push=False)

    def _to_directory(self) -> None:
        self._ensure_directory()
        self._paint_directory()
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
        old = self._pages.widget(_SOURCES_PAGE)
        self._pages.insertWidget(_SOURCES_PAGE, page)
        self._pages.removeWidget(old)
        old.deleteLater()
        apply_font_scale_to_tree(page)
        self._directory = page

    def _paint_directory(self) -> None:
        if self._directory is not None:
            self._directory.set_data(list(self._connectors.values()), self._cases)



    def _on_prompt(self, text: str) -> None:






        case = self._detail.case
        self.accept()
        if case is not None:
            self.example_chosen.emit(case.slug)
        self.prompt_chosen.emit(str(text), {})

    def _on_source_prompt(self, text: str, chip) -> None:

        self.accept()
        self.prompt_chosen.emit(str(text or ""), dict(chip) if isinstance(chip, dict) else {})



    def _move_focus(self, delta: int) -> None:


        tiles = self._home.tiles()
        if not tiles:
            return
        if self._focus < 0 or self._focus >= len(tiles):
            index = 0 if delta > 0 else len(tiles) - 1
        else:
            index = max(0, min(self._focus + delta, len(tiles) - 1))
            tiles[self._focus].set_focused(False)
        self._focus = index
        tile = tiles[index]
        tile.set_focused(True)
        self._home.reveal(tile)

    def keyPressEvent(self, event):  # noqa: N802
        key = event.key()
        page = self._pages.currentIndex()
        if page != _GRID_PAGE:
            if key == Qt.Key.Key_Escape:
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
        tiles = self._home.tiles()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if 0 <= self._focus < len(tiles):
                self.open_case(tiles[self._focus].case)
            elif tiles and self._home.query():
                self.open_case(tiles[0].case)
            return
        steps = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1,
                 Qt.Key.Key_PageDown: 6, Qt.Key.Key_PageUp: -6}
        if key in steps:
            self._move_focus(steps[key])
            return
        super().keyPressEvent(event)

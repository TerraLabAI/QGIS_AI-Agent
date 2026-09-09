# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Examples library: a window, not a popover."""


















from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..font_scale import apply_font_scale_to_tree, scale_px_length
from ..settings_pages import SCROLL_QSS
from ..shared import size_within_screen, tr
from ..use_cases import match_cases, use_case_groups, use_cases
from .cards import ExampleCard
from .common import (
    CARD_GAP,
    CARD_H,
    CARD_W,
    DIALOG_H,
    DIALOG_QSS,
    DIALOG_W,
    GRID_MARGIN,
    glyph_of,
)
from .detail import ExampleDetail
from .rail import LibraryRail

_GRID_PAGE, _DETAIL_PAGE = 0, 1



_SEARCH_DEBOUNCE_MS = 120


class ExamplesDialog(QDialog):
    """The library window: rail, search, card grid, and one page per card."""

    prompt_chosen = pyqtSignal(str)
    example_chosen = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("AIAgentExamplesDialog")
        self.setWindowTitle(tr("Examples"))
        try:
            from ...core import telemetry
            from ...core import telemetry_events as ev

            telemetry.track(ev.LIBRARY_OPENED, {"source": "composer"})
        except Exception:  # nosec B110 - a dialog opens with or without telemetry
            pass
        self.setModal(True)
        self.setStyleSheet(DIALOG_QSS)
        size_within_screen(self, scale_px_length(DIALOG_W), scale_px_length(DIALOG_H),
                           scale_px_length(560), scale_px_length(400))

        self._cases = list(use_cases())
        self._cards: list = []
        self._focus = -1
        self._groups = [key for key, _ in use_case_groups()]
        self._group = self._groups[0] if self._groups else ""
        self._columns = 0

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        rows = [(key, glyph_of(key), label) for key, label in use_case_groups()]
        self._rail = LibraryRail(tr("Examples"), rows, self)
        self._rail.selected.connect(self._on_rail)
        root.addWidget(self._rail)

        right = QWidget(self)
        col = QVBoxLayout(right)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        self._searchbar = self._build_search(right)
        col.addWidget(self._searchbar)
        self._pages = QStackedWidget(right)
        self._pages.addWidget(self._build_grid(right))
        self._detail = ExampleDetail(right)
        self._detail.back_requested.connect(self._show_grid)
        self._detail.prompt_chosen.connect(self._on_prompt)
        self._pages.addWidget(self._detail)
        col.addWidget(self._pages, 1)
        root.addWidget(right, 1)

        self._rail.set_current(self._group)
        self._painted = False
        self._counts()
        apply_font_scale_to_tree(self)
        self._search.setFocus()

    def showEvent(self, event):  # noqa: N802 - Qt override
        """The first fill waits for a viewport with a width."""






        super().showEvent(event)
        if not self._painted:
            self._painted = True
            self._repaint()



    def _build_search(self, parent: QWidget) -> QWidget:
        bar = QWidget(parent)
        row = QHBoxLayout(bar)
        row.setContentsMargins(GRID_MARGIN, 14, GRID_MARGIN + 4, 10)
        row.setSpacing(10)
        self._search = QLineEdit(bar)
        self._search.setObjectName("librarySearch")
        self._search.setPlaceholderText(tr("Search examples…"))
        self._search.setClearButtonEnabled(True)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._repaint)
        self._search.textChanged.connect(self._debounce.start)
        row.addWidget(self._search, 1)
        self._count = QLabel("", bar)
        self._count.setObjectName("libraryCount")
        row.addWidget(self._count, 0, Qt.AlignmentFlag.AlignVCenter)
        return bar

    def _build_grid(self, parent: QWidget) -> QWidget:
        self._scroll = QScrollArea(parent)
        self._scroll.setObjectName("libraryScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setStyleSheet(SCROLL_QSS)
        self._scroll.viewport().installEventFilter(self)
        holder = QWidget(self._scroll)
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(GRID_MARGIN, 0, GRID_MARGIN, GRID_MARGIN)
        outer.setSpacing(0)
        self._grid_host = QWidget(holder)
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(CARD_GAP)
        self._grid.setVerticalSpacing(CARD_GAP)
        outer.addWidget(self._grid_host)
        self._empty = QLabel(tr("No example matches that."), holder)
        self._empty.setObjectName("libraryEmpty")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.hide()
        outer.addWidget(self._empty)
        outer.addStretch(1)
        self._scroll.setWidget(holder)
        return self._scroll



    def _query(self) -> str:
        return self._search.text().strip()

    def _visible(self, hits=None) -> list:
        """What the pane shows, in file order."""







        cases = match_cases(self._cases, self._search.text()) if hits is None else hits
        if self._query():
            return cases
        return [case for case in cases if case.group == self._group]

    def _counts(self, hits=None) -> dict:
        """The rail's numbers for the query as it stands."""




        if hits is None:
            hits = match_cases(self._cases, self._search.text())
        counts: dict = {}
        for case in hits:
            counts[case.group] = counts.get(case.group, 0) + 1
        self._rail.set_counts(counts)
        self._rail.set_current("" if self._query() else self._group)
        return counts

    def _say_count(self, cases: list) -> None:
        self._count.setText(
            tr("1 example") if len(cases) == 1 else tr("%n examples", "", len(cases)))

    def _repaint(self) -> None:



        hits = match_cases(self._cases, self._search.text())
        self._counts(hits)
        cases = self._visible(hits)
        self._say_count(cases)
        self._fill(cases)

    def _fill(self, cases: list) -> None:





        keep = self._focused_slug()
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cards = []
        self._focus = -1
        columns = self._column_count()
        self._columns = columns
        for index, case in enumerate(cases):
            card = ExampleCard(case, self._grid_host)
            card.setFixedHeight(scale_px_length(CARD_H))
            card.clicked.connect(lambda c=case: self.open_case(c))
            self._grid.addWidget(card, index // columns, index % columns)
            self._cards.append(card)
        for column in range(self._grid.columnCount()):
            self._grid.setColumnStretch(column, 0)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)
        self._empty.setVisible(not cases)
        self._grid_host.setVisible(bool(cases))
        self._restore_focus(keep)

    def _focused_slug(self) -> str:
        """The slug under the keyboard, or an empty string."""
        if 0 <= self._focus < len(self._cards):
            case = getattr(self._cards[self._focus], "case", None)
            return str(getattr(case, "slug", "") or "")
        return ""

    def _restore_focus(self, slug: str) -> None:
        """Put the highlight back on ``slug`` if it is still on the shelf."""





        if not slug:
            return
        for index, card in enumerate(self._cards):
            if str(getattr(card.case, "slug", "") or "") == slug:
                self._focus = index
                card.set_focused(True)
                return

    def _column_count(self) -> int:
        width = self._scroll.viewport().width() - 2 * scale_px_length(GRID_MARGIN)
        step = scale_px_length(CARD_W + CARD_GAP)
        if width <= 0 or step <= 0:
            return 2
        return max(1, int((width + scale_px_length(CARD_GAP)) // step))

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        if event.type() == QEvent.Type.Resize and watched is self._scroll.viewport():
            if self._column_count() != self._columns:
                self._fill(self._visible())
        return super().eventFilter(watched, event)



    def _on_rail(self, key: str) -> None:
        """A group is a place, so going to one leaves the search behind."""
        self._group = key
        if self._query():
            self._search.blockSignals(True)
            self._search.clear()
            self._search.blockSignals(False)
            self._debounce.stop()
        self._rail.set_current(key)
        self._show_grid()
        self._repaint()

    def open_case(self, case) -> None:
        self._detail.set_case(case)
        self._pages.setCurrentIndex(_DETAIL_PAGE)


        self._searchbar.hide()

    def _show_grid(self) -> None:
        self._pages.setCurrentIndex(_GRID_PAGE)
        self._searchbar.show()
        self._search.setFocus()

    def _on_prompt(self, text: str) -> None:
        """Close first, then hand the prompt over."""









        case = self._detail.case
        self.accept()
        if case is not None:
            self.example_chosen.emit(case.slug)
        self.prompt_chosen.emit(str(text))



    def _move_focus(self, delta: int) -> None:
        """Walk the grid in reading order."""







        if not self._cards:
            return
        if self._focus < 0:
            index = 0 if delta > 0 else len(self._cards) - 1
        else:
            index = max(0, min(self._focus + delta, len(self._cards) - 1))
        if self._focus >= 0:
            self._cards[self._focus].set_focused(False)
        self._focus = index
        card = self._cards[index]
        card.set_focused(True)
        self._scroll.ensureWidgetVisible(card, 0, scale_px_length(CARD_H))

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        key = event.key()
        if self._pages.currentIndex() == _DETAIL_PAGE:
            if key == Qt.Key.Key_Escape:
                self._show_grid()
                return
            super().keyPressEvent(event)
            return
        if key == Qt.Key.Key_Escape:
            if self._search.text():
                self._search.clear()
                self._debounce.stop()
                self._repaint()
                return
            self.reject()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if 0 <= self._focus < len(self._cards):
                self.open_case(self._cards[self._focus].case)
            elif self._cards:
                self.open_case(self._cards[0].case)
            return



        if key == Qt.Key.Key_Down:
            self._move_focus(1)
            return
        if key == Qt.Key.Key_Up:
            self._move_focus(-1)
            return
        if key == Qt.Key.Key_PageDown:
            self._move_focus(2 * (self._columns or 1))
            return
        if key == Qt.Key.Key_PageUp:
            self._move_focus(-2 * (self._columns or 1))
            return
        super().keyPressEvent(event)

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Connectors directory: thirty-eight sources you can actually find one in."""




































from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics
from qgis.PyQt.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core import catalog
from ..core.background import run_sliced
from ..core.connector_locale import country_of, sort_by_locale
from .font_scale import scale_px_length, scale_qss_font_px, widget_pixel_ratio
from .permission_chip import glyph_tile
from .settings_pages import (
    HAIRLINE,
    MUTED,
    PAGE_TITLE_QSS,
    SCROLL_QSS,
    TINT,
    TINT_HOVER,
)
from .style import (
    ACCENT,
    ACCENT_BORDER,
    ACCENT_TINT,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    LINE,
    RADIUS_BOX,
    SURFACE,
)
from .styles import INK_HOVER_FILL
from .widgets import WrapLabel


ALL_KEY = "__all__"
PLUGINS_KEY = "__plugins__"
INSIDE_KEY = "__inside__"



_NATIONAL_KEY = "national"









CATEGORY_ACCENTS = {
    "worldwide": "#3E86D6",
    "national": "#7C6CD0",
    "imagery": "#1F9E96",
    "terrain": "#8D6E63",
    "nature": "#5B9E3F",
    "people": "#C96A8C",
    "catalogs": "#C98A2E",
    PLUGINS_KEY: "#5F7D8C",
    INSIDE_KEY: "#6B7A8F",
}
_DEFAULT_ACCENT = "#3E86D6"




_ROW_MIN_W = 300
_ROW_GAP = 10
_ROW_VGAP = 8
_SCROLLBAR_W = 14
_TILE_PX = 32
_TILE_GLYPH_PX = 17

_TILE_CODE_PX = 12
_SEARCH_W = 230






_ROW_QSS = (
    f"QFrame#connectorRow {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_BOX}px; }}"
    f"QFrame#connectorRow:hover {{ background: {ACCENT_TINT}; border-color: {ACCENT_BORDER}; }}"
    f"QLabel#connectorName {{ font-size: {FONT_BASE}px; font-weight: 600; color: palette(text);"
    " background: transparent; }"
    f"QLabel#connectorNote {{ font-size: {FONT_HINT}px; color: {MUTED}; background: transparent; }}"
)




_STATIC_ROW_QSS = (
    f'QFrame#connectorRow[static="true"]:hover {{ background: {SURFACE};'
    f" border-color: {LINE}; }}"
)




_PAGE_ROW_QSS = _ROW_QSS + _STATIC_ROW_QSS

_SEARCH_QSS = (
    f"QLineEdit#connectorSearch {{ background: {TINT};"
    f" border: 1px solid {HAIRLINE}; border-radius: 16px; padding: 6px 14px;"
    f" font-size: {FONT_BASE}px; color: palette(text); }}"
    f"QLineEdit#connectorSearch:focus {{ border-color: {ACCENT_BORDER}; }}"
)











_CHIP_PAD_X = 13
_CHIP_PAD_Y = 5


def chip_qss(radius: int) -> str:
    return (
        f"QPushButton#filterChip {{ background: {TINT};"
        f" border: 1px solid {HAIRLINE}; border-radius: {radius}px;"
        f" padding: {_CHIP_PAD_Y}px {_CHIP_PAD_X}px; font-size: {FONT_BODY}px;"
        f" color: {MUTED}; }}"
        f"QPushButton#filterChip:hover {{ background: {TINT_HOVER};"
        " color: palette(text); }"
        f"QPushButton#filterChip:checked {{ background: {ACCENT_TINT}; color: palette(text);"
        f" border-color: {ACCENT_BORDER}; font-weight: 600; }}"
    )





def more_qss(radius: int) -> str:
    return (
        "QPushButton#shelfMore { background: transparent; border: 1px solid transparent;"
        f" border-radius: {radius}px; padding: 5px 12px; font-size: {FONT_HINT}px;"
        f" color: {MUTED}; text-align: left; }}"
        f"QPushButton#shelfMore:hover {{ background: {TINT}; color: {ACCENT};"
        f" border-color: {ACCENT_BORDER}; }}"
    )





_TILE_ACTION_QSS = (
    "QPushButton { background: palette(text); color: palette(base); border: none;"
    f" border-radius: 11px; padding: 3px 12px; font-size: {FONT_HINT}px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {INK_HOVER_FILL}; }}"
)

_SECTION_QSS = (
    f"QLabel#connectorSection {{ font-size: {FONT_BODY}px; font-weight: 600; color: palette(text);"
    " background: transparent; }"
)
_TALLY_QSS = f"font-size: {FONT_BODY}px; color: {MUTED}; background: transparent;"

_PAINT_SLICE_MS = 10
_EMPTY_QSS = f"font-size: {FONT_BASE}px; color: {MUTED}; padding: 36px 12px; background: transparent;"






def connector_note(row: dict, tr) -> str:
    """The line under a name: what it is for, or the facts when nobody wrote one."""





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


def code_tile(widget, code: str, accent: str, size: int, font_px: int) -> QLabel:
    """A rounded tile carrying two letters instead of a glyph, in ``accent``."""








    color = QColor(accent)
    tile = QLabel(str(code or "")[:2].upper(), widget)
    tile.setObjectName("codeTile")
    tile.setFixedSize(size, size)
    tile.setAlignment(Qt.AlignmentFlag.AlignCenter)
    tile.setStyleSheet(
        f"QLabel#codeTile {{ background: rgba({color.red()}, {color.green()}, {color.blue()}, 0.16);"
        f" color: {accent}; border: none; border-radius: {size // 3 + 2}px;"
        f" font-size: {font_px}px; font-weight: 700; letter-spacing: 0.5px; }}")
    return tile


class ConnectorTile(QFrame):
    """One connector in the grid: tile, name, one line, and its switch."""







    clicked = pyqtSignal()

    def __init__(self, name: str, note: str, glyph: str, accent: str,
                 control: QWidget | None = None, parent=None, code: str = ""):
        super().__init__(parent)
        self.setObjectName("connectorRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._control = control
        self._full_note = str(note or "")
        self._elided_width = -1

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(11)
        side = scale_px_length(_TILE_PX)
        if code:
            mark = code_tile(self, code, accent, side, scale_px_length(_TILE_CODE_PX))
        else:
            mark = glyph_tile(self, glyph or "globe", accent, side,
                              scale_px_length(_TILE_GLYPH_PX))
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)

        words = QVBoxLayout()
        words.setContentsMargins(0, 0, 0, 0)
        words.setSpacing(1)
        self._name = QLabel(str(name or ""), self)
        self._name.setObjectName("connectorName")
        self._name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full_name = str(name or "")
        words.addWidget(self._name)
        self._note = QLabel(self)
        self._note.setObjectName("connectorNote")
        self._note.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        words.addWidget(self._note)
        row.addLayout(words, 1)





        if control is not None:
            control.setParent(self)
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter)
        self._relayout()

    def _text_width(self) -> int:
        used = 24 + scale_px_length(_TILE_PX) + 11
        if self._control is not None:
            used += self._control.sizeHint().width() + 11
        return max(80, self.width() - used)

    def _relayout(self) -> None:
        """Elide both lines by hand: a row that clips mid-word looks broken."""







        width = self._text_width()
        if width == self._elided_width:
            return
        self._elided_width = width
        name = self._name.fontMetrics().elidedText(
            self._full_name, Qt.TextElideMode.ElideRight, width)
        if name != self._name.text():
            self._name.setText(name)
        note = self._note.fontMetrics().elidedText(
            self._full_note, Qt.TextElideMode.ElideRight, width)
        if note != self._note.text():
            self._note.setText(note)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._relayout()

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        control = self._control
        if control is not None and control.isVisible():
            local = control.mapFrom(self, event.pos())
            if control.rect().contains(local):
                super().mouseReleaseEvent(event)
                return
        if self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ConnectorsPage(QWidget):
    """Search, category pills, and the sections of two-column rows."""

    plugin_install_requested = pyqtSignal(str)
    plugin_enable_requested = pyqtSignal(str)
    connector_opened = pyqtSignal(str)
    plugin_opened = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sources: list = []
        self._plugins: list = []
        self._capabilities: list = []
        self._filter = ALL_KEY
        self._columns = 0
        self._chip_width = 0
        self._chips: dict = {}
        self._paint_generation = 0


        self._expanded: set = set()
        self._keep_scroll = -1



        self._pending: list = []



        self._shelf_grids: dict = {}
        self.setStyleSheet(_PAGE_ROW_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        head = QVBoxLayout()
        head.setContentsMargins(28, 24, 28, 12)
        head.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(14)
        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(4)
        title = QLabel(self.tr("Connectors"), self)
        title.setStyleSheet(PAGE_TITLE_QSS)
        titles.addWidget(title)








        self._tally = QLabel(self)
        self._tally.setStyleSheet(_TALLY_QSS)
        self._tally.setWordWrap(True)
        titles.addWidget(self._tally)
        title_row.addLayout(titles, 1)
        self._search = QLineEdit(self)
        self._search.setObjectName("connectorSearch")
        self._search.setStyleSheet(_SEARCH_QSS)
        self._search.setPlaceholderText(self.tr("Search connectors"))
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(scale_px_length(_SEARCH_W))
        self._search.textChanged.connect(self._on_search)
        title_row.addWidget(self._search, 0, Qt.AlignmentFlag.AlignTop)
        head.addLayout(title_row)
        outer.addLayout(head)

        self._chip_host = QWidget(self)
        self._chip_col = QVBoxLayout(self._chip_host)
        self._chip_col.setContentsMargins(28, 0, 28, 14)
        self._chip_col.setSpacing(6)
        outer.addWidget(self._chip_host)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(SCROLL_QSS)
        body = QWidget(self._scroll)
        self._col = QVBoxLayout(body)
        self._col.setContentsMargins(28, 2, 28, 24)
        self._col.setSpacing(0)
        self._scroll.setWidget(body)
        self._body = body







        self._scroll.viewport().installEventFilter(self)
        outer.addWidget(self._scroll, 1)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._body.setMaximumWidth(self._scroll.viewport().width())
        return super().eventFilter(obj, event)



    def set_data(self, sources, plugins, capabilities) -> None:
        """Everything the page shows, in one call. Repaints chips and sections."""
        self._sources = [dict(r) for r in list(sources or [])[:500]
                         if isinstance(r, dict) and r.get("id")]
        self._plugins = [dict(r) for r in list(plugins or [])[:500]
                         if isinstance(r, dict) and r.get("folder")]
        self._capabilities = [r for r in list(capabilities or [])[:500]
                              if isinstance(r, dict)]
        self._paint_tally()
        self._paint_chips()
        self._paint()

    def _paint_tally(self) -> None:
        """How many sources the agent can reach, and how many datasets that is."""





        total = len(self._sources)
        if not total:
            self._tally.setText("")
            self._tally.setVisible(False)
            return
        datasets = sum(int(row.get("datasets") or 0) for row in self._sources)
        self._tally.setVisible(True)
        self._tally.setText(
            self.tr("%n data sources", "", total) + (
                self.tr(", {n} ready datasets").format(n=datasets) if datasets else ""))



    def _shelves(self) -> list:
        """``(key, label, count)`` for the pill row, in the server's own order."""





        order: list = []
        labels: dict = {}
        counts: dict = {}
        for row in self._sources:
            key = str(row.get("category") or "")
            if not key:
                continue
            if key not in labels:
                order.append(key)
                labels[key] = str(row.get("category_label") or key)
            counts[key] = counts.get(key, 0) + 1
        shelves = [(ALL_KEY, self.tr("All"), len(self._sources) + len(self._plugins))]










        served = [key for key in catalog.shelf_order() if key in labels]
        if served:
            order = served + [key for key in order if key not in served]
        else:
            order = ([key for key in order if key != _NATIONAL_KEY]
                     + [key for key in order if key == _NATIONAL_KEY])
        shelves += [(key, labels[key], counts[key]) for key in order]
        if self._plugins:
            shelves.append((PLUGINS_KEY, self.tr("QGIS plugins"), len(self._plugins)))
        if self._capabilities:
            shelves.append((INSIDE_KEY, self.tr("Inside QGIS"), len(self._capabilities)))
        return shelves

    def _paint_chips(self) -> None:
        _clear(self._chip_col)
        self._chips = {}
        shelves = self._shelves()
        if len(shelves) <= 1:
            self._chip_host.setVisible(False)
            return
        self._chip_host.setVisible(True)


        line = self._chip_line()
        used = 0
        width = max(scale_px_length(560), self.width() - scale_px_length(68))






        face = QFont(self.font())
        face.setPixelSize(FONT_BODY)
        face.setWeight(QFont.Weight.DemiBold)
        metrics = QFontMetrics(face)
        pad = 2 * _CHIP_PAD_X + 4
        for key, label, _count in shelves:



            chip = QPushButton(label, self._chip_host)
            chip.setObjectName("filterChip")
            chip.setCheckable(True)
            chip.setAutoDefault(False)
            chip.setChecked(key == self._filter)
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.clicked.connect(lambda _checked=False, k=key: self._on_filter(k))
            chip.setMinimumWidth(metrics.horizontalAdvance(label) + pad)
            span = chip.minimumWidth() + 6
            if used and used + span > width:
                line.addStretch(1)
                line = self._chip_line()
                used = 0
            line.addWidget(chip, 0)
            used += span
            self._chips[key] = chip
        line.addStretch(1)









        self._chip_host.setStyleSheet(
            chip_qss(max(10, (metrics.height() + 2 * _CHIP_PAD_Y + 2) // 2)))

    def _chip_line(self):
        holder = QWidget(self._chip_host)
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._chip_col.addWidget(holder)
        return row

    def _on_filter(self, key: str) -> None:
        self._filter = str(key)
        for name, chip in self._chips.items():
            chip.setChecked(name == self._filter)
        self._paint()

    def _on_search(self, _text: str) -> None:
        self._paint()



    def _query(self) -> str:
        return " ".join(self._search.text().lower().split())

    @staticmethod
    def _matches(row: dict, query: str) -> bool:
        if not query:
            return True
        hay = " ".join(str(row.get(key) or "") for key in
                       ("name", "tagline", "category_label", "summary", "licence",
                        "coverage", "folder", "description")).lower()
        hay += " " + " ".join(str(k) for k in (row.get("kinds") or []))
        return all(word in hay for word in query.split())

    def _paint(self) -> None:
        """One pass over the four things the page can show, in one order."""










        _clear(self._col)
        self._drop_pending()
        self._shelf_grids = {}
        self._columns = self._column_count()
        query = self._query()
        want = self._filter
        sources = [r for r in self._sources if self._matches(r, query)]
        plugins = [r for r in self._plugins if self._matches(r, query)]
        shown = 0
        steps: list = []

        if want == PLUGINS_KEY:
            sources = []
        elif want == INSIDE_KEY:
            sources, plugins = [], []
        elif want != ALL_KEY:
            sources = [r for r in sources if str(r.get("category") or "") == want]
            plugins = []

        if sources:
            shown += len(sources)
            if want == ALL_KEY and not query:
                self._shelf_steps(steps, sources)
            else:
                self._grid_steps(steps, self.tr("Data sources") if want == ALL_KEY
                                 else self._label_of(want), self._source_tile,
                                 self._shelf_order(want, sources), key=f"one:{want}")

        if plugins and want in (ALL_KEY, PLUGINS_KEY):
            self._grid_steps(steps, self.tr("QGIS plugins"), self._plugin_tile, plugins,
                             key=PLUGINS_KEY)
            shown += len(plugins)

        if self._capabilities and want in (ALL_KEY, INSIDE_KEY) and not query:
            self._grid_steps(steps, self.tr("Inside QGIS"), self._capability_tile,
                             self._capabilities, key=INSIDE_KEY)
            shown += len(self._capabilities)

        self._paint_generation += 1
        generation = self._paint_generation

        def finish() -> None:
            if not still_wanted():
                return
            if not shown:



                empty = WrapLabel(self.tr("Nothing matches that.") if query
                                  else self.tr("The list arrives when the panel connects."), self)
                empty.setStyleSheet(_EMPTY_QSS)
                empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self._col.addWidget(empty)
            self._col.addStretch(1)



            self._restore_scroll()





        def still_wanted() -> bool:




            try:
                self.isVisible()
            except RuntimeError:
                return False
            return generation == self._paint_generation

        run_sliced(steps, _PAINT_SLICE_MS, still_wanted=still_wanted, on_done=finish)

    def _drop_pending(self) -> None:
        """Throw away the shelf hosts a dropped paint never placed."""
        pending, self._pending = self._pending, []
        for host in pending:
            try:
                host.hide()
                host.setParent(None)
                host.deleteLater()
            except RuntimeError:
                pass

    def _place(self, widget: QWidget) -> None:
        """Into the column, in order; the stretch is added after the shelves."""
        self._col.addWidget(widget)

    def _shelf_steps(self, steps: list, sources: list) -> None:
        """The whole directory: the popular few, then every shelf in server order."""








        popular = sorted((r for r in sources if int(r.get("popular") or 0)),
                         key=lambda r: int(r["popular"]))
        if popular:
            self._grid_steps(steps, self.tr("Popular"), self._source_tile, popular,
                             key="popular")
        lifted = {id(r) for r in popular}
        for key, label, _count in self._shelves():
            if key in (ALL_KEY, PLUGINS_KEY, INSIDE_KEY):
                continue
            rows = [r for r in sources
                    if str(r.get("category") or "") == key and id(r) not in lifted]
            if rows:
                self._grid_steps(steps, label, self._source_tile,
                                 self._shelf_order(key, rows), key=key)
        loose = [r for r in sources
                 if not str(r.get("category") or "") and id(r) not in lifted]
        if loose:
            self._grid_steps(steps, self.tr("Data sources"), self._source_tile, loose,
                             key="loose")

    def _shelf_order(self, key: str, rows: list) -> list:
        """The order one shelf is read in."""






        if key != _NATIONAL_KEY:
            return rows
        try:
            return sort_by_locale(rows)
        except Exception:  # noqa: BLE001 - an unreadable locale is not a broken page
            return rows

    def _label_of(self, key: str) -> str:
        for shelf_key, label, _count in self._shelves():
            if shelf_key == key:
                return label
        return self.tr("Data sources")

    def _section(self, title: str, count: int = 0) -> None:
        """A heading and its count."""






        row = QWidget(self)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(2, 18, 0, 8)
        lay.setSpacing(8)
        label = QLabel(title, row)
        label.setObjectName("connectorSection")
        label.setStyleSheet(_SECTION_QSS)
        lay.addWidget(label, 0)
        if count:
            tally = QLabel(str(count), row)
            tally.setStyleSheet(
                f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;")
            lay.addWidget(tally, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self._place(row)

    def _grid_steps(self, steps: list, title: str, build, rows: list, key: str = "") -> None:
        """The steps of one shelf: its heading, its grid, one tile per row built straight into the grid's host (a tile built on the page and moved."""










        columns = max(1, self._columns)
        holder: dict = {}
        shelf_key = str(key or title)
        all_rows = list(rows)
        total = len(rows)
        cap = catalog.shelf_cap()
        if total > cap and shelf_key not in self._expanded:
            rows = rows[:cap]
        shown = len(rows)

        def open_shelf() -> None:
            self._section(title, total)
            host = QWidget(self)

            host.hide()
            self._pending.append(host)
            grid = QGridLayout(host)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(_ROW_GAP)
            grid.setVerticalSpacing(_ROW_VGAP)
            for column in range(columns):
                grid.setColumnStretch(column, 1)
            holder["host"], holder["grid"] = host, grid

        def add_tile(index: int, row: dict) -> None:
            holder["grid"].addWidget(build(row, holder["host"]), index // columns, index % columns)

        def close_shelf() -> None:
            rest = shown % columns
            if rest:
                for column in range(rest, columns):
                    holder["grid"].addItem(_spacer(), (shown - 1) // columns, column)


            host = holder["host"]
            if host in self._pending:
                self._pending.remove(host)
            self._place(host)
            host.show()
            if total > cap:
                shelf = {"host": host, "grid": holder["grid"], "build": build,
                         "rows": all_rows, "button": None}
                self._shelf_grids[shelf_key] = shelf
                more = self._more_button(shelf_key, total, shown)
                shelf["button"] = more.findChild(QPushButton)
                self._place(more)

        steps.append(open_shelf)
        for index, row in enumerate(rows):
            steps.append(lambda index=index, row=row: add_tile(index, row))
        steps.append(close_shelf)

    def _more_button(self, key: str, total: int, shown: int) -> QWidget:
        """"Show all 16", or "Show less" once the shelf is open."""
        host = QWidget(self)
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 6, 0, 0)
        row.setSpacing(0)
        opened = shown >= total
        label = (self.tr("Show less") if opened
                 else self.tr("Show all {total}").format(total=total))
        button = QPushButton(label, host)
        button.setObjectName("shelfMore")
        button.setStyleSheet(more_qss(max(10, (button.fontMetrics().height() + 12) // 2)))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False, k=key: self._on_expand(k))
        row.addWidget(button, 0)
        row.addStretch(1)
        return host

    def _on_expand(self, key: str) -> None:
        """Open or close one shelf, in the shelf and nowhere else."""







        opened = key in self._expanded
        if opened:
            self._expanded.discard(key)
        else:
            self._expanded.add(key)
        shelf = self._shelf_grids.get(key)
        if shelf is None:


            self.remember_scroll()
            self._paint()
            return
        self._reflow_shelf(shelf, not opened)

    def _reflow_shelf(self, shelf: dict, expanded: bool) -> None:
        """Grow or trim one shelf's grid to what it should now show."""
        grid = shelf["grid"]
        host = shelf["host"]
        rows = shelf["rows"]
        columns = max(1, self._columns)
        want = rows if expanded else rows[:catalog.shelf_cap()]
        host.setUpdatesEnabled(False)
        try:


            for index in reversed(range(grid.count())):
                item = grid.itemAt(index)
                if item is not None and item.widget() is None:
                    grid.removeItem(item)
            have = grid.count()
            for index in reversed(range(len(want), have)):
                item = grid.takeAt(index)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.hide()
                    widget.setParent(None)
                    widget.deleteLater()
            for index in range(have, len(want)):
                grid.addWidget(shelf["build"](want[index], host),
                               index // columns, index % columns)
            rest = len(want) % columns
            if rest:
                for column in range(rest, columns):
                    grid.addItem(_spacer(), (len(want) - 1) // columns, column)
        finally:
            host.setUpdatesEnabled(True)
        button = shelf["button"]
        if button is not None:
            button.setText(self.tr("Show less") if expanded
                           else self.tr("Show all {total}").format(total=len(rows)))

    def remember_scroll(self) -> None:
        """Put the scroll back after the next repaint."""






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

    def _column_count(self) -> int:
        """How many rows fit across, from the page's own width."""






        width = self.width() - scale_px_length(56 + _SCROLLBAR_W)
        return max(1, min(3, (width + _ROW_GAP) // (scale_px_length(_ROW_MIN_W) + _ROW_GAP)))



    def _source_tile(self, row: dict, parent: QWidget | None = None) -> ConnectorTile:
        """One source."""









        key = str(row.get("id") or "")
        count = int(row.get("datasets") or 0)
        mark = QLabel(str(count) if count else "")
        mark.setStyleSheet(
            f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;")
        mark.setToolTip(self.tr("%n ready datasets", "", count) if count else "")
        tile = ConnectorTile(str(row.get("name") or key), connector_note(row, self.tr),
                             str(row.get("glyph") or "globe"),
                             accent_of(row.get("category")), mark, parent or self,
                             code=country_of(row))
        tile.clicked.connect(lambda k=key: self.connector_opened.emit(k))
        return tile

    def _plugin_tile(self, row: dict, parent: QWidget | None = None) -> ConnectorTile:
        """One QGIS plugin, wearing the state of this machine rather than a choice."""















        key = str(row.get("folder") or "")
        if row.get("installed") and row.get("loaded"):
            control = QLabel(self.tr("On"))
            control.setStyleSheet(
                f"font-size: {FONT_HINT}px; color: {ACCENT}; background: transparent;"
                " font-weight: 600;")
        elif row.get("installed"):
            control = self._tile_button(self.tr("Enable"), self.plugin_enable_requested, key)
        else:
            control = self._tile_button(self.tr("Install"), self.plugin_install_requested, key)
        tile = ConnectorTile(str(row.get("name") or key), self._plugin_note(row),
                             str(row.get("glyph") or "package"),
                             accent_of(PLUGINS_KEY), control, parent or self)



        icon = str(row.get("icon") or "").strip()
        if icon:
            _wear_logo(tile, icon)
        tile.clicked.connect(lambda k=key: self.plugin_opened.emit(k))
        return tile

    def _tile_button(self, label: str, signal, key: str) -> QPushButton:
        """The small dark pill a plugin tile acts through."""




        button = QPushButton(label, self)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(scale_qss_font_px(_TILE_ACTION_QSS))
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False, k=key: signal.emit(k))
        return button

    def _plugin_note(self, row: dict) -> str:
        """State first, else what the plugin is for, cut at a word."""






        if not row.get("installed"):




            return self.tr("Not installed on this machine.")
        if not row.get("loaded"):
            return self.tr("Switched off in the QGIS plugin manager.")
        summary = " ".join(str(row.get("summary") or row.get("description") or "").split())
        summary = summary.split(". ")[0].rstrip(".")
        return summary or str(row.get("version") or "")

    def _capability_tile(self, entry: dict, parent: QWidget | None = None) -> ConnectorTile:
        count = QLabel(str(entry.get("count") or ""))
        count.setStyleSheet(f"font-size: {FONT_HINT}px; color: {MUTED}; background: transparent;")
        tile = ConnectorTile(str(entry.get("name") or ""), str(entry.get("note") or ""),
                             str(entry.get("glyph") or "layers"),
                             accent_of(INSIDE_KEY), count, parent or self)

        tile.setCursor(Qt.CursorShape.ArrowCursor)
        tile.setProperty("static", "true")
        return tile



    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)





        if self._column_count() != self._columns:
            self._paint()
        if abs(self.width() - self._chip_width) >= scale_px_length(24):
            self._chip_width = self.width()
            self._paint_chips()

    def focus_search(self) -> None:
        self._search.setFocus()


def _spacer():
    from qgis.PyQt.QtWidgets import QSpacerItem

    return QSpacerItem(1, 1, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)


def _wear_logo(tile: ConnectorTile, path: str) -> None:
    """Swap the glyph tile for the plugin's real logo, when the file loads."""





    from .logo_tile import logo_pixmap

    label = tile.layout().itemAt(0).widget()
    if label is None:
        return
    side = scale_px_length(_TILE_PX)
    chip = logo_pixmap(path, side, widget_pixel_ratio(label))
    if chip is None:
        return
    label.setStyleSheet("QLabel { background: transparent; border: none; }")
    label.setPixmap(chip)


def _clear(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:



            widget.hide()
            widget.setParent(None)
            widget.deleteLater()

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later























from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..shared import tr
from . import common as C
from .common import escape, link_html, source_rows
from .parts import Breadcrumb, InfoTable, PageHeader, UserBubble, label, primary_button, section_title
from .pictures import wear_source_mark
from .pressable import Pressable, labels_through


class SourceChip(Pressable):


    def __init__(self, row: dict, parent=None):
        super().__init__(parent, radius=C.px(14))
        self.key = str(row.get("id") or "")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(C.px(4), C.px(2), C.px(10), C.px(2))
        lay.setSpacing(C.px(8))
        mark = QLabel(self)
        wear_source_mark(mark, row, str(row.get("_accent") or "#5d5d5d"), C.px(20))
        lay.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(label(self, str(row.get("name") or self.key), C.BODY_PX, C.T.text), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self.setFixedHeight(C.px(28))


class ExampleDetail(QWidget):


    back_requested = pyqtSignal()

    crumb_requested = pyqtSignal(int)
    prompt_chosen = pyqtSignal(str)
    connector_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.case = None
        self._hero = None
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
        self._crumbs = Breadcrumb(self._body)
        self._crumbs.crumb.connect(self.crumb_requested.emit)
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

        self._use = primary_button(self, tr("Use this example"))

        use = self._use
        use.setAutoDefault(True)
        use.setDefault(True)
        use.clicked.connect(self._use_prompt)

    def _use_prompt(self) -> None:
        if self.case is not None:
            self.prompt_chosen.emit(str(self.case.prompt or ""))

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Type.Resize and watched is self._scroll.viewport():
            self._centre()
        return super().eventFilter(watched, event)

    def _centre(self) -> None:

        viewport = self._scroll.viewport().width()
        if not self._scroll.isVisible() or viewport < C.px(200):


            viewport = self.width() - C.px(10)
        room = viewport - 2 * C.px(C.SPACE_4)
        width = max(C.px(C.TILE_MIN_W), min(room, C.px(C.DETAIL_W)))
        side = max(C.px(C.SPACE_4), (viewport - width) // 2)
        self._page.setContentsMargins(side, C.px(C.SPACE_3), side, C.px(C.SPACE_5))
        if self._hero is not None:
            try:
                self._hero.setFixedHeight(self._hero.heightForWidth(max(1, width)))
            except RuntimeError:
                self._hero = None



    def _clear(self) -> None:
        self._hero = None
        self._use.setParent(self)
        self._use.hide()
        while self._col.count():
            item = self._col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def set_trail(self, names: list) -> None:

        self._crumbs.set_trail(names)

    def set_case(self, case, group_label: str = "", root: str = "") -> None:



        self.case = case
        self._clear()
        if case is None:
            return
        self._crumbs.set_trail([root or tr("Examples"), group_label or str(case.group or "")])
        self._scroll.verticalScrollBar().setValue(0)
        header = PageHeader(self._column, str(case.title or ""), str(case.outcome or ""), self._use)
        self._use.show()
        self._col.addWidget(header)
        self._col.addSpacing(C.px(C.SPACE_3))
        from .slider import SliderView

        small = case.pair(small=True)
        self._hero = SliderView(case.image, parent=self._column, radius=C.RADIUS_HERO,
                                label=str(case.title or ""), label_px=20, pair=case.pair(),
                                split=case.split,
                                fallback=small if small[0] else (case.image_small,))
        self._hero.setAccessibleName(str(case.outcome or ""))
        self._col.addWidget(self._hero)
        self._col.addSpacing(C.px(C.SPACE_3))
        self._col.addWidget(UserBubble(str(case.prompt or ""), self._column))
        self._col.addSpacing(C.px(C.SPACE_5))
        self._col.addWidget(section_title(self._column, tr("Information")))
        self._col.addSpacing(C.px(12))
        self._col.addWidget(self._information(case))
        self._centre()
        self._use.setFocus()
        self._hero.play_intro()

    def _information(self, case) -> QWidget:
        table = InfoTable(self._column)
        table.link_activated.connect(self._on_link)
        samples = tuple(getattr(case, "samples", ()) or ())
        if samples:
            parts = []
            for sample in samples:
                bit = link_html(sample.url, sample.name)
                if sample.licence:
                    bit += f' <span style="color: {C.T.text_2};">{escape(sample.licence)}</span>'
                parts.append(bit)
            table.add(tr("Sample file"), "<br>".join(parts), rich=True)
        sources = source_rows(case)
        if sources:
            table.add(tr("Data sources"), self._source_chips(sources))
        steps = [str(s) for s in (getattr(case, "steps", ()) or ()) if str(s)]
        if steps:
            table.add(tr("Steps"), "<br>".join(
                f'<span style="color: {C.T.text_2};">{n}.</span>&nbsp; {escape(step)}'
                for n, step in enumerate(steps, 1)), rich=True)
        caveat = str(getattr(case, "caveat", "") or "")
        if caveat:

            table.add(tr("Good to know"), caveat)
        return table

    def _source_chips(self, rows: list) -> QWidget:
        from ..widgets import FlowLayout

        host = QWidget(self._column)

        lay = FlowLayout(host, C.px(4), C.px(4))
        lay.setContentsMargins(0, 0, 0, 0)
        for row in rows[:6]:
            chip = SourceChip(row, host)
            chip.clicked.connect(lambda k=chip.key: self.connector_requested.emit(k))
            lay.addWidget(chip)
        return host

    def _on_link(self, href: str) -> None:
        href = str(href or "")
        if href.startswith("https://"):
            from ..external_links import open_external_url

            open_external_url(href, parent=self)

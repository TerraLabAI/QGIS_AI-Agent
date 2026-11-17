# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..icons import icon_for
from ..shared import tr
from ..widgets import FlowLayout
from . import common as C
from .common import escape, link_html, source_rows
from .parts import Breadcrumb, InfoTable, PageHeader, label, primary_button, section_title
from .pictures import PictureView, wear_square_mark
from .pressable import Pressable, labels_through
from .slider import SliderView


class SourceChip(Pressable):


    def __init__(self, row: dict, parent=None):
        super().__init__(parent, radius=C.px(14))
        self.key = str(row.get("id") or "")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(C.px(4), C.px(2), C.px(10), C.px(2))
        lay.setSpacing(C.px(8))
        mark = QLabel(self)
        wear_square_mark(mark, row, str(row.get("_accent") or "#5d5d5d"), C.px(20), C.px(5), 2)
        lay.addWidget(mark, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(label(self, str(row.get("name") or self.key), C.BODY_PX, C.T.text), 0,
                      Qt.AlignmentFlag.AlignVCenter)
        labels_through(self)
        self.setAccessibleName(str(row.get("name") or self.key))
        self.setFixedHeight(C.px(28))



_CARD_SOURCES = 3

_ATTACH_W = 120

_HERO_RATIO = 0.5
_COPIED_MS = 1500


class PromptCard(QFrame):


    connector_requested = pyqtSignal(str)

    def __init__(self, prompt: str, sources: list, use: QPushButton, parent=None, attach=()):
        super().__init__(parent)
        self.setObjectName("promptCard")
        self.setStyleSheet(
            f"QFrame#promptCard {{ background: transparent; border: 1px solid {C.T.border};"
            f" border-radius: {C.px(C.RADIUS_HERO)}px; }}")
        self._prompt = str(prompt or "")
        col = QVBoxLayout(self)
        col.setContentsMargins(C.px(C.SPACE_2), C.px(C.SPACE_2), C.px(C.SPACE_2), C.px(C.SPACE_2))
        col.setSpacing(C.px(C.SPACE_1))
        col.addWidget(label(self, tr("What you type"), C.SMALL_PX, C.T.text_2, C.MEDIUM))
        text = label(self, self._prompt, C.READ_PX, C.T.text, wrap=True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        col.addWidget(text)
        if attach:
            col.addSpacing(C.px(4))
            col.addWidget(self._attached(attach))
        col.addSpacing(C.px(4))
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(C.px(4))
        if sources:
            row.addWidget(label(self, tr("Uses"), C.BODY_PX, C.T.text_2), 0, Qt.AlignmentFlag.AlignVCenter)
            for source in sources[:_CARD_SOURCES]:
                chip = SourceChip(source, self)
                chip.clicked.connect(lambda _c=False, k=chip.key: self.connector_requested.emit(k))
                row.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addStretch(1)
        self._copy = QPushButton(tr("Copy"), self)
        self._copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy.setAutoDefault(False)
        self._copy.setIconSize(QSize(16, 16))
        self._copy.setStyleSheet(C.qss(
            f"QPushButton {{ background: transparent; border: none; border-radius: {C.px(8)}px;"
            f" padding: 6px 10px; font-size: {C.BODY_PX}px; color: {C.T.text_2}; }}"
            f"QPushButton:hover {{ background: {C.T.hover}; color: {C.T.text}; }}"
            "QPushButton:focus { outline: none; }"))
        self._copy.clicked.connect(self._on_copy)
        self._set_copy_look(False)
        row.addWidget(self._copy, 0, Qt.AlignmentFlag.AlignVCenter)
        use.setParent(self)
        row.addSpacing(C.px(4))
        row.addWidget(use, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(row)

    def _attached(self, attach) -> QWidget:

        host = QWidget(self)
        lay = QHBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(C.px(C.SPACE_1))
        for sample in attach:
            item = QWidget(host)
            item.setToolTip(sample.licence or sample.name)
            box = QVBoxLayout(item)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(C.px(4))
            picture = PictureView(sample.url, parent=item, full=True, radius=C.px(8), label=sample.name)
            picture.setFixedSize(C.px(_ATTACH_W), round(C.px(_ATTACH_W) * 9 / 16))
            box.addWidget(picture)
            box.addWidget(label(item, sample.name, C.SMALL_PX, C.T.text_2))
            lay.addWidget(item, 0, Qt.AlignmentFlag.AlignTop)
        lay.addStretch(1)
        return host

    def _set_copy_look(self, copied: bool) -> None:

        self._copy.setText(tr("Copied") if copied else tr("Copy"))
        self._copy.setIcon(icon_for(self._copy, C.line_glyph("check" if copied else "copy"), 16, QColor(C.T.text_2)))

    def _on_copy(self) -> None:
        QApplication.clipboard().setText(self._prompt)
        self._set_copy_look(True)

        timer = getattr(self, "_copied_timer", None)
        if timer is None:
            timer = self._copied_timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._reset_copy)
        timer.start(_COPIED_MS)

    def _reset_copy(self) -> None:
        try:
            self._set_copy_look(False)
        except RuntimeError:
            pass


class ExampleDetail(QWidget):


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
        C.keep_gutter(self._scroll)
        self._body = QWidget(self._scroll)
        self._page = QVBoxLayout(self._body)
        self._page.setSpacing(0)
        self._crumbs = Breadcrumb(self._body)
        self._crumbs.crumb.connect(self.crumb_requested.emit)
        self._page.addWidget(self._crumbs)
        self._column = QWidget(self._body)
        self._col = QVBoxLayout(self._column)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)
        self._page.addWidget(self._column)
        self._page.addStretch(1)
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)
        self._scroll.viewport().installEventFilter(self)

        self._use = primary_button(self, tr("Try in chat"))
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
        width = max(C.px(C.TILE_MIN_W), min(room, C.px(C.COLUMN_W)))
        side = max(C.px(C.SPACE_4), (viewport - width) // 2) - C.px(C.EDGE)
        self._page.setContentsMargins(side, 0, side, C.px(C.SPACE_5))
        if self._hero is not None:
            try:
                width = max(1, width - 2 * C.px(C.EDGE))
                self._hero.setFixedHeight(min(self._hero.heightForWidth(width),
                                              round(width * _HERO_RATIO)))
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
        self._col.addWidget(PageHeader(self._column, str(case.title or ""), str(case.outcome or "")))
        self._col.addSpacing(C.px(C.SPACE_3))

        small = case.pair(small=True)
        self._hero = SliderView(case.image, parent=self._column, radius=C.RADIUS_HERO,
                                label=str(case.title or ""), label_px=20, pair=case.pair(),
                                split=case.split,
                                fallback=small if small[0] else (case.image_small,))
        self._hero.setAccessibleName(str(case.outcome or ""))
        self._col.addWidget(self._edged(self._hero))
        self._col.addSpacing(C.px(C.SPACE_2))
        sources = source_rows(case)
        card = PromptCard(str(case.prompt or ""), sources, self._use, self._column,
                          attach=tuple(getattr(case, "attach", ()) or ()))
        card.connector_requested.connect(self.connector_requested.emit)
        self._use.show()
        self._col.addWidget(self._edged(card))
        information = self._information(case, sources)
        if information.rows():
            self._col.addSpacing(C.px(C.SPACE_5))
            self._col.addWidget(section_title(self._column, tr("Information")))
            self._col.addSpacing(C.px(12))
            self._col.addWidget(self._edged(information))
        else:

            information.setParent(None)
            information.deleteLater()
        self._centre()
        self._use.setFocus()
        self._hero.play_intro()

    def _edged(self, widget: QWidget) -> QWidget:

        holder = QWidget(self._column)
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(C.px(C.EDGE), 0, C.px(C.EDGE), 0)
        lay.setSpacing(0)
        widget.setParent(holder)
        lay.addWidget(widget)
        return holder

    def _information(self, case, sources: list) -> InfoTable:
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
        if len(sources) > _CARD_SOURCES:
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

        host = QWidget(self._column)
        lay = FlowLayout(host, C.px(4), C.px(4))
        lay.setContentsMargins(0, 0, 0, 0)
        for row in rows[:6]:
            chip = SourceChip(row, host)
            chip.clicked.connect(lambda _c=False, k=chip.key: self.connector_requested.emit(k))
            lay.addWidget(chip)
        return host

    def _on_link(self, href: str) -> None:
        href = str(href or "")
        if href.startswith("https://"):
            from ..external_links import open_external_url

            open_external_url(href, parent=self)

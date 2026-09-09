# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""One connector, on its own page: what it holds, what to ask it, what it costs."""



























from __future__ import annotations

from qgis.PyQt.QtCore import QEvent, Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .connectors_page import PLUGINS_KEY, accent_of
from .external_links import open_external_url
from .font_scale import scale_px_length
from .icons import pixmap_for
from .permission_chip import glyph_tile
from .settings_pages import (
    GROUP_TITLE_QSS,
    HAIRLINE,
    MUTED,
    ROW_NOTE_QSS,
    SCROLL_QSS,
    TINT,
    SettingGroup,
)
from .shared import event_pos
from .style import (
    ACCENT_BORDER,
    ACCENT_TINT,
    FONT_BASE,
    INK_2,
    INSET,
    LINE,
    ON_ORANGE,
    ORANGE,
    ORANGE_DARK,
    ORANGE_TINT,
    RADIUS_PANEL,
    SURFACE,
    accent_ink,
)
from .styles import INK_HOVER_FILL
from .widgets import ElidedLabel



_TILE_PX = 56
_TILE_GLYPH_PX = 28
_PROMPTS = 3


_MAX_ACTIONS = 8
_LABEL_W = 120

_INSTALL_QSS = (
    "QPushButton { background: palette(text); color: palette(base); border: none;"
    f" border-radius: 15px; padding: 6px 18px; font-size: {FONT_BASE}px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {INK_HOVER_FILL}; }}"
)

_TITLE_QSS = f"font-size: {FONT_BASE + 7}px; font-weight: 600; color: palette(text); background: transparent;"
_SUBTITLE_QSS = f"font-size: {FONT_BASE}px; color: {MUTED}; background: transparent;"
_BODY_QSS = f"font-size: {FONT_BASE}px; color: palette(text); background: transparent;"







_PROMPT_CARD_QSS = (
    f"QFrame#promptCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    f"QFrame#promptCard:hover {{ background: {ACCENT_TINT}; border-color: {ACCENT_BORDER}; }}"
    f"QLabel#promptText {{ font-size: {FONT_BASE}px; color: palette(text); background: transparent; }}"
)



_SUMMARY_QSS = (
    f"QFrame#summaryCard {{ background: {INSET}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    f"QLabel#summaryText {{ font-size: {FONT_BASE}px; color: {INK_2}; background: transparent; }}"
)




_NOTICE_QSS = (
    f"QFrame#promptNotice {{ background: {ORANGE_TINT}; border: 1px solid {ORANGE};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    f"QLabel#promptNoticeText {{ font-size: {FONT_BASE}px; color: palette(text);"
    " background: transparent; }"
)
_NOTICE_BTN_QSS = (
    f"QPushButton {{ background: {ORANGE}; color: {ON_ORANGE}; border: none;"
    f" border-radius: 13px; padding: 5px 14px; font-size: {FONT_BASE}px; font-weight: 600; }}"
    f"QPushButton:hover {{ background: {ORANGE_DARK}; }}"
)
_CHIP_QSS = (
    f"QFrame#kindChip {{ background: {TINT}; border: 1px solid {HAIRLINE};"
    " border-radius: 13px; }"
)


def example_prompts(detail: dict) -> list[str]:
    """What to ask this connector: the three the server wrote for it."""





    authored = [str(p).strip() for p in (detail.get("prompts") or []) if str(p or "").strip()]
    return authored[:_PROMPTS]


def tile_pixmap(widget, detail: dict, size: int = _TILE_GLYPH_PX, fallback: str = ""):
    """The mark on a card: the plugin's real logo when there is one, else our glyph."""











    from .logo_tile import logo_pixmap

    path = str(detail.get("icon") or "").strip()
    if path:



        chip = logo_pixmap(path, scale_px_length(size), 1.0)
        if chip is not None:
            return chip



    default = fallback or ("package" if detail.get("folder") else "globe")
    return pixmap_for(widget, str(detail.get("glyph") or default), size)


class PromptCard(QFrame):
    """One thing to ask, as a full-width row."""







    chosen = pyqtSignal(str, object)

    def __init__(self, mention: str, prompt: str, accent: str, parent=None, chip: dict | None = None):
        super().__init__(parent)
        self.setObjectName("promptCard")
        self.setStyleSheet(_PROMPT_CARD_QSS)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._prompt = str(prompt or "")
        self._chip = dict(chip or {})
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 11, 12, 11)
        row.setSpacing(10)



        text = QLabel(self)
        text.setObjectName("promptText")
        if mention:
            tag = _escape(mention)
            line = _escape(self._prompt)
            text.setText(
                f'<span style="color:{accent};font-weight:600;">@{tag}</span> {line}')
        else:
            text.setText(_escape(self._prompt))
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)
        text.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        row.addWidget(text, 1)
        arrow = QLabel(self)
        arrow.setPixmap(pixmap_for(arrow, "chevron_right", 14))
        arrow.setStyleSheet("background: transparent;")
        row.addWidget(arrow, 0, Qt.AlignmentFlag.AlignVCenter)

    def mouseReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event_pos(event)):
            self.chosen.emit(self._prompt, dict(self._chip))
        super().mouseReleaseEvent(event)


class ConnectorPage(QWidget):
    """The detail page of one connector. Repopulated, never rebuilt per connector."""

    back_requested = pyqtSignal()

    prompt_chosen = pyqtSignal(str, object)
    install_requested = pyqtSignal(str)
    enable_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._id = ""
        self._url = ""


        self._blocked = ""
        self._blocked_action = ""
        self._name = ""
        self._notice: QFrame | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        back_row = QHBoxLayout()
        back_row.setContentsMargins(16, 14, 24, 0)
        back_row.setSpacing(0)
        self._back = QPushButton(self.tr("Connectors"), self)
        self._back.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 4px 8px 4px 4px;"
            f" font-size: {FONT_BASE}px; color: {MUTED}; text-align: left; }}"
            f"QPushButton:hover {{ color: {accent_ink()}; }}"
        )
        self._back.setIcon(self._glyph_icon("chevron_left"))
        self._back.setCursor(Qt.CursorShape.PointingHandCursor)
        self._back.clicked.connect(self.back_requested.emit)
        back_row.addWidget(self._back, 0)
        back_row.addStretch(1)
        outer.addLayout(back_row)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setStyleSheet(SCROLL_QSS)
        body = QWidget(self._scroll)
        self._col = QVBoxLayout(body)
        self._col.setContentsMargins(28, 16, 28, 28)
        self._col.setSpacing(0)
        self._scroll.setWidget(body)
        self._body = body








        self._scroll.viewport().installEventFilter(self)
        outer.addWidget(self._scroll, 1)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt override
        if obj is self._scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._body.setMaximumWidth(self._scroll.viewport().width())
        return super().eventFilter(obj, event)



    def _glyph_icon(self, glyph: str):
        from qgis.PyQt.QtGui import QIcon

        return QIcon(pixmap_for(self, glyph, 14))

    def _clear(self) -> None:
        col = self._col
        while col.count():
            item = col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._switch = None
        self._notice = None
        self._blocked = ""
        self._blocked_action = ""

    def _heading(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setStyleSheet(GROUP_TITLE_QSS)
        return label

    def _summary_card(self, summary: str) -> QWidget:
        """The description, in its own inset box rather than loose on the page."""
        card = QFrame(self)
        card.setObjectName("summaryCard")
        card.setStyleSheet(_SUMMARY_QSS)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 13, 16, 13)
        lay.setSpacing(0)
        text = QLabel(summary, card)
        text.setObjectName("summaryText")
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(text)
        return card

    def _accent(self, detail: dict) -> str:
        return accent_of(PLUGINS_KEY if detail.get("folder") else detail.get("category"))

    def set_connector(self, detail: dict) -> None:
        """Paint one connector."""





        self._clear()
        detail = dict(detail or {})
        self._id = str(detail.get("id") or "")
        self._url = str(detail.get("url") or "")
        col = self._col

        col.addWidget(self._header(detail))
        col.addSpacing(scale_px_length(20))

        self._name = str(detail.get("name") or self._id)

        prompts = example_prompts(detail)
        if prompts:
            col.addWidget(self._prompt_row(detail, prompts))
            col.addSpacing(scale_px_length(20))

        summary = str(detail.get("summary") or "").strip()
        if summary:
            col.addWidget(self._summary_card(summary))
            col.addSpacing(scale_px_length(20))

        highlights = detail.get("highlights") or []
        if highlights:
            col.addWidget(self._heading(self.tr("What is inside")))
            col.addSpacing(scale_px_length(8))
            group = SettingGroup(self)
            for item in highlights:
                group.add_row(self._highlight_row(item, self._accent(detail)))
            col.addWidget(group)
            col.addSpacing(scale_px_length(20))

        kinds = [k for k in (detail.get("kinds") or []) if k]
        if kinds:
            col.addWidget(self._heading(self.tr("Skills")))
            col.addSpacing(scale_px_length(8))
            col.addWidget(self._chip_flow(kinds, self._accent(detail)))
            col.addSpacing(scale_px_length(20))

        col.addWidget(self._heading(self.tr("Information")))
        col.addSpacing(scale_px_length(8))
        col.addWidget(self._information(detail))
        col.addStretch(1)

    def _header(self, detail: dict, control: QWidget | None = None) -> QWidget:
        row = QWidget(self)
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(scale_px_length(14))

        side = scale_px_length(_TILE_PX)
        accent = self._accent(detail)
        tile = glyph_tile(row, str(detail.get("glyph")
                                   or ("package" if detail.get("folder") else "globe")),
                          accent, side, scale_px_length(_TILE_GLYPH_PX))
        own = str(detail.get("icon") or "").strip()
        if own:
            pixmap = tile_pixmap(tile, detail, _TILE_PX)
            if pixmap is not None and not pixmap.isNull():
                tile.setStyleSheet("QLabel { background: transparent; border: none; }")
                tile.setPixmap(pixmap)
        lay.addWidget(tile, 0, Qt.AlignmentFlag.AlignTop)

        text = QWidget(row)
        col = QVBoxLayout(text)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(3)
        title = QLabel(str(detail.get("name") or ""), text)
        title.setStyleSheet(_TITLE_QSS)
        title.setWordWrap(True)
        col.addWidget(title)
        subtitle = QLabel(self._subtitle(detail), text)
        subtitle.setStyleSheet(_SUBTITLE_QSS)
        subtitle.setWordWrap(True)
        col.addWidget(subtitle)
        lay.addWidget(text, 1)

        if control is not None:
            control.setParent(row)
            lay.addWidget(control, 0, Qt.AlignmentFlag.AlignTop)
        return row

    def _subtitle(self, detail: dict) -> str:
        """What it is for, then the two facts that place it: count and coverage."""
        if detail.get("folder"):
            bits = [b for b in (str(detail.get("version") or "").strip(),
                                str(detail.get("author") or "").strip()) if b]
            return "  ·  ".join(bits)
        bits = [b for b in (str(detail.get("tagline") or "").strip(),) if b]
        count = int(detail.get("datasets") or 0)
        if count:
            bits.append(self.tr("1 ready source") if count == 1
                        else self.tr("%n ready sources", "", count))
        coverage = str(detail.get("coverage") or "").strip()
        if coverage:
            bits.append(coverage)
        return "  ·  ".join(bits)

    def _prompt_row(self, detail: dict, prompts: list[str]) -> QWidget:
        """The prompts as full-width cards, stacked, ChatGPT's connector page."""







        holder = QWidget(self)
        row = QVBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        folder = str(detail.get("folder") or "")
        name = str(detail.get("name") or folder or detail.get("id") or "")
        chip = {"kind": "plugin" if folder else "source",
                "label": name, "value": folder or str(detail.get("id") or ""),
                "glyph": str(detail.get("glyph") or ("package" if folder else "globe"))}
        accent = self._accent(detail)
        for prompt in prompts:
            card = PromptCard(name, prompt, accent, holder, chip if chip["value"] else None)
            card.chosen.connect(self._on_prompt)
            row.addWidget(card, 0)
        self._notice = self._notice_card(holder)
        self._notice.hide()
        row.addWidget(self._notice, 0)
        return holder

    def _notice_card(self, parent: QWidget) -> QFrame:
        """The box that says why a pressed example went nowhere."""





        card = QFrame(parent)
        card.setObjectName("promptNotice")
        card.setStyleSheet(_NOTICE_QSS)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(14, 11, 12, 11)
        lay.setSpacing(10)
        glyph = QLabel(card)
        glyph.setPixmap(pixmap_for(glyph, "warning", 16, _ink(ORANGE)))
        glyph.setStyleSheet("background: transparent;")
        lay.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel(card)
        text.setObjectName("promptNoticeText")
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(text, 1)
        button = QPushButton(self.tr("Enable"), card)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_NOTICE_BTN_QSS)
        button.setAutoDefault(False)
        button.clicked.connect(self._on_notice_action)
        lay.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
        card._text = text  # noqa: SLF001 - the page owns this widget
        card._button = button  # noqa: SLF001
        return card

    def _on_prompt(self, text: str, chip) -> None:
        """Send the example, or say why it cannot be sent."""





        if not self._blocked:
            self.prompt_chosen.emit(str(text or ""), chip)
            return
        notice = self._notice
        if notice is None:
            return
        notice._text.setText(self._blocked)  # noqa: SLF001
        notice._button.setVisible(bool(self._blocked_action))  # noqa: SLF001
        notice.show()

    def _on_notice_action(self) -> None:
        """The one move the notice named: start the plugin QGIS has switched off."""




        if self._blocked_action == "enable" and self._id:
            self.enable_requested.emit(self._id)

    def _highlight_row(self, item: dict, accent: str) -> QWidget:
        row = QFrame(self)
        row.setObjectName("settingsRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 9, 14, 9)
        lay.setSpacing(scale_px_length(10))
        glyph = QLabel(row)
        glyph.setPixmap(pixmap_for(
            glyph, _KIND_GLYPHS.get(str(item.get("kind") or ""), "layers"), 15, _ink(accent)))
        glyph.setFixedWidth(scale_px_length(18))
        glyph.setStyleSheet("background: transparent;")
        lay.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        text = QWidget(row)
        col = QVBoxLayout(text)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(1)
        name = QLabel(str(item.get("name") or ""), text)
        name.setStyleSheet(_BODY_QSS)
        name.setWordWrap(True)
        col.addWidget(name)
        note = str(item.get("resolution") or "").strip()
        if note:
            hint = QLabel(note, text)
            hint.setStyleSheet(ROW_NOTE_QSS)
            hint.setWordWrap(True)
            col.addWidget(hint)
        lay.addWidget(text, 1)
        return row

    def _chip_flow(self, kinds: list[str], accent: str) -> QWidget:
        """The kinds as pills, wrapped by hand: no Qt layout wraps for us."""
        holder = QWidget(self)
        col = QVBoxLayout(holder)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        per_row = 4
        for start in range(0, len(kinds), per_row):
            line = QWidget(holder)
            lay = QHBoxLayout(line)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(6)
            for kind in kinds[start:start + per_row]:
                lay.addWidget(self._chip(kind, accent, line), 0)
            lay.addStretch(1)
            col.addWidget(line)
        return holder

    def _chip(self, kind: str, accent: str, parent: QWidget) -> QWidget:
        """One kind, as a pill. A label, not a disabled button: nothing to press."""
        chip = QFrame(parent)
        chip.setObjectName("kindChip")
        chip.setStyleSheet(_CHIP_QSS)
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(9, 4, 11, 4)
        lay.setSpacing(6)
        glyph = QLabel(chip)
        glyph.setPixmap(pixmap_for(glyph, _KIND_GLYPHS.get(kind, "layers"), 13, _ink(accent)))
        glyph.setStyleSheet("background: transparent;")
        lay.addWidget(glyph, 0)
        text = QLabel(self._kind_label(kind), chip)
        text.setStyleSheet(f"font-size: {FONT_BASE}px; color: palette(text); background: transparent;")
        lay.addWidget(text, 0)
        return chip

    def _kind_label(self, kind: str) -> str:
        """The catalog's kind, in the words a GIS person uses for it."""







        served = self._served_labels("kind_labels")
        if kind in served:
            return served[kind]
        labels = {
            "basemap": self.tr("Basemaps"),
            "imagery": self.tr("Imagery"),
            "elevation": self.tr("Elevation"),
            "boundaries": self.tr("Boundaries"),
            "buildings": self.tr("Buildings"),
            "cadastre": self.tr("Cadastre"),
            "transport": self.tr("Transport"),
            "hydrology": self.tr("Water"),
            "landcover": self.tr("Land cover"),
            "climate": self.tr("Climate"),
            "hazards": self.tr("Hazards"),
            "poi": self.tr("Points of interest"),
            "extract": self.tr("Extracts"),
            "portal": self.tr("Portals"),
            "biodiversity": self.tr("Biodiversity"),
            "population": self.tr("Population"),
        }
        return labels.get(kind, kind.replace("_", " ").capitalize())

    def _information(self, detail: dict) -> QWidget:
        """The facts, in the bordered group every settings list uses."""




        group = SettingGroup(self)
        rows = [
            (self.tr("What it needs"), self._status_line(detail)),
            (self.tr("Category"), str(detail.get("category_label") or "")),
            (self.tr("Coverage"), str(detail.get("coverage") or "")),
            (self.tr("Licence"), str(detail.get("licence") or "")),
            (self.tr("Attribution"), str(detail.get("attribution") or "")),
            (self.tr("Ready sources"), str(detail.get("datasets") or "")),
        ]
        caveat = str(detail.get("caveat") or "").strip()
        if caveat:
            rows.append((self.tr("Good to know"), caveat))
        for label, value in rows:
            if str(value).strip():
                group.add_row(self._info_row(label, str(value)))
        link = self._url or str(detail.get("terms_url") or "")
        if link:
            group.add_row(self._link_row(self.tr("Website"), link))
        terms = str(detail.get("terms_url") or "")
        if terms and terms != link:
            group.add_row(self._link_row(self.tr("Terms"), terms))
        return group

    def _status_line(self, detail: dict) -> str:
        """What using this one costs, in the words that decide the switch."""








        status = str(detail.get("status") or "").strip()






        served = str(detail.get("status_label") or "").strip()
        if served:
            return served[:200]
        served_map = self._served_labels("status_labels")
        if status in served_map:
            return served_map[status]
        return {
            "ready": self.tr("Nothing. Open data, no account and no key."),
            "key": self.tr("Your own account with the provider: it will ask for a key."),
            "community": self.tr("Nothing, but it is run by volunteers. Expect it to be slower or "
                                 "stricter about how much you can ask for."),
        }.get(status, "")

    @staticmethod
    def _served_labels(key: str) -> dict:
        """One `{token: words}` map from /api/plugin/config, already localised."""
        from .shared import served_label_map

        return served_label_map(key)

    def _info_row(self, label: str, value: str) -> QWidget:
        row = QFrame(self)
        row.setObjectName("settingsRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(scale_px_length(16))
        name = ElidedLabel(label, row)
        name.setStyleSheet(_SUBTITLE_QSS)



        name.setFixedWidth(scale_px_length(_LABEL_W))
        name.setToolTip(label)
        lay.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel(value, row)
        text.setStyleSheet(_BODY_QSS)
        text.setWordWrap(True)
        text.setTextFormat(Qt.TextFormat.PlainText)
        lay.addWidget(text, 1)
        return row

    def _link_row(self, label: str, url: str) -> QWidget:
        row = QFrame(self)
        row.setObjectName("settingsRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(scale_px_length(16))
        name = ElidedLabel(label, row)
        name.setStyleSheet(_SUBTITLE_QSS)



        name.setFixedWidth(scale_px_length(_LABEL_W))
        name.setToolTip(label)
        lay.addWidget(name, 0, Qt.AlignmentFlag.AlignTop)
        button = QPushButton(url, row)
        button.setStyleSheet(
            "QPushButton { background: transparent; border: none; padding: 0; text-align: left;"
            f" font-size: {FONT_BASE}px; color: {accent_ink()}; }}"
            "QPushButton:hover { text-decoration: underline; }"
        )
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda _checked=False, target=url: open_external_url(target))
        lay.addWidget(button, 1, Qt.AlignmentFlag.AlignTop)
        return row



    def set_plugin(self, detail: dict) -> None:
        """One QGIS plugin, on the same page shape as a data source."""









        self._clear()
        detail = dict(detail or {})
        self._id = str(detail.get("folder") or "")
        self._url = str(detail.get("homepage") or "")
        col = self._col
        installed = bool(detail.get("installed"))




        control = None
        if not installed:
            control = self._action_button(self.tr("Install"), self.install_requested)
        elif not detail.get("loaded"):
            control = self._action_button(self.tr("Enable"), self.enable_requested)
        col.addWidget(self._header(detail, control))
        col.addSpacing(scale_px_length(20))

        name = self._name = str(detail.get("name") or self._id)
        if not installed:
            self._blocked = self.tr(
                "%s is not installed on this machine. Install it to send this example."
            ) % name
        elif not detail.get("loaded"):
            self._blocked = self.tr(
                "%s is switched off in the QGIS plugin manager. Enable it to send this example."
            ) % name
            self._blocked_action = "enable"

        prompts = [str(p).strip() for p in (detail.get("prompts") or []) if str(p or "").strip()]
        if installed and prompts:
            col.addWidget(self._prompt_row(detail, prompts[:_PROMPTS]))
            col.addSpacing(scale_px_length(20))

        summary = str(detail.get("summary") or detail.get("description") or "").strip()
        if summary:
            col.addWidget(self._summary_card(summary))
            col.addSpacing(scale_px_length(20))

        actions = [str(a) for a in (detail.get("actions") or []) if str(a or "").strip()]
        if actions:
            col.addWidget(self._heading(self.tr("What the agent can trigger")))
            col.addSpacing(scale_px_length(8))
            group = SettingGroup(self)
            accent = accent_of(PLUGINS_KEY)
            for path in actions[:_MAX_ACTIONS]:
                group.add_row(self._highlight_row(
                    {"name": path.replace("/", " > "), "kind": "action"}, accent))
            col.addWidget(group)
            if len(actions) > _MAX_ACTIONS:
                rest = len(actions) - _MAX_ACTIONS
                more = QLabel(self.tr("and 1 more") if rest == 1
                              else self.tr("and %n more", "", rest), self)
                more.setStyleSheet(ROW_NOTE_QSS)
                more.setContentsMargins(4, 6, 0, 0)
                col.addWidget(more)
            col.addSpacing(scale_px_length(20))

        col.addWidget(self._heading(self.tr("Information")))
        col.addSpacing(scale_px_length(8))
        col.addWidget(self._plugin_information(detail, actions))
        col.addStretch(1)

    def _action_button(self, label: str, signal) -> QPushButton:
        button = QPushButton(label, self)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(_INSTALL_QSS)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False: signal.emit(self._id))
        return button

    def _plugin_information(self, detail: dict, actions: list) -> QWidget:
        group = SettingGroup(self)
        if detail.get("installed"):
            if not detail.get("loaded"):
                reach = self.tr("Installed but switched off in the QGIS plugin manager")
            elif actions:
                reach = (self.tr("1 command the agent can run") if len(actions) == 1
                         else self.tr("%n commands the agent can run", "", len(actions)))
            else:
                reach = self.tr("No menu commands. The agent reads it but cannot drive it.")
        else:
            reach = self.tr("Not installed on this machine")
        rows = [
            (self.tr("Status"), reach),
            (self.tr("Author"), str(detail.get("author") or "")),
            (self.tr("Version"), str(detail.get("version") or "")),
            (self.tr("Licence"), str(detail.get("licence") or "")),
            (self.tr("Folder"), str(detail.get("folder") or "")),
        ]
        for label, value in rows:
            if str(value).strip():
                group.add_row(self._info_row(label, str(value)))
        link = str(detail.get("homepage") or "")
        if link.startswith("http"):
            group.add_row(self._link_row(self.tr("Website"), link))
        return group



    def connector_id(self) -> str:
        return self._id


def _ink(accent: str):
    from qgis.PyQt.QtGui import QColor

    return QColor(accent)


def _escape(text: str) -> str:
    """A connector's own words go into a rich-text label; they are not markup."""
    return (str(text or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))




_KIND_GLYPHS = {
    "basemap": "globe",
    "imagery": "satellite",
    "elevation": "terrain",
    "boundaries": "polygon",
    "buildings": "package",
    "cadastre": "hexgrid",
    "transport": "route",
    "hydrology": "measure",
    "landcover": "classify",
    "climate": "chart",
    "hazards": "warning",
    "poi": "pin",
    "extract": "download",
    "portal": "search",
    "biodiversity": "spark",
    "population": "cluster",
    "action": "route",
}

__all__ = ["ConnectorPage", "PromptCard", "example_prompts", "tile_pixmap"]

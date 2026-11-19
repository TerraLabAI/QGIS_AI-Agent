# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later














from __future__ import annotations

from qgis.PyQt.QtCore import QLocale, QSize, Qt, QTimer
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import settings_final as final
from . import tokens
from .cards_click import HIDDEN_FROM_AX, LabelButton, PressSurface, RowButtons
from .external_links import open_external_url
from .font_scale import apply_font_scale_to_tree, scale_px_length
from .icons import icon_for, pixmap_for
from .settings_pages import (
    CONTROL_PX,
    DANGER_GHOST_BTN_QSS,
    EDGE,
    FOCUS_RING,
    GHOST_BTN_QSS,
    INPUT_QSS,
    PAGE_TITLE_QSS,
    PRIMARY_BTN_QSS,
    RADIUS_CARD_PX,
    TEXTAREA_QSS,
    Page,
    SettingGroup,
    SettingRow,
    SkeletonRow,
    Switch,
    nav_qss,
)
from .shared import format_reset_date, released_on
from .styles import DARK_UI
from .widgets import ElidedLabel, placeholder_ink

T = tokens.DARK if DARK_UI else tokens.LIGHT
TAGS = tokens.TAGS_DARK if DARK_UI else tokens.TAGS_LIGHT

_NAV_W, _ITEM_H, _TITLE_PX = 228, 32, 22

_ROW_PAD = (12, 12)

_INFO_PAGES = ("general", "usage", "privacy", "account")



_PEEK_ROOM_PX = 34
_PEEK_PAGES = ("account", "privacy")
_EXTRAS = ("shortcuts", "learn", "contact", "report", "more", "sources")
_NAV = (("general", "memory", "connected"),
        ("usage", "account", "privacy"), _EXTRAS)

_FIELD_W = 248

_INK, _INK2, _LINE = T["text_primary"], T["text_secondary"], T["border_default"]
_TITLE_QSS = f"font-size: 13px; color: {_INK}; background: transparent;"
_VALUE_QSS = f"font-size: 13px; font-weight: 600; color: {_INK}; background: transparent;"
_NOTE_QSS = f"font-size: 11px; color: {_INK2}; background: transparent;"
_LINK_BTN_QSS = (
    f"QPushButton {{ background: transparent; border: none; color: {_INK2}; font-size: 11px;"
    " padding: 2px 0; text-align: left; }"
    f"QPushButton:hover {{ color: {_INK}; text-decoration: underline; }}"
)
_UPGRADE_ROW_QSS = (
    "QAbstractButton#shellUpgrade { background: transparent; border-radius: 8px; }"
    f"QAbstractButton#shellUpgrade:hover {{ background: {T['accent_tint']}; }}"
    f"QAbstractButton#shellUpgrade:pressed {{ background: {T['accent_tint_on']}; }}"
)


_CARD_QSS = (f"QFrame#settingsGroup {{ background: {T['surface_raised']};"
             f" border: 1px solid {EDGE}; border-radius: {RADIUS_CARD_PX}px; }}"
             "QFrame#settingsGroup > QFrame#settingsRow { border: none; background: transparent; }")
_INNER_QSS = ("QFrame#settingsGroup { background: transparent; border: none; border-radius: 0; }"
              "QFrame#settingsGroup > QFrame#settingsRow { border: none; background: transparent; }")
_DIVIDER_QSS = (f"QFrame {{ background: {_LINE}; border: none; margin: 0 {_ROW_PAD[0]}px;"
                " min-height: 1px; max-height: 1px; }")


def _count(value: int) -> str:


    return QLocale(QLocale().name()).toString(int(value))


def _tabular(label: QLabel) -> QLabel:


    tag = getattr(QFont, "Tag", None)
    if tag is not None:
        try:
            font = QFont(label.font())
            font.setFeature(tag("tnum"), 1)
            label.setFont(font)
        except (AttributeError, TypeError):
            pass
    return label


def _label(text: str, qss: str, parent: QWidget, wrap: bool = False) -> QLabel:
    label = QLabel(text, parent)
    label.setStyleSheet(qss)
    label.setWordWrap(wrap)
    return label


def _tag(text: str, kind: str, parent: QWidget) -> QLabel:
    bg, ink = TAGS[kind]
    label = QLabel(text, parent)
    label.setStyleSheet(f"background: {bg}; color: {ink}; border-radius: 6px; padding: 1px 7px;"
                        " font-size: 11px; font-weight: 600;")
    return label


class _Tile(PressSurface):


    def __init__(self, parent=None):
        super().__init__(parent, Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)



    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.setDown(True)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self.setDown(False)
        if released_on(self, event):
            self.click()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self.setDown(False)
        super().leaveEvent(event)


class ShellMixin:




    def _build_shell(self, info: dict) -> None:
        self._usage_mascot = None
        self._right.setObjectName("settingsRight")
        self._right.setStyleSheet(f"QWidget#settingsRight {{ background: {T['surface_sunk']}; }}")
        self._page_rows: dict[str, int] = {}
        self._shell_subscriber = None
        self._moment = "none"
        self._nav_by_code = False
        self._viewed_page = ""

        from .settings_connected import ConnectedAccountsPage

        built = {
            "general": ("lu.sliders-horizontal", self.tr("General"), self._build_general_page()),
            "memory": ("lu.bookmark", self.tr("Memory"), self._build_memory_page()),
            "usage": ("lu.chart-column", self.tr("Usage"), self._build_usage_page()),
            "privacy": ("lu.shield", self.tr("Privacy"), self._build_privacy_page()),
            "account": ("lu.user", self.tr("Account"), self._build_account()),
            "shortcuts": ("lu.keyboard", self.tr("Keyboard shortcuts"), self._build_shortcuts()),
            "learn": ("lu.book-open", self.tr("Learn"), self._build_learn()),
            "more": ("lu.grid-2x2", self.tr("More AI plugins"), self._build_more_page()),
        }
        from ..core import accounts

        if accounts.any_enabled():
            built["connected"] = ("lu.lock", self.tr("Connected accounts"), ConnectedAccountsPage(self))

        built["learn"][2].siblings.hide()
        for block_index, block in enumerate(_NAV):
            if block_index:
                self._add_separator()
            for key in block:
                if key in ("sources", "contact", "report"):
                    glyph, label = {"sources": ("lu.globe", self.tr("Data sources")),
                                    "contact": ("lu.message-circle", self.tr("Contact us")),
                                    "report": ("lu.triangle-alert", self.tr("Report a problem"))}[key]
                    self._add_action(glyph, label, key)
                    self._nav.item(self._nav.count() - 1).setSizeHint(QSize(0, scale_px_length(_ITEM_H)))
                elif key in built:
                    self._add_shell_page(key, *built[key])
        self._account_row = self._page_rows["account"]
        self._nav.currentRowChanged.connect(self._on_shell_nav)

        self._landing_pending = True
        code = str(info.get("error_code") or "").strip().upper()
        if code in ("SIGNED_OUT", "INVALID_KEY"):
            self._land("account")
        else:
            known = self._plan_known_subscriber()
            if known is not None:
                self._land("general" if known else "usage")
            else:

                self._go("usage")
        self.set_account_info(info)
        self._dress()

        self.setFocus()

    @staticmethod
    def _plan_known_subscriber():

        from ..core import plan

        if "memory" not in getattr(plan, "_said", set()):
            return None
        return bool(plan.memory_allowed())

    def _go(self, key: str) -> None:
        row = self._page_rows.get(key)
        if row is None:
            return
        self._nav_by_code = True
        try:
            self._nav.setCurrentRow(row)
        finally:
            self._nav_by_code = False

    def open_on(self, key: str) -> None:

        self._land(key)
        QTimer.singleShot(0, self._focus_first_card)

    def _focus_first_card(self) -> None:
        from .learn_page import LearnCard

        page = self._pages.currentWidget()
        cards = [card for card in page.findChildren(LearnCard) if card.isVisibleTo(page)] if page else []
        if cards:
            cards[0].setFocus(Qt.FocusReason.TabFocusReason)

    def _land(self, key: str) -> None:
        self._landing_pending = False
        self._go(key)

    def _on_shell_nav(self, _row: int) -> None:
        if not self._nav_by_code:
            self._landing_pending = False
        self._sync_upgrade_item()
        key = self._current_key()
        if key and key != self._viewed_page:
            self._viewed_page = key

            QTimer.singleShot(0, self._track_page_viewed)

        QTimer.singleShot(0, self._tab_in_reading_order)
        peek = getattr(self, "_privacy_peek", None)
        if peek is not None:
            from ..core.qt_compat import qt_deleted

            if key == "privacy":

                peek.mascot.set_mood("shy")
                peek._set_sink(0.0)
                QTimer.singleShot(250, lambda: None if qt_deleted(peek._anim) else peek.rest_at(0.22))
            else:
                peek.rest_at(0.0)

    def _track_page_viewed(self) -> None:

        from ..core import telemetry
        from ..core import telemetry_events as ev
        from ..core.qt_compat import qt_deleted

        if qt_deleted(self) or not self._viewed_page or self._viewed_page == getattr(self, "_tracked_page", ""):
            return
        self._tracked_page = self._viewed_page
        telemetry.track(ev.SETTINGS_PAGE_VIEWED, {"page": self._viewed_page})

    def _current_key(self) -> str:
        row = self._nav.currentRow()
        for key, value in self._page_rows.items():
            if value == row:
                return key
        return ""

    def _sync_context_upsell(self) -> None:


        button = getattr(self, "_context_upsell_btn", None)
        from ..core.qt_compat import qt_deleted

        if button is None or qt_deleted(button):
            return
        moment = getattr(self, "_moment", "none")
        if moment == "payment_failed":
            from ..core.served_state import get_payment_failed, offer_text

            declined = bool((get_payment_failed() or {}).get("declined"))
            button.setText(offer_text("declined.button", self.tr("Try another card")) if declined
                           else offer_text("plan.failed.button", self.tr("Update card")))
        else:
            button.setText(self._offer_words(moment))

    def _sync_upgrade_item(self) -> None:

        self._sync_context_upsell()
        item = getattr(self, "_upgrade_item", None)
        if item is None:
            return
        moment = getattr(self, "_moment", "none")
        offers = self._shell_subscriber is False and moment not in ("payment_failed", "trial_running", "pro")
        if offers:

            trial = moment == "trial" or (moment == "out" and getattr(self, "trial_offered", False))
            words = self.tr("Try Pro free") if trial else self._offer_words(moment)
            self._upgrade_words.setText(words)
            item.setAccessibleName(words)
        item.setVisible(offers and self._current_key() not in ("usage", "general", "account"))

    def _build_shell_sidebar(self) -> QFrame:
        side = QFrame(self)
        side.setObjectName("settingsSidebar")
        side.setStyleSheet(
            f"QFrame#settingsSidebar {{ background: {T['surface_page']}; border: none;"
            f" border-right: 1px solid {_LINE}; }}"
            "QFrame#settingsSidebar QLabel { background: transparent; border: none; }")
        side.setFixedWidth(scale_px_length(_NAV_W))
        col = QVBoxLayout(side)
        col.setContentsMargins(10, 14, 10, 12)
        col.setSpacing(10)
        self._nav = QListWidget(side)

        self._nav_buttons = RowButtons(self._nav, side, self._nav.setCurrentIndex)
        self._nav.setStyleSheet(
            nav_qss(HIDDEN_FROM_AX).replace("padding: 4px 6px;", "padding: 0;"))
        self._nav.setIconSize(QSize(16, 16))
        self._nav.setFrameShape(QFrame.Shape.NoFrame)
        self._nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._nav.setFocusPolicy(Qt.FocusPolicy.TabFocus)


        self._nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._nav.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._nav.setCursor(Qt.CursorShape.PointingHandCursor)
        self._nav.currentRowChanged.connect(self._on_nav_changed)
        self._page_row = 0
        col.addWidget(self._nav, 1)


        self._upgrade_item = _Tile(side)
        self._upgrade_item.setObjectName("shellUpgrade")
        self._upgrade_item.setStyleSheet(_UPGRADE_ROW_QSS)
        self._upgrade_item.setAccessibleName(self.tr("Upgrade to Pro"))
        self._upgrade_item.clicked.connect(lambda _checked=False: self._on_upgrade("settings_nav"))
        line = QHBoxLayout(self._upgrade_item)
        line.setContentsMargins(8, 7, 8, 7)
        line.setSpacing(8)
        glyph = QLabel(self._upgrade_item)
        glyph.setPixmap(pixmap_for(glyph, "lu.zap", 16, QColor(T["accent_text"])))
        line.addWidget(glyph)
        words = self._upgrade_words = QLabel(self.tr("Upgrade to Pro"), self._upgrade_item)
        words.setWordWrap(True)
        words.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {T['accent_text']};")
        line.addWidget(words, 1)
        for child in (glyph, words):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        keep = self._upgrade_item.sizePolicy()
        keep.setRetainSizeWhenHidden(True)
        self._upgrade_item.setSizePolicy(keep)
        self._upgrade_item.hide()
        col.addWidget(self._upgrade_item)
        from ..core.served_state import get_privacy_url, get_terms_url


        legal = QWidget(side)
        legal_line = QHBoxLayout(legal)
        legal_line.setContentsMargins(8, 0, 8, 0)
        legal_line.setSpacing(0)
        small = f"font-size: 11px; color: {_INK2}; background: transparent;"
        for index, (words, url) in enumerate(((self.tr("Terms"), get_terms_url()),
                                              (self.tr("Privacy"), get_privacy_url()))):
            if index:
                legal_line.addWidget(_label("\u00a0\u00b7\u00a0", small, legal))
            link = LabelButton(_label(words, small, legal), legal, Qt.FocusPolicy.TabFocus)
            link.setAccessibleName(words)
            link.clicked.connect(lambda _checked=False, url=url: open_external_url(str(url), parent=self))
            legal_line.addWidget(link)
        legal_line.addStretch(1)
        col.addWidget(legal)
        return side

    def _add_shell_page(self, key: str, glyph: str, label: str, page: QWidget) -> None:
        self._page_rows[key] = self._nav.count()
        page.setObjectName(f"settingsPage_{key}")
        self._add_page(glyph, label, page)
        self._nav.item(self._nav.count() - 1).setSizeHint(QSize(0, scale_px_length(_ITEM_H)))

    def _open_page(self, key: str) -> None:
        row = self._page_rows.get(key)
        if row is not None:
            self._nav.setCurrentRow(row)



    def _dress(self) -> None:


        px, py = _ROW_PAD
        title_qss = PAGE_TITLE_QSS.replace("font-size: 20px", f"font-size: {_TITLE_PX}px")
        for page in self._pages.findChildren(Page):
            page.title_label.setStyleSheet(title_qss)
            page.layout().itemAt(0).layout().setContentsMargins(28, 24, 28, 12)
            peeks = page.objectName() in (f"settingsPage_{key}" for key in _PEEK_PAGES)
            page.body.setContentsMargins(28, scale_px_length(_PEEK_ROOM_PX) if peeks else 0, 28, 24)
            page.body.setSpacing(12)
        for group in self._pages.findChildren(QFrame, "settingsGroup"):
            if group.property("shellOwn"):
                continue
            nested = self._inside_group(group)
            group.setStyleSheet(_INNER_QSS if nested else _CARD_QSS)
            col = group.layout()
            if not nested:
                col.setContentsMargins(4, 4, 4, 4)
                final.lift(group)
            for i in range(col.count()):
                widget = col.itemAt(i).widget()

                if widget is not None and type(widget) is QFrame and not widget.objectName():
                    widget.setStyleSheet(_DIVIDER_QSS)
        for row in self._pages.findChildren(QFrame, "settingsRow"):
            if row.layout() is not None and not row.property("shellFixedPad"):
                row.layout().setContentsMargins(px, py, px, py)
        for title in self._pages.findChildren(QLabel, "settingsGroupTitle"):
            title.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {_INK}; background: transparent;")
            title.setContentsMargins(4, 12, 0, 0)
        for button in self.findChildren(QPushButton):
            if button.property("shellFocus"):
                continue
            button.setProperty("shellFocus", True)

            button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            qss = button.styleSheet()
            if qss and final.UPGRADE_FILL in qss:

                button.setStyleSheet(qss + f"QPushButton:focus {{ border: 2px solid {final.FOCUS_RING_ON_FILL};"
                                           " padding: 6px 15px; }")
            elif qss and ":focus" not in qss:
                button.setStyleSheet(qss.replace("border: none;", "border: 1px solid transparent;", 1)
                                     + f"QPushButton:focus {{ border: 1px solid {FOCUS_RING}; }}")
            if qss in (PRIMARY_BTN_QSS, GHOST_BTN_QSS, DANGER_GHOST_BTN_QSS):
                button.setFixedHeight(scale_px_length(CONTROL_PX))

        for field in self._pages.findChildren(QComboBox) + self._pages.findChildren(QLineEdit):
            field.setFixedHeight(scale_px_length(CONTROL_PX))
        self._row_infos()

        apply_font_scale_to_tree(self)
        self._tab_in_reading_order()

    def _tab_in_reading_order(self) -> None:


        from ..core.qt_compat import qt_deleted

        if qt_deleted(self):
            return
        tab = Qt.FocusPolicy.TabFocus
        for page in self._pages.findChildren(Page):
            stops = []
            for w in page.findChildren(QWidget):
                policy = w.focusPolicy()
                if (policy == tab or policy == Qt.FocusPolicy.StrongFocus or policy == Qt.FocusPolicy.WheelFocus) \
                        and w.isVisibleTo(page) and w.isEnabled() and w.focusProxy() is None:
                    centre = w.mapTo(page, w.rect().center())
                    stops.append((centre.y() // scale_px_length(14), centre.x(), w))
            stops.sort(key=lambda stop: (stop[0], stop[1]))
            for before, after in zip(stops, stops[1:]):
                QWidget.setTabOrder(before[2], after[2])

    def _row_infos(self) -> None:




        cards_with_info = set()
        for key in _INFO_PAGES:
            row_index = self._page_rows.get(key)
            if row_index is None:
                continue
            data = self._nav.item(row_index).data(Qt.ItemDataRole.UserRole)
            page = self._pages.widget(data) if isinstance(data, int) else None
            if page is None:
                continue
            for row in page.findChildren(SettingRow):
                if row.property("keepNote") or hasattr(row, "_final_info"):
                    continue
                words = row.layout().itemAt(0).layout()
                if words is None:
                    continue
                card = row.parentWidget()
                while card is not None and card.objectName() != "settingsGroup":
                    card = card.parentWidget()
                if card is not None and id(card) in cards_with_info:
                    row._final_info = False
                    row._final_watch = final.NoteToInfo(row.note_label, None, row.title_label)
                    continue
                if card is not None and row.note_label.text():
                    cards_with_info.add(id(card))
                info = final.InfoButton(row, row.note_label.text(), row.title_label.text())
                words.removeWidget(row.title_label)
                line = QHBoxLayout()
                line.setContentsMargins(0, 0, 0, 0)
                line.setSpacing(6)
                line.addWidget(row.title_label)
                line.addWidget(info, 0, Qt.AlignmentFlag.AlignVCenter)
                line.addStretch(1)
                words.insertLayout(0, line)
                row._final_info = info
                row._final_watch = final.NoteToInfo(row.note_label, info)

    def _inside_group(self, widget: QWidget) -> bool:
        parent = widget.parentWidget()
        while parent is not None and parent is not self:
            if parent.objectName() == "settingsGroup":
                return True
            parent = parent.parentWidget()
        return False



    def _btn(self, text: str, qss: str, slot, parent: QWidget) -> QPushButton:
        button = QPushButton(text, parent)
        button.setStyleSheet(qss)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False: slot())
        return button

    def _avatar(self, parent: QWidget, email: str, d: int, account: dict | None = None) -> QLabel:
        d = scale_px_length(d)
        avatar = QLabel(email[:1].upper() if email and email != "-" else "?", parent)
        avatar.setFixedSize(d, d)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"background: {T['accent_tint_on']}; color: {T['accent_text']};"
                             f" border-radius: {d // 2}px; font-size: {max(13, d * 2 // 5)}px; font-weight: 600;")
        self._avatar_label = avatar
        if account:
            self._show_account_picture(account.get("avatar_url"), d)
        return avatar

    def _row_frame(self, parent: QWidget, vertical: bool = True):
        row = QFrame(parent)
        row.setObjectName("settingsRow")
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        lay = QVBoxLayout(row) if vertical else QHBoxLayout(row)
        lay.setSpacing(10)
        return row, lay

    def _plan_note(self, plan) -> str:
        if plan.is_subscriber:
            return self.tr("Commercial use included")
        return self.tr("Personal, non-commercial use")

    def _task_definition(self) -> str:
        return self.tr("One task is one message you send, however many steps the AI agent "
                       "takes. Tasks that fail on our side are given back.")

    def _plan_moment(self, plan, left, limit) -> str:

        from ..core.plan_moment import moment
        from ..core.served_state import get_payment_failed

        return moment(getattr(self, "offer_kind", "none"), plan.is_subscriber, left, limit,
                      payment_failed=bool(get_payment_failed()), trial_ends_on=plan.trial_ends_on)

    def _offer_words(self, moment: str) -> str:

        from ..core.served_state import offer_text

        if moment == "trial" or (moment == "out" and getattr(self, "trial_offered", False)):
            days = getattr(self, "trial_days", 7)
            if days == 1:
                return offer_text("plan.trial.button.one", self.tr("Try Pro free for 1 day"))
            return offer_text("plan.trial.button", self.tr("Try Pro free for {n} days"),
                              n=_count(days), days=_count(days))
        if moment == "return":
            return offer_text("plan.return.button", self.tr("Come back to Pro"))
        return offer_text("plan.upgrade.button", self.tr("Get Pro"))

    def _price_note(self, moment: str) -> str:

        from ..core.served_state import get_pro_price, get_pro_price_excludes_tax, offer_text

        price = get_pro_price()
        excl = bool(price) and get_pro_price_excludes_tax()
        trial = moment == "trial" or (moment == "out" and getattr(self, "trial_offered", False))
        if trial:
            if not price:
                return self.tr("Cancel anytime before it ends.")
            if excl:
                return offer_text("plan.trial.info.excl_vat",
                                  self.tr("Then {price}/month excl. VAT. Cancel anytime."), price=price)
            return offer_text("plan.trial.info", self.tr("Then {price}/month. Cancel anytime."), price=price)
        if moment == "return" and price:
            if excl:
                return offer_text("plan.return.info.excl_vat",
                                  self.tr("{price}/month excl. VAT. Cancel anytime."), price=price)
            return offer_text("plan.return.info", self.tr("{price}/month. Cancel anytime."), price=price)
        return ""

    def _plan_action(self, plan, parent: QWidget, surface: str, moment: str = "none") -> QPushButton:
        if moment == "payment_failed":
            from ..core.served_state import get_payment_failed, offer_text

            declined = bool((get_payment_failed() or {}).get("declined"))
            words = (offer_text("declined.button", self.tr("Try another card")) if declined
                     else offer_text("plan.failed.button", self.tr("Update card")))

            return self._btn(words, final.UPGRADE_QSS, lambda: self._on_upgrade(surface), parent)
        if plan.is_subscriber:
            from ..core.served_state import offer_text

            button = self._btn(offer_text("plan.manage.button", self.tr("Manage plan")), GHOST_BTN_QSS,
                               lambda: self._open_dashboard(surface), parent)
            button.setToolTip(self.tr("Your plan, payment method and invoices, on terra-lab.ai."))
            return button
        return self._btn(self._offer_words(moment), final.UPGRADE_QSS, lambda: self._on_upgrade(surface), parent)



    def _build_usage_page(self) -> Page:
        page = Page(self.tr("Usage"), self.tr("Your plan and the tasks you used this month."), self)
        self._usage_page = page
        self._usage_box = QWidget(page)
        self._usage_col = QVBoxLayout(self._usage_box)
        self._usage_col.setContentsMargins(0, 0, 0, 0)
        self._usage_col.setSpacing(12)
        page.add(self._usage_box)
        return page

    def _clear_usage(self) -> None:
        old = getattr(self, "_usage_mascot", None)
        if old is not None:
            old.mascot.hide()
            old.mascot.deleteLater()
            old.deleteLater()
            self._usage_mascot = None
        self._usage_col.setContentsMargins(0, 0, 0, 0)
        while self._usage_col.count():
            item = self._usage_col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _fill_usage(self, plan, plan_name: str, state: str = "loaded") -> None:
        self._clear_usage()
        box = self._usage_box
        if plan is None:
            if state == "signed_out":
                self._usage_col.addWidget(self._signed_out_row(box))
            else:
                group = SettingGroup(box)
                if state == "loading":

                    for spoken in (self.tr("Loading your plan..."), self.tr("Loading your tasks...")):
                        group.add_row(SkeletonRow(group, spoken))
                else:
                    retry = self._btn(self.tr("Retry"), GHOST_BTN_QSS, self._on_retry, group)
                    group.add_row(SettingRow(self.tr("Could not load your usage"),
                                             self.tr("Your tasks are counted on our side; nothing is lost."),
                                             retry, group))
                self._usage_col.addWidget(group)
            self._shell_subscriber = None
            self._moment = "none"
            self._sync_upgrade_item()
            return
        subscriber = plan.is_subscriber
        upgraded = self._shell_subscriber is False and subscriber
        self._shell_subscriber = subscriber
        if self._landing_pending:
            self._land("general" if subscriber else "usage")
        used = max(0, int(plan.used)) if plan.used is not None else None
        limit = max(int(plan.limit), 1) if plan.limit is not None else None
        self._usage_card_for(plan, plan_name, used, limit, format_reset_date(plan.reset_iso or ""))
        if upgraded:
            self._celebrate()
        self._sync_upgrade_item()

    def _usage_card_for(self, plan, plan_name, used, limit, reset) -> None:

        box = self._usage_box
        group = SettingGroup(box)
        left = max(0, limit - used) if used is not None and limit is not None else None
        moment = self._moment = self._plan_moment(plan, left, limit)
        tint = {"payment_failed": "danger", "out": "alert"}.get(moment)
        if tint:

            edge = QColor(T[tint])
            edge.setAlphaF(0.22 if DARK_UI else 0.18)
            group.setProperty("shellOwn", True)
            group.setStyleSheet(
                f"QFrame#settingsGroup {{ background: {T[tint + '_tint']}; border: 1px solid"
                f" rgba({edge.red()}, {edge.green()}, {edge.blue()}, {edge.alphaF():.2f});"
                f" border-radius: {RADIUS_CARD_PX}px; }}"
                "QFrame#settingsGroup > QFrame#settingsRow { border: none; background: transparent; }")
            group.layout().setContentsMargins(4, 4, 4, 4)
            final.lift(group)
        row, col = self._row_frame(group)
        row.setProperty("shellFixedPad", True)
        col.setContentsMargins(16, 14, 16, 16)
        col.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(6)
        head.addWidget(_tag(plan_name, "success" if plan.is_subscriber else "neutral", row), 0,
                       Qt.AlignmentFlag.AlignVCenter)

        about = [self._plan_note(plan) + ".", self._task_definition(), self._price_note(moment)]
        head.addWidget(final.InfoButton(row, " ".join(part for part in about if part), plan_name), 0,
                       Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        action = self._plan_action(plan, row, "usage", moment)
        head.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(head)
        said = self._moment_lines(plan, moment, left, limit, reset)
        if said is not None:
            lines = QVBoxLayout()
            lines.setSpacing(2)
            lines.addWidget(_tabular(_label(said[0], f"font-size: 22px; font-weight: 600; color: {_INK};"
                                                     " background: transparent; letter-spacing: -0.3px;",
                                            row, wrap=True)))
            if said[1]:
                lines.addWidget(_tabular(_label(said[1], f"font-size: 13px; color: {_INK2};"
                                                         " background: transparent;", row, wrap=True)))
            col.addLayout(lines)
        bar = None
        if left is not None:
            if said is None:
                figure = QHBoxLayout()
                figure.setSpacing(8)
                figure.addWidget(_tabular(_label(
                    self.tr("{n} tasks left").format(n=_count(left)) if left != 1
                    else self.tr("1 task left"),
                    f"font-size: 32px; font-weight: 600; color: {_INK}; background: transparent;"
                    " letter-spacing: -0.5px;", row)), 0, Qt.AlignmentFlag.AlignBottom)
                figure.addWidget(_tabular(_label(
                    self.tr("of {limit} this month").format(limit=_count(limit)),
                    f"font-size: 13px; color: {_INK2}; background: transparent;"
                    " padding-bottom: 6px;", row)), 0, Qt.AlignmentFlag.AlignBottom)
                figure.addStretch(1)
                col.addLayout(figure)
            meter = QVBoxLayout()
            meter.setSpacing(6)
            title = QHBoxLayout()
            title.setSpacing(6)
            title.addWidget(_label(self.tr("Monthly tasks"), f"font-size: 13px; font-weight: 500; color: {_INK};"
                                                             " background: transparent;", row))
            title.addStretch(1)
            meter.addLayout(title)
            small = QHBoxLayout()
            days = final.days_until(plan.reset_iso or "")
            if days is None or moment == "trial_running":
                when = ""
            elif final.reset_is_today(plan.reset_iso or ""):
                when = self.tr("Resets today")
            elif days <= 1:
                when = self.tr("Resets tomorrow")
            else:
                when = self.tr("Resets in {n} days").format(n=_count(days))
            reset_label = _tabular(_label(when, f"font-size: 11px; color: {T['alert'] if left == 0 else _INK2};"
                                                " background: transparent;", row))
            reset_label.setToolTip(reset)
            small.addWidget(reset_label)
            small.addStretch(1)

            share = round(100 * left / limit)
            share = min(max(share, 1), 99) if 0 < left < limit else share
            small.addWidget(_tabular(_label(self.tr("{p}% left").format(p=_count(share)),
                                            f"font-size: 11px; color: {_INK2}; background: transparent;", row)))
            meter.addLayout(small)
            bar = final.UsageBar(row, left, limit)
            meter.addWidget(bar)
            col.addLayout(meter)
        group.add_row(row)
        student = None if moment in ("payment_failed", "trial_running") else self._student_row(group)
        if student is not None:
            group.add_row(student)
        self._usage_col.addWidget(group)
        foot = QWidget(box)
        line = QHBoxLayout(foot)
        line.setContentsMargins(_ROW_PAD[0] + 4, 2, 0, 0)
        details = self._btn(self.tr("Every task, day by day, on terra-lab.ai"), _LINK_BTN_QSS,
                            self._open_usage_details, foot)
        details.setIcon(icon_for(self, "lu.external-link", 11, QColor(_INK2)))
        details.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        line.addWidget(details)
        line.addStretch(1)
        self._usage_col.addWidget(foot)
        self._usage_card = group
        self._usage_col.setContentsMargins(0, scale_px_length(22), 0, 0)
        self._usage_mascot = final.Peek(box, group, self._store, 36, at=0.76)
        self._usage_mascot.mascot.set_mood("sleep" if moment == "out" else final.quota_mood(left, limit))
        self._usage_moments(plan, left, limit, action, bar)
        self._last_usage = (plan.is_subscriber, left)

    def _moment_lines(self, plan, moment, left, limit, reset) -> tuple[str, str] | None:

        from ..core.served_state import get_payment_failed, offer_text

        if moment == "payment_failed":
            info = get_payment_failed() or {}
            if info.get("declined"):
                return (offer_text("declined.title", self.tr("Card declined")),
                        offer_text("declined.note", self.tr("Your trial did not start. You were not charged.")))
            amount = info.get("amount") or ""
            note = offer_text("payment.note", self.tr("Pro is paused until payment goes through."))
            return (offer_text("plan.failed.title", self.tr("Payment failed")),
                    self.tr("{amount} due.").format(amount=amount) + " " + note if amount else note)
        if moment == "trial_running":
            days = final.days_until(plan.trial_ends_on or "")
            date = format_reset_date(plan.trial_ends_on or "")
            if days is None:
                return None
            title = (offer_text("plan.trialing.title.one", self.tr("Pro trial: 1 day left")) if days <= 1
                     else offer_text("plan.trialing.title.many", self.tr("Pro trial: {n} days left"), n=_count(days)))
            return title, offer_text("plan.trialing.ends", self.tr("Ends on {date}"), date=date) if date else ""
        if moment == "out" and left is not None:
            if left == 0:
                title = (offer_text("plan.out.title", self.tr("Monthly limit reached, resets {date}"), date=reset)
                         if reset else self.tr("You're out of tasks this month"))
            elif left == 1:
                title = offer_text("plan.low.title.one", self.tr("1 task left this month"))
            else:
                title = offer_text("plan.low.title.many", self.tr("{n} tasks left this month"),
                                   n=_count(left))
            return title, self.tr("{used} of {limit} used").format(used=_count(limit - left),
                                                                    limit=_count(limit))
        return None

    def _usage_moments(self, plan, left, limit, action, bar) -> None:



        from ..core.qt_compat import qt_deleted
        from .memory_mascot import HoverWatch

        mark = self._usage_mascot.mascot

        if not plan.is_subscriber and self._moment != "payment_failed":
            def lean():
                mark.hover("hope", action)
                mark.nudge(260)

            self._usage_lean = HoverWatch(action, lean, lambda: mark.hover(None))
        before = getattr(self, "_last_usage", None)
        if before is not None and before[0] == plan.is_subscriber and None not in (before[1], left):
            if left < before[1] and bar is not None:
                mark.look_at(bar)
                mark.react("nod", 0.5)
                QTimer.singleShot(1200, lambda: None if qt_deleted(mark) else mark.look_at(None))
            elif left > before[1]:
                mark.burst("happy", 1400)
                mark.react("stretch", 0.8)
        elif before is None and left is not None and left == limit and not getattr(self, "_fresh_seen", False):
            self._fresh_seen = True
            mark.burst("happy", 1600)
            mark.react("stretch", 0.9)

    def _celebrate(self) -> None:

        if self._usage_mascot is None:
            return
        mark = self._usage_mascot.mascot
        mark.stir()
        mark.burst("happy", 2200)
        mark.react("jump", 1.0)

    def _open_usage_details(self) -> None:
        from ..core.served_state import build_utm_url

        self.dashboard_opened.emit("usage_details")
        open_external_url(build_utm_url("/dashboard", "settings_usage"), parent=self)



    def _siblings(self) -> tuple:

        return (
            ("ai-segmentation", "AI Segmentation", "lu.scan",
             self.tr("Detect and outline objects on imagery: buildings, trees, water or fields "
                     "become polygons you can edit.")),
            ("ai-edit", "AI Edit", "lu.wand-sparkles",
             self.tr("Edit your imagery with one sentence, the way you would ask a colleague.")),
        )

    def _sibling_mark(self, parent: QWidget, product_id: str, glyph: str, px: int) -> QLabel:
        from ..core.qgis_plugins import plugin_logo
        from ..core.siblings import sibling_info
        from .logo_tile import logo_pixmap

        mark = QLabel(parent)
        side = scale_px_length(px)
        mark.setFixedSize(side, side)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        path = plugin_logo(str((sibling_info(product_id) or {}).get("folder") or ""))
        picture = logo_pixmap(path, side, mark.devicePixelRatioF()) if path else None
        if picture is not None:
            mark.setPixmap(picture)
        else:
            mark.setPixmap(pixmap_for(mark, glyph, max(12, px // 2), QColor(_INK)))
            mark.setStyleSheet(f"background: {T['surface_field']}; border-radius: {max(4, side // 4)}px;")
        return mark

    def _sibling_button(self, parent: QWidget, product_id: str, name: str):
        from ..core.siblings import is_sibling_installed

        installed = is_sibling_installed(product_id)
        button = self._btn(self.tr("Open") if installed else self.tr("Install"),
                           GHOST_BTN_QSS if installed else PRIMARY_BTN_QSS,
                           lambda pid=product_id: self._on_sibling(pid), parent)
        button.setAccessibleName((self.tr("Open {plugin}") if installed
                                  else self.tr("Install {plugin}")).format(plugin=name))
        button.setToolTip(self.tr("Show its panel.") if installed
                          else self.tr("Opens the QGIS plugin manager on this plugin. Free to install."))
        return button

    def _on_sibling(self, product_id: str) -> None:
        from .cross_plugin_discovery import open_sibling

        self._on_sibling_opened(product_id, open_sibling(product_id))

    def _build_more_page(self) -> Page:

        page = Page(self.tr("More AI plugins"),
                    self.tr("Two more AI plugins for QGIS, from the makers of AI Agent."), self)
        intro = _label(self.tr("One TerraLab account signs you in to all three plugins."),
                       f"font-size: 12px; color: {_INK2}; background: transparent;", page, wrap=True)
        intro.setContentsMargins(0, 0, 0, 4)
        page.add(intro)
        cards = QWidget(page)
        cards.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        row = QHBoxLayout(cards)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        for product_id, name, glyph, line in self._siblings():
            row.addWidget(self._sibling_card(cards, product_id, name, glyph, line), 1)
        page.add(cards)
        return page

    def _sibling_card(self, parent: QWidget, product_id: str, name: str, glyph: str, line: str) -> QWidget:
        from ..core.siblings import sibling_info
        from .learn_page import Thumbnail, ThumbnailLoader
        from .thumbnail_cache import cached_thumbnail, is_picture_url_usable

        group = SettingGroup(parent)

        group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        card, col = self._row_frame(group)
        card.setProperty("shellFixedPad", True)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        col.setContentsMargins(10, 10, 10, 14)
        col.setSpacing(0)
        thumb = Thumbnail(glyph, T["border_strong"], card)
        thumb.setFixedHeight(scale_px_length(120))


        thumb.hide()
        gap = QWidget(card)
        gap.setFixedHeight(12)
        gap.hide()

        def show_picture(image, thumb=thumb, gap=gap):
            thumb.set_image(image)
            thumb.show()
            gap.show()

        url = str((sibling_info(product_id) or {}).get("thumbnail_url") or "")
        if is_picture_url_usable(url):
            cached = cached_thumbnail(url)
            if cached is not None:
                show_picture(cached)
            else:
                loader = ThumbnailLoader(card)
                loader.loaded.connect(show_picture)
                loader.fetch(url)
        col.addWidget(thumb)
        col.addWidget(gap)
        button = self._sibling_button(card, product_id, name)
        title = _label(name, f"font-size: 14px; font-weight: 600; color: {_INK}; background: transparent;", card)
        words = _label(line, f"font-size: 12px; color: {_INK2}; background: transparent;", card, wrap=True)
        head = QHBoxLayout()
        head.setContentsMargins(4, 0, 4, 0)
        head.setSpacing(8)
        head.addWidget(self._sibling_mark(card, product_id, glyph, 22), 0, Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(title, 1, Qt.AlignmentFlag.AlignVCenter)
        col.addLayout(head)
        col.addSpacing(6)
        words.setContentsMargins(4, 0, 4, 0)
        col.addWidget(words)
        col.addStretch(1)
        col.addSpacing(12)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        foot = QHBoxLayout()
        foot.setContentsMargins(4, 0, 4, 0)
        foot.addWidget(button)
        col.addLayout(foot)
        group.add_row(card)
        return group



    def _shell_account(self, account: dict, email: str, plan_name: str, plan=None) -> None:


        box = self._account_box
        who = SettingGroup(box)
        row, line = self._row_frame(who, vertical=False)
        line.setSpacing(12)
        line.addWidget(self._avatar(row, email, 36, account), 0, Qt.AlignmentFlag.AlignVCenter)
        mail = ElidedLabel(email, row, Qt.TextElideMode.ElideMiddle)
        mail.setStyleSheet(f"font-size: 13px; color: {_INK}; background: transparent;")
        line.addWidget(mail, 1)
        line.addWidget(self._btn(self.tr("Sign out"), GHOST_BTN_QSS, self._on_sign_out, row), 0,
                       Qt.AlignmentFlag.AlignVCenter)
        who.add_row(row)
        if plan is None:
            go = self._btn(self.tr("See usage"), GHOST_BTN_QSS, lambda: self._open_page("usage"), who)
            note = plan_name
        else:
            used = max(0, int(plan.used)) if plan.used is not None else None
            limit = max(int(plan.limit), 1) if plan.limit is not None else None
            left = max(0, limit - used) if used is not None and limit is not None else None
            moment = self._plan_moment(plan, left, limit)
            go = self._plan_action(plan, who, "account", moment)
            price = self._price_note(moment)
            if price:

                pair = QWidget(who)
                both = QHBoxLayout(pair)
                both.setContentsMargins(0, 0, 0, 0)
                both.setSpacing(8)
                go.setParent(pair)
                both.addWidget(final.InfoButton(pair, price, go.text()), 0, Qt.AlignmentFlag.AlignVCenter)
                both.addWidget(go, 0, Qt.AlignmentFlag.AlignVCenter)
                go = pair
            note = self._plan_line(plan, plan_name, moment)
        plan_row = SettingRow(self.tr("Plan"), note, go, who)
        plan_row.setProperty("keepNote", True)
        who.add_row(plan_row)
        self._account_col.addWidget(who)
        old = getattr(self, "_account_peek", None)
        if old is not None:
            old.mascot.hide()
            old.mascot.deleteLater()
            old.deleteLater()


        self._account_peek = final.Peek(box.parentWidget(), who, self._store, 30, at=0.14, perched=True)

    def _plan_line(self, plan, plan_name: str, moment: str) -> str:

        from ..core.served_state import offer_text

        if moment == "payment_failed":
            return offer_text("plan.failed.title", self.tr("Payment failed"))
        if moment == "trial_running":
            date = format_reset_date(plan.trial_ends_on or "")
            return self.tr("Pro trial, ends {date}").format(date=date) if date else self.tr("Pro trial")


        ends = format_reset_date(plan.ends_on or "") if moment == "pro" else ""
        return f"{plan_name} · " + self.tr("Ends on {date}").format(date=ends) if ends else plan_name

    def _shell_after_account(self) -> None:

        state = self._account_state
        if state != "loaded":
            self._fill_usage(None, "", state)
        signed_out = state == "signed_out"
        danger = getattr(self, "_delete_account_row", None)
        if danger is not None:
            danger.parentWidget().setVisible(not signed_out)
        for row in getattr(self, "_from_account_rows", ()):
            row.set_note(self.tr("Saved on this computer.") if signed_out
                         else self.tr("From your TerraLab account."))

    def _signed_out_row(self, parent: QWidget) -> QWidget:

        group = SettingGroup(parent)
        group.add_row(SettingRow(self.tr("Sign in to see your plan and tasks"),
                                 self.tr("Everything else here works without an account."),
                                 self._btn(self.tr("Sign in"), PRIMARY_BTN_QSS, self._on_sign_in, group), group))
        return group



    def _build_general_page(self) -> Page:

        page = Page(self.tr("General"),
                    self.tr("How AI Agent looks, what it knows about you, and how it calls you back."), self)
        self._answer_rows = self._build_answers(page)
        self._sync_language_combo()

        self._language_combo.setMinimumWidth(scale_px_length(150))
        self._language_combo.setMaximumWidth(scale_px_length(_FIELD_W))
        first = SettingGroup(page)
        first.add_row(self._answer_rows["language"])
        first.add_row(self._answer_rows["style"])
        self._suggest_switch = Switch(first, self._store.suggestions_enabled)
        self._suggest_switch.toggled.connect(self._on_suggest_switch)
        first.add_row(SettingRow(self.tr("Typing suggestions"),
                                 self.tr("Grey text in the message box. Tab takes it."),
                                 self._suggest_switch, first))
        page.add(first)

        self._add_notifications(page)
        self._add_profile(page)
        reset = SettingGroup(page)
        reset.add_row(SettingRow(
            self.tr("Reset settings"),
            self.tr("Language, response style, suggestions and notifications go back to their defaults."),
            self._btn(self.tr("Reset"), GHOST_BTN_QSS, self._on_reset_preferences, reset), reset))
        page.add(reset)
        return page

    def _on_reset_preferences(self) -> None:
        self._store.reset_preferences()
        self._sync_language_combo()
        self._style_segments.set_value(self._store.reply_style)
        self._suggest_switch.blockSignals(True)
        self._suggest_switch.setChecked(self._store.suggestions_enabled)
        self._suggest_switch.blockSignals(False)

        self._notify_switch.setChecked(self._store.notify_mode != "off")
        for picker, key in zip(self._sound_rows, ("notify_sound_waiting", "notify_sound_finished")):
            combo = picker.findChild(QComboBox)
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, combo.findData(getattr(self._store, key))))
            combo.blockSignals(False)
        self._sync_sounds_summary()
        self._sync_notify_rows()
        self._show_saved()
        self._emit_profile()

    def _build_privacy_page(self) -> Page:
        page = Page(self.tr("Privacy"), self.tr("What leaves this computer, and for how long."), self)
        group = self._build_privacy_group(page)
        page.add(group)
        self._privacy_peek = final.Peek(group.parentWidget(), group, self._store, 30, at=0.5)
        return page

    def _build_privacy_group(self, page: Page) -> SettingGroup:


        from .settings_retention import RetentionRow

        group = SettingGroup(page)
        self._improve_switch = Switch(group, self._help_improve_on())
        self._improve_switch.toggled.connect(self._on_help_improve_toggled)
        self._improve_row = SettingRow(self.tr("Help improve AI Agent"),
                                       self.tr("Your usage and chats help us fix bugs. Turn it off anytime."),
                                       self._improve_switch, group)
        self._improve_row.setToolTip(self._improve_tip(False))
        group.add_row(self._improve_row)
        self._retention = RetentionRow(self, group)
        group.add_row(self._retention.row)
        divider = group.layout().itemAt(group.layout().count() - 2)
        self._retention.divider = divider.widget() if divider is not None else None
        self._retention.load()
        return group

    def _add_notifications(self, page: Page) -> None:

        page.add_group_title(self.tr("Notifications"))
        main = SettingGroup(page)
        self._sound_rows = []
        self._notify_switch = Switch(main, self._store.notify_mode != "off")
        self._notify_switch.toggled.connect(self._on_notify_switch)

        notify_row = SettingRow(self.tr("Desktop notifications"),
                                self.tr("When QGIS is in the background and a run finishes or needs you."),
                                self._notify_switch, main)
        notify_row.setProperty("keepNote", True)
        main.add_row(notify_row)
        self._sounds_group = SettingGroup(page)
        pickers = []
        for key, title, note in (
                ("notify_sound_waiting", self.tr("Needs your answer"), self.tr("Questions and approvals.")),
                ("notify_sound_finished", self.tr("Run finished"), "")):
            picker = self._sound_picker(self._sounds_group, key)
            pickers.append(picker)
            self._sounds_group.add_row(SettingRow(title, note, picker, self._sounds_group))
        self._sounds_summary = SettingRow(self.tr("Sounds"), "", self._btn(
            self.tr("Change"), GHOST_BTN_QSS,
            lambda: self._sounds_group.setVisible(not self._sounds_group.isVisible()), main), main)
        self._sounds_summary.setProperty("keepNote", True)
        main.add_row(self._sounds_summary)
        self._sync_sounds_summary()
        page.add(main)
        self._sounds_group.hide()
        page.add(self._sounds_group)
        self._notify_peek = final.Peek(main.parentWidget(), main, self._store, 30, at=0.9)
        if self._store.notify_mode == "off":
            self._notify_peek.mascot.set_mood("shy")
            self._notify_peek.rest_at(0.22)
        for picker in pickers:
            for play in picker.findChildren(QAbstractButton):
                play.clicked.connect(lambda _checked=False: self._mascot_hears())
        self._sync_notify_rows()

    def _sync_sounds_summary(self) -> None:
        names = [self._sound_names().get(getattr(self._store, key), getattr(self._store, key))
                 for key in ("notify_sound_waiting", "notify_sound_finished")]
        self._sounds_summary.set_note(self.tr("Needs your answer: {a} · Run finished: {b}").format(
            a=names[0], b=names[1]))

    def _mascot_hears(self) -> None:
        peek = getattr(self, "_notify_peek", None)
        if peek is not None:
            peek.mascot.burst("listen", 1000)
            peek.mascot.react("wiggle", 0.6)

    def _on_notify_switch(self, on: bool) -> None:
        peek = getattr(self, "_notify_peek", None)
        if peek is not None:

            peek.mascot.set_mood("listen" if on else "shy")
            peek.mascot.react("nod" if on else "duck", 0.4)
            peek.rest_at(0.0 if on else 0.22)
        self._on_notify_mode_changed("sound" if on else "off")

    def _add_profile(self, page: Page) -> None:


        page.add_group_title(self.tr("Profile"))
        profile = SettingGroup(page)
        self._name_edit = self._shell_line(profile, self._store.profile_name, self.tr("Your first name"))
        self._name_edit.editingFinished.connect(self._on_name_changed)
        name_row = SettingRow(self.tr("Name"), self.tr("From your TerraLab account."), self._name_edit, profile)
        profile.add_row(name_row)
        self._role_edit = self._shell_line(profile, self._store.profile_role, self.tr("Urban planner"))
        self._role_edit.editingFinished.connect(self._on_role_changed)
        role_row = SettingRow(self.tr("Job"), self.tr("From your TerraLab account."), self._role_edit, profile)
        profile.add_row(role_row)
        self._from_account_rows = (name_row, role_row)
        page.add(profile)

        self._context_title = page.add_group_title(self.tr("Context for every task"))
        context = SettingGroup(page)
        self._about_edit, self._about_count = self._text_row(
            context, self.tr("About you"), self.tr("What you work on, your data, your area."),
            self.tr("Urban planner at the city of Lyon, cadastre and PLU layers in EPSG:2154"),
            self._store.profile_about, "about")
        self._instructions_edit, self._instructions_count = self._text_row(
            context, self.tr("Instructions"), self.tr("Rules the AI agent always follows."),
            self.tr("Always answer in French, never delete a layer without asking"),
            self._store.profile_instructions, "instructions")
        page.add(context)
        self._context_group = context
        upsell = SettingGroup(page)
        upsell.add_row(SettingRow(self.tr("About you and Instructions"),
                                  self.tr("Tell the AI agent who you are and the rules it always "
                                          "follows. Included in Pro."),
                                  self._btn(self.tr("Get Pro"), final.UPGRADE_QSS,
                                            lambda: self._on_upgrade("personalisation"), upsell), upsell))
        self._context_upsell_btn = upsell.findChildren(QPushButton)[-1]
        page.add(upsell)
        self._context_upsell = upsell
        self._shell_apply_plan(self._memory_locked())

    def _shell_apply_plan(self, locked: bool) -> None:

        self._context_group.setVisible(not locked)
        self._context_upsell.setVisible(locked)
        if self._context_title is not None:
            self._context_title.setVisible(not locked)

    def _shell_line(self, parent: QWidget, text: str, placeholder: str) -> QLineEdit:
        from ..core.profile import PROFILE_LINE_MAX_CHARS

        edit = QLineEdit(text, parent)
        edit.setStyleSheet(INPUT_QSS)
        edit.setPlaceholderText(placeholder)
        placeholder_ink(edit)
        edit.setMaxLength(PROFILE_LINE_MAX_CHARS)
        edit.setFixedWidth(scale_px_length(_FIELD_W))
        return edit

    def _text_row(self, group: SettingGroup, title: str, note: str, placeholder: str, text: str, key: str):

        row = QFrame(group)
        row.setObjectName("settingsRow")
        row.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        col = QVBoxLayout(row)
        col.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(6)
        name = _label(title, _TITLE_QSS, row)
        head.addWidget(name)
        if group.property("hasInfo"):
            name.setToolTip(note)
            name.setAccessibleDescription(note)
        else:
            group.setProperty("hasInfo", True)
            head.addWidget(final.InfoButton(row, note, title), 0, Qt.AlignmentFlag.AlignVCenter)
        head.addStretch(1)
        col.addLayout(head)
        edit = QPlainTextEdit(row)
        edit.setStyleSheet(TEXTAREA_QSS)
        edit.setPlaceholderText(placeholder)
        placeholder_ink(edit)
        edit.setPlainText(text or "")
        edit.setFixedHeight(scale_px_length(48))
        edit.setTabChangesFocus(True)
        col.addWidget(edit)
        count = QLabel(row)
        count.setStyleSheet(_NOTE_QSS)
        count.setAlignment(Qt.AlignmentFlag.AlignRight)
        col.addWidget(count)
        self._update_count(edit, count)
        edit.textChanged.connect(lambda: self._on_profile_text_changed(edit, count, key))
        group.add_row(row)
        return edit, count


__all__ = ["ShellMixin"]

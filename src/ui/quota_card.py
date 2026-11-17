# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import html
from urllib.parse import quote, urlencode

from qgis.PyQt.QtCore import QCoreApplication, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QIcon
from qgis.PyQt.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.qt_compat import disconnect_quietly, one_shot
from ..core.quota_view import WARN_AT_RUNS_LEFT
from ..core.served_state import (
    get_pro_offer,
    get_pro_price,
    get_pro_price_excludes_tax,
    get_pro_runs_per_month,
    get_promo_active,
    get_student_offer,
    get_support_email,
    offer_text,
)
from .external_links import open_email, open_external_url
from .font_scale import scale_px_length, scale_qss_font_px
from .icons import pixmap_for
from .style import (
    _BTN_OFFER_PILL,
    ACCENT_INK,
    FONT_BASE,
    FONT_BODY,
    FONT_HINT,
    HOVER,
    HOVER_ON,
    INK,
    INK_2,
    INK_3,
    INSET,
    LINE,
    OFFER_PILL_PX,
    RADIUS_CONTROL,
    RADIUS_PANEL,
    SPACE_CARD,
    SPACE_OUTER,
    SPACE_TIGHT,
    SURFACE,
    WEIGHT_MEDIUM,
    keyboard_ring,
)



_CARD_QSS = (
    f"QWidget#quotaCard {{ background: {SURFACE}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_PANEL}px; }}"
    "QLabel { background: transparent; border: none; }"
)
_TITLE_QSS = f"font-size: {FONT_BASE + 1}px; font-weight: 600; color: {INK};"
_NOTE_QSS = f"font-size: {FONT_BODY}px; color: {INK_2};"
_CAPTION_QSS = f"font-size: {FONT_HINT}px; color: {INK_3};"
_EMAIL_QSS = (f"QWidget#quotaEmail {{ background: {INSET}; border: 1px solid {LINE};"
              f" border-radius: {RADIUS_CONTROL}px; }}")
_ADDRESS_QSS = f"font-size: {FONT_BODY}px; color: {INK}; background: transparent; border: none;"
_COPY_PX = 24
_BTN_COPY = scale_qss_font_px(
    f"QPushButton {{ background: {SURFACE}; color: {INK}; border: 1px solid {LINE};"
    f" border-radius: {RADIUS_CONTROL - 2}px; padding: 0 10px; font-size: {FONT_HINT}px;"
    f" font-weight: {WEIGHT_MEDIUM}; }}"
    f"QPushButton:hover {{ background: {HOVER}; }}"
    f"QPushButton:pressed {{ background: {HOVER_ON}; }}"
    f'QPushButton[ring="true"]:focus {{ border: 2px solid {ACCENT_INK}; padding: 0 9px; outline: none; }}'
)
_CARD_MARGINS = (14, 12, 12, 14)
_CLOSE_PX = 16

_SEAT_W, _SEAT_H = 36, 46

_BUTTON_PAD = 44
_COPIED_MS = 2000
_MAILTO_MAX = 1800
_WARNING_QSS = f"font-size: {FONT_HINT}px; color: {INK}; font-weight: {WEIGHT_MEDIUM}; background: transparent;"


def _link(href: str, text: str) -> str:
    return f'<a href="{href}" style="color: {INK_2}; text-decoration: none;">{html.escape(text)}</a>'


_STUDENT_SEEN: set = set()


def _track_student(event_name: str, surface: str) -> None:
    try:
        from ..core import telemetry
        from ..core import telemetry_events as ev

        telemetry.track(getattr(ev, event_name), {"product": "ai_agent", "surface": surface})
    except Exception:  # nosec B110
        pass


def note_student_offer_shown(surface: str) -> None:

    if surface in _STUDENT_SEEN:
        return
    _STUDENT_SEEN.add(surface)
    _track_student("STUDENT_OFFER_SHOWN", surface)


def open_student_offer(surface: str, parent=None) -> None:

    offer = get_student_offer()
    if not offer or not offer.get("url"):
        return
    _track_student("STUDENT_OFFER_CLICKED", surface)
    open_external_url(offer["url"], parent=parent)


class RunsLeftLine(QWidget):






    requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("runsLeftLine")
        self.where = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)



        self._label = QLabel(self)
        self._label.setTextFormat(Qt.TextFormat.RichText)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._label.linkActivated.connect(self._on_link)
        self._plain = ""
        row.addWidget(self._label, 1)

    def set_balance(self, left: int, limit: int, trial: tuple | None = None) -> None:


        self.where = "warning" if left <= WARN_AT_RUNS_LEFT else ""
        if self.where == "warning":
            count = (offer_text("runs.left.one", self.tr("1 free task left")) if left == 1
                     else offer_text("runs.left.many", self.tr("{n} free tasks left"), n=left))
            link = (trial_short() if trial
                    else offer_text("runs.link.pro", self.tr("Get more tasks with Pro")))
            self._label.setStyleSheet(scale_qss_font_px(_WARNING_QSS))
            text = (f'{html.escape(count)} &middot; <a href="pro" style="color: {ACCENT_INK};'
                    f' text-decoration: none;">{html.escape(link)}</a>')
            self._plain = f"{count} {link}"
            student = get_student_offer()
            if student and student["state"] == "offer":
                text += (f' &middot; <a href="student" style="color: {ACCENT_INK};'
                         f' text-decoration: none;">{html.escape(student["label"])}</a>')
                self._plain += f" {student['label']}"
                note_student_offer_shown("upgrade_line")
            self._label.setText(text)
        else:

            self._label.setText("")
            self._plain = ""
        tip = ""
        if self.where == "warning":
            tip = (trial_promise(*trial) if trial else
                   offer_text("runs.tip.pro", self.tr(
                       "Pro: {n} tasks a month, Medium and High effort for harder work, Autopilot"),
                       n=get_pro_runs_per_month()))
        self._label.setToolTip(tip)

    def text(self) -> str:
        return self._plain

    def _on_link(self, href: str) -> None:
        if href == "student":
            open_student_offer("upgrade_line", self.window() or self)
            return
        self.requested.emit(self.where)


def pro_price_per_month() -> str:




    if not get_promo_active():
        return ""
    price = get_pro_price()
    if not price:
        return ""
    if get_pro_price_excludes_tax():
        return offer_text("price.month.excl_vat",
                          QCoreApplication.translate("QuotaCard", "{amount}/month excl. VAT"), amount=price)
    return offer_text("price.month", QCoreApplication.translate("QuotaCard", "{amount}/month"), amount=price)


def trial_cta(_days: int = 7) -> str:

    return offer_text("trial.button", QCoreApplication.translate("QuotaCard", "Start 7 day free trial"))


def trial_short() -> str:

    return offer_text("trial.short", QCoreApplication.translate("QuotaCard", "Free trial"))


def get_pro_label() -> str:

    return offer_text("pro.button", QCoreApplication.translate("QuotaCard", "Get Pro"))


def trial_reassurance() -> str:


    student = get_student_offer()
    if student and student.get("applied"):
        return offer_text("trial.reassure.student", QCoreApplication.translate(
            "QuotaCard",
            "Free for 7 days, nothing to pay today, then monthly at your student price. Cancel in one click."))
    price = get_pro_price()
    if not price:
        return offer_text("trial.reassure.local", QCoreApplication.translate(
            "QuotaCard",
            "Free for 7 days, nothing to pay today, then monthly at your local price. Cancel in one click."))
    if get_pro_price_excludes_tax():
        return offer_text("trial.reassure.excl_vat", QCoreApplication.translate(
            "QuotaCard",
            "Free for 7 days, nothing to pay today, then {price}/month excl. VAT. Cancel in one click."),
            price=price)
    return offer_text("trial.reassure.price", QCoreApplication.translate(
        "QuotaCard", "Free for 7 days, nothing to pay today, then {price}/month. Cancel in one click."),
        price=price)


def trial_promise(days: int, _ends_on: str | None = None) -> str:

    return f"{trial_cta(days)}. {trial_reassurance()}"


class QuotaCard(QWidget):









    plans_requested = pyqtSignal(str)
    contact_requested = pyqtSignal(str)
    return_closed = pyqtSignal()
    low_closed = pyqtSignal()
    welcome_closed = pyqtSignal()
    start_now_requested = pyqtSignal()
    pay_requested = pyqtSignal()

    moment_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = ""
        self.moment = ""
        self._trial = None
        self._labels = ("", "")

        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(SPACE_OUTER)

        self._seat = QWidget(self)
        self._seat.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._seat.setFixedSize(scale_px_length(_SEAT_W), scale_px_length(_SEAT_H))
        self._seat.hide()
        outer.addWidget(self._seat, 0, Qt.AlignmentFlag.AlignBottom)
        self._card = QWidget(self)
        self._card.setObjectName("quotaCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._card.setStyleSheet(_CARD_QSS)
        outer.addWidget(self._card, 1)
        col = QVBoxLayout(self._card)
        col.setContentsMargins(*_CARD_MARGINS)
        col.setSpacing(SPACE_TIGHT)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(SPACE_CARD)
        self._title = QLabel(self._card)
        self._title.setTextFormat(Qt.TextFormat.PlainText)
        self._title.setStyleSheet(scale_qss_font_px(_TITLE_QSS))
        head.addWidget(self._title, 1)
        self._close = QToolButton(self._card)
        self._close.setIcon(QIcon(pixmap_for(self._close, "close", _CLOSE_PX, QColor(INK_3))))
        self._close.setAutoRaise(True)
        self._close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close.setAccessibleName(self.tr("Close"))
        self._close.setToolTip(self.tr("Close"))
        self._close.setStyleSheet("QToolButton { background: transparent; border: none; }")
        self._close.clicked.connect(self._on_close)
        head.addWidget(self._close, 0, Qt.AlignmentFlag.AlignTop)
        col.addLayout(head)
        self._note = QLabel(self._card)
        self._note.setTextFormat(Qt.TextFormat.PlainText)
        self._note.setStyleSheet(scale_qss_font_px(_NOTE_QSS))
        col.addWidget(self._note)

        self._email = QWidget(self._card)
        self._email.setObjectName("quotaEmail")
        self._email.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._email.setStyleSheet(_EMAIL_QSS)
        email = QHBoxLayout(self._email)
        email.setContentsMargins(10, 4, 4, 4)
        email.setSpacing(SPACE_CARD)
        self._address = QLabel(self._email)
        self._address.setStyleSheet(scale_qss_font_px(_ADDRESS_QSS))
        self._address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._address.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        email.addWidget(self._address, 1)
        self._copy = QPushButton(self._copy_label(), self._email)
        self._copy.setStyleSheet(_BTN_COPY)
        keyboard_ring(self._copy)
        self._copy.setFixedHeight(scale_px_length(_COPY_PX))
        self._copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._copy.setAutoDefault(False)
        self._copy.clicked.connect(self._copy_email)
        email.addWidget(self._copy, 0)
        self._email.hide()
        col.addSpacing(SPACE_TIGHT)
        col.addWidget(self._email)
        self._button = QPushButton(self._card)
        self._button.setStyleSheet(_BTN_OFFER_PILL)
        keyboard_ring(self._button)
        self._button.setFixedHeight(scale_px_length(OFFER_PILL_PX))
        self._button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._button.setAutoDefault(False)
        col.addWidget(self._button, 0, Qt.AlignmentFlag.AlignLeft)
        self._caption = QLabel(self._card)
        self._caption.setStyleSheet(scale_qss_font_px(_CAPTION_QSS))
        col.addWidget(self._caption)

        self._links = QLabel(self._card)
        self._links.setStyleSheet(scale_qss_font_px(_CAPTION_QSS))
        self._links.setTextFormat(Qt.TextFormat.RichText)
        self._links.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self._links.linkActivated.connect(self._on_link)
        col.addWidget(self._links)
        for label in (self._title, self._note, self._caption, self._links):
            label.setWordWrap(True)
        for label in (self._title, self._note, self._caption, self._address):
            label.setTextFormat(Qt.TextFormat.PlainText)
        self.hide()

    def seat(self) -> QWidget:

        return self._seat

    def set_seat_shown(self, shown: bool) -> None:

        if self._seat.isHidden() != shown:
            return
        self._seat.setVisible(shown)
        self.layout().activate()

    def focus_widget(self) -> QWidget:

        return self._email if self._email.isVisible() else self._button

    def offers(self) -> bool:

        return self.moment in ("low", "wall") or (self.state == "paid_out" and self._button.isVisible())



    def show_low(self, left: int, reset_text: str = "", trial: tuple | None = None) -> None:



        del reset_text
        self.state = "free_low"
        self._set(offer_text("low.title.one", self.tr("1 free task left this month")) if left == 1
                  else offer_text("low.title.many", self.tr("{n} free tasks left this month"), n=left),
                  offer_text("low.note.trial", self.tr("Want more? Pro is free for a week.")) if trial
                  else offer_text("low.note.return", self.tr("Want more? Come back to Pro: {n} tasks a month."),
                                  n=get_pro_runs_per_month()) if get_pro_offer() == "return"
                  else offer_text("low.note.pro", self.tr("Want more? Pro has {n} tasks a month."),
                                  n=get_pro_runs_per_month()),
                  close=True)
        self._offer(trial, wall=False)
        self._moment("low")

    def show_exhausted(self, is_subscriber: bool, limit: int, reset_text: str = "",
                       trial: tuple | None = None, account_cap: str | None = None,
                       start_now: bool = False) -> None:






        if is_subscriber:
            self.state = "paid_out"
            if limit and reset_text:
                note = offer_text("spent.note.both", self.tr(
                    "You used all {n} tasks this month. They come back on {date}."), n=limit, date=reset_text)
            elif reset_text:
                note = offer_text("spent.note.date", self.tr("They come back on {date}."), date=reset_text)
            else:
                note = offer_text("spent.note.used", self.tr("You used all {n} tasks this month."),
                                  n=limit) if limit else ""
            self._set(offer_text("spent.title", self.tr("Your tasks for this month are used")), note)
            if start_now:
                self._button_says(offer_text("spent.start", self.tr("Start my plan now")))
                self._route_button(self.start_now_requested.emit)
            else:
                self._button.hide()
            self._links.setText(_link("write", offer_text("spent.write", self.tr("Need more this month? Write to us"))))
            self._links.show()
            self._moment("spent")
            return
        self.state = "free_out"
        if account_cap is not None:
            self._set(self.tr("Free AI Agent was already used by other accounts on this computer."),
                      account_cap or self.tr("Upgrade to Pro to keep going. Shared computer, or a mistake? "
                                             "Contact {email} and we will unlock it.").format(
                          email=get_support_email()))
        else:
            self._set(offer_text("wall.title", self.tr("That was your last free task")),
                      offer_text("wall.note.date", self.tr("Free tasks come back on {date}."), date=reset_text)
                      if reset_text
                      else offer_text("wall.note.reset", self.tr("Free tasks come back at your monthly reset.")))
        self._offer(trial, wall=True)
        self._moment("wall")

    def show_payment_failed(self, amount: str = "", declined: bool = False) -> None:




        self.state = "payment_failed"
        if declined:
            self._set(offer_text("declined.title", self.tr("Card declined")),
                      offer_text("declined.note", self.tr("Your trial didn't start. Nothing was charged.")))
            self._button_says(offer_text("declined.button", self.tr("Try another card")))
            self._route_button(self.pay_requested.emit)
            self._links.hide()
            self._moment("payment")
            return
        self._set(offer_text("payment.title.amount", self.tr("Your payment of {amount} failed"), amount=amount)
                  if amount else offer_text("payment.title", self.tr("Your Pro payment failed")),
                  offer_text("payment.note", self.tr("Pro is off until it's paid.")))
        self._button_says(offer_text("payment.button.amount", self.tr("Pay {amount} now"),
                                     amount=amount.replace("&", "&&")) if amount
                          else offer_text("payment.button", self.tr("Pay now")))
        self._route_button(self.pay_requested.emit)
        self._links.hide()
        self._moment("payment")

    def show_checkout_return(self, trial: tuple | None = None) -> None:



        self.state = "checkout_return"
        self._trial = trial
        self._set(offer_text("return.title", self.tr("Need it for work?")),
                  offer_text("return.note.trial", self.tr(
                      "No card to pay with? Write to us and we'll set up your 7 free days."))
                  if trial else offer_text("return.note", self.tr(
                      "No card to pay with? Write to us and we'll send your company an invoice.")), close=True)
        self._address.setText(get_support_email())
        self._copy.setText(self._copy_label())
        self._email.show()
        self._button_says(offer_text("return.button", self.tr("Write to us")))
        self._route_button(self._write)
        self._links.setText(_link("plans", offer_text("return.back", self.tr("Back to the Pro page"))))
        self._links.show()
        self._moment("return")

    def show_pro_welcome(self, limit: int, trial_until: str = "") -> None:


        self.state = "pro_welcome"
        lines = [offer_text("welcome.runs", self.tr("{n} tasks a month."), n=limit)] if limit > 0 else []
        if trial_until:
            lines.append(offer_text("welcome.trial", self.tr("Free trial until {date}."), date=trial_until))
        self._set(offer_text("welcome.title", self.tr("You're on Pro")), " ".join(lines))
        self._button_says(offer_text("welcome.ok", self.tr("OK")))
        self._route_button(self.welcome_closed.emit)
        self._links.hide()
        self._moment("welcome")

    def clear(self) -> None:
        self.state = self.moment = ""
        self.hide()



    def _set(self, title: str, note: str, close: bool = False) -> None:
        self._title.setText(title)
        self._note.setText(note)
        self._note.setVisible(bool(note))
        self._close.setVisible(close)
        self._email.hide()
        self._caption.hide()

    def _moment(self, moment: str) -> None:
        self.show()
        if moment != self.moment:
            self.moment = moment
            self.moment_changed.emit(moment)

    def _offer(self, trial: tuple | None, wall: bool) -> None:

        where = self._where()
        if trial:
            self._button_says(trial_cta(trial[0]), trial_short())
            self._button.setToolTip(trial_promise(*trial))
            text = offer_text("wall.caption.trial", self.tr("Nothing to pay today"))
        else:
            self._button_says(get_pro_label())
            price = pro_price_per_month()
            text = offer_text("wall.caption.price", self.tr("{price} · cancel anytime"),
                              price=price) if price else ""
            if not text and get_pro_offer() == "return":
                text = offer_text("wall.caption.return", self.tr("{n} tasks a month, cancel anytime"),
                                  n=get_pro_runs_per_month())
        self._caption.setText(text)
        self._caption.setVisible(wall and bool(text))
        self._route_button(lambda: self.plans_requested.emit(where))
        student = get_student_offer()
        student = wall and student and student["state"] == "offer"
        if student:
            self._links.setText(_link("student", get_student_offer()["label"]))
            note_student_offer_shown("wall")
        self._links.setVisible(bool(student))

    def _button_says(self, label: str, short: str = "") -> None:
        self._labels = (label, short or label)
        self._button.setToolTip("")
        self._button.show()
        self._fit_button()

    def _fit_button(self) -> None:


        label, short = self._labels
        if not label:
            return
        self._button.ensurePolished()
        room = self._card.width() - _CARD_MARGINS[0] - _CARD_MARGINS[2]
        need = self._button.fontMetrics().horizontalAdvance(label) + scale_px_length(_BUTTON_PAD)
        self._button.setText(label if room <= 0 or need <= room else short)
        self._button.setAccessibleName(label.replace("&&", "&"))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_button()

    def _where(self) -> str:

        return {"checkout_return": "checkout_return", "free_low": "low_card"}.get(self.state, "exhausted_card")

    def _on_link(self, href: str) -> None:
        if href == "student":
            open_student_offer("wall", self.window() or self)
        elif href == "plans":
            self.plans_requested.emit("checkout_return")
        elif href == "write":
            self._write()

    def _on_close(self) -> None:
        (self.low_closed if self.state == "free_low" else self.return_closed).emit()

    def _write(self) -> None:

        self.contact_requested.emit(self._where())
        address = get_support_email()
        if self.state == "paid_out":
            kind, subject, body = "more", self.tr("More AI Agent tasks this month"), self.tr(
                "Hello,\n\nI used all my AI Agent tasks this month. Could you raise my quota?\n\nThank you")
        elif self._trial:
            kind, subject, body = "trial", self.tr("AI Agent Pro for work, without a card"), self.tr(
                "Hello,\n\nI'd like to use AI Agent Pro at work but I can't pay by card. "
                "Could you set up the 7 free days for us?\n\nCompany:\nName:\n\nThank you")
        else:
            kind, subject, body = "invoice", self.tr("AI Agent Pro for work, by invoice"), self.tr(
                "Hello,\n\nI'd like to use AI Agent Pro at work but I can't pay by card. "
                "Could you send my company an invoice or a quote?\n\nCompany:\nName:\n\nThank you")
        subject = offer_text(f"mail.{kind}.subject", subject)
        shipped, body = body, offer_text(f"mail.{kind}.body", body)


        query = urlencode({"subject": subject, "body": body.replace("\r\n", "\n").replace("\n", "\r\n")},
                          quote_via=quote)
        if len(query) > _MAILTO_MAX:
            query = urlencode({"subject": subject, "body": shipped.replace("\n", "\r\n")}, quote_via=quote)
        open_email(f"mailto:{address}?{query}", address, parent=self.window() or self)

    def _copy_email(self) -> None:
        try:
            QApplication.clipboard().setText(get_support_email())
        except (RuntimeError, AttributeError):
            return
        self.contact_requested.emit(self._where() + ":copy")
        self._copy.setText(offer_text("return.copied", self.tr("Copied")))
        self.moment_changed.emit("copied")

        if getattr(self, "_copied_timer", None) is None:
            self._copied_timer = one_shot(self, lambda: self._copy.setText(self._copy_label()))
        self._copied_timer.start(_COPIED_MS)

    def _copy_label(self) -> str:
        return offer_text("return.copy", self.tr("Copy"))

    def _route_button(self, slot) -> None:
        disconnect_quietly(self._button, "clicked")
        if slot is not None:
            self._button.clicked.connect(slot)

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Billing: the one page `SettingsDialog` builds for the plan in force."""







from __future__ import annotations

from qgis.PyQt.QtWidgets import QProgressBar, QPushButton, QVBoxLayout, QWidget

from .account_settings_dialog import format_count, resolve_plan_runs
from .font_scale import apply_font_scale_to_tree
from .settings_pages import (
    GHOST_BTN_QSS,
    PROGRESS_QSS,
    USAGE_KEYS,
    BillingCard,
    BillingCardRow,
    Page,
)
from .shared import format_reset_date, get_plan_name, get_pro_runs_per_month, get_support_email


class BillingPageMixin:
    """Which plan is in force, what is left of it, and the way to the site."""



    def _build_billing(self) -> Page:
        """Which plan is in force, what Pro adds, and the way out to the site."""










        page = Page(self.tr("Billing"),
                    self.tr("The plan this copy of QGIS is signed in on."), self)
        self._billing_box = QWidget(page)
        self._billing_col = QVBoxLayout(self._billing_box)
        self._billing_col.setContentsMargins(0, 0, 0, 0)
        self._billing_col.setSpacing(14)
        page.add(self._billing_box)
        self._paint_billing({})
        return page

    def _paint_billing(self, info: dict) -> None:
        """Repaint the Billing page from the same dict the Account page gets."""












        col = getattr(self, "_billing_col", None)
        if col is None:
            return
        while col.count():
            item = col.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        box = self._billing_box
        info = dict(info or {})
        if info.get("loading"):
            col.addWidget(self._muted(self.tr("Loading your plan..."), box))
            return
        usage = dict(info.get("usage") or {})
        for key in USAGE_KEYS:
            if key in info and key not in usage:
                usage[key] = info[key]
        plan = resolve_plan_runs(usage, self._find_subscription(info) or {})

        cards = BillingCardRow(box)
        cards.add_card(self._billing_plan_card(cards, plan))
        cards.add_card(self._billing_dashboard_card(cards, plan.is_subscriber))
        col.addWidget(cards)
        apply_font_scale_to_tree(box)

    def _billing_plan_card(self, parent: QWidget, plan) -> BillingCard:
        """The plan in force: what is left of it, and who to write to for more."""
        card = BillingCard(
            get_plan_name("pro", self.tr("Pro plan")) if plan.is_subscriber
            else get_plan_name("free", self.tr("Free plan")), parent)
        if plan.limit is not None and plan.used is not None:
            left = max(0, int(plan.limit) - int(plan.used))
            card.add_stat(format_count(left), self.tr("runs left of {limit} this month").format(
                limit=format_count(plan.limit)))
            bar = QProgressBar(card)
            bar.setRange(0, max(int(plan.limit), 1))
            bar.setValue(min(left, int(plan.limit)))
            bar.setTextVisible(False)
            bar.setFixedHeight(6)
            bar.setStyleSheet(PROGRESS_QSS)
            card.add(bar)
        elif plan.used is not None:
            card.add_stat(format_count(plan.used), self.tr("runs this month"))
        else:


            card.add_status(self.tr("No runs counted yet this month."))
        reset_str = format_reset_date(plan.reset_iso or "")
        if reset_str:
            card.add_note(self.tr("Resets {date}").format(date=reset_str))
        if plan.is_subscriber:
            card.add_note(self.tr("Need more than {n} runs a month? Write to {email}.").format(
                n=get_pro_runs_per_month(), email=get_support_email()))
        return card

    def _billing_dashboard_card(self, parent: QWidget, is_subscriber: bool) -> BillingCard:
        """The way out to the site, and nothing priced."""







        card = BillingCard(self.tr("Manage"), parent)
        card.add_subtitle(self.tr("Your plan, payment method and invoices are on the "
                                  "TerraLab dashboard."))
        if not is_subscriber:

            offer = QPushButton(self.tr("See what Pro unlocks"), card)
            offer.clicked.connect(self._on_upgrade)
            card.add_action(offer)
        manage = QPushButton(self.tr("Open the dashboard"), card)
        manage.clicked.connect(lambda: self._open_dashboard("billing_page"))
        card.add_action(manage)
        if not is_subscriber:
            manage.setStyleSheet(GHOST_BTN_QSS)
        card.add_note(self.tr("Payment happens on the TerraLab website, never inside QGIS. "
                              "Your plan here updates on its own."))
        return card

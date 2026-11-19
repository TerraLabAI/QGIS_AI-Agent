# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget

from ..core.logger import log_warning
from . import map_preview
from .card_controls import _ASK_LABEL_QSS, _BTN_GHOST_PILL, _BTN_QUIET_LINK, AnswerFooter, _pill, ask_head
from .font_scale import scale_qss_font_px
from .style import FONT_BODY, INK, RADIUS_CONTROL, RED, SPACE_CARD, SPACE_OUTER, WEIGHT_MEDIUM, _focus_ring
from .tool_describe import group_number
from .widgets import ChatLabel

_BTN_DELETE_PILL = scale_qss_font_px(
    f"QPushButton {{ background: {RED}; color: #ffffff; border: none;"
    f" border-radius: {RADIUS_CONTROL}px; padding: 0 14px; font-size: {FONT_BODY}px;"
    f" font-weight: {WEIGHT_MEDIUM}; }}"
    "QPushButton:hover { background: #b02e33; }"
    "QPushButton:pressed { background: #962629; }"

    + _focus_ring(INK, "0 12px")
)
_NOTE_QSS = _ASK_LABEL_QSS

_IDS_SHOWN = 12

_FLASH_AFTER_MS = 250


class MapProposalMixin:


    _proposal: dict | None = None
    _map_outline = None

    def _read_proposal(self, call: dict) -> dict | None:
        try:
            return map_preview.proposal_of(call["name"], call["args"])
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Map proposal not read: {exc}")
            return None

    def _proposal_texts(self, proposal: dict) -> tuple[str, str, str]:

        n, layer = proposal["count"], proposal["layer_name"]
        count = group_number(n)
        if proposal["kind"] == "delete":
            title = self.tr("Delete 1 feature from {layer}").format(layer=layer) if n == 1 else \
                self.tr("Delete {n} features from {layer}").format(n=count, layer=layer)
            return title, self.tr("Hatched in red on the map. Nothing is deleted yet."), \
                self.tr("Delete {n}").format(n=count)
        if proposal["kind"] == "overwrite":
            title = self.tr("Overwrite 1 feature of {layer}").format(layer=layer) if n == 1 else \
                self.tr("Overwrite {n} features of {layer}").format(n=count, layer=layer)
            return title, self.tr("Hatched in red on the map. Nothing is written yet."), \
                self.tr("Overwrite {n}").format(n=count)
        title = self.tr("Remove {layer} from the project").format(layer=layer)
        if proposal["memory"]:
            note = self.tr("Its features exist only in this project and go with it. Hatched in red "
                           "on the map; nothing is removed yet.")
        else:
            note = self.tr("Hatched in red on the map. Its data source stays as it is; nothing is "
                           "removed yet.")
        return title, note, self.tr("Remove layer")

    def _proposal_details(self, proposal: dict, call: dict, reasons: str) -> list[str]:
        lines = []
        if proposal["kind"] in ("delete", "overwrite"):
            lines.append(self.tr("{n} of {total} features in {layer}").format(
                n=group_number(proposal["count"]), total=group_number(proposal["total"]),
                layer=proposal["layer_name"]))
            if proposal.get("expression"):
                lines.append(self.tr("Rule: {expression}").format(expression=proposal["expression"]))
            ids = proposal["fids"]
            shown = ", ".join(str(i) for i in ids[:_IDS_SHOWN])
            more = len(ids) - _IDS_SHOWN
            lines.append(self.tr("Feature ids: {ids}").format(ids=shown) if more <= 0 else
                         self.tr("Feature ids: {ids} and {n} more").format(ids=shown, n=group_number(more)))
        elif proposal["count"] >= 0:
            lines.append(self.tr("{n} features in {layer}").format(
                n=group_number(proposal["count"]), layer=proposal["layer_name"]))
        if proposal["kind"] == "overwrite":
            for update in [u for u in call["args"].get("updates") or [] if isinstance(u, dict)][:5]:
                values = ", ".join(f"{k} = {v!r}" for k, v in (update.get("attributes") or {}).items())
                lines.append(self.tr("Feature {fid}: {values}").format(fid=update.get("fid"), values=values))
        if reasons:
            lines.append(reasons)
        return lines

    def _build_proposal_body(self, proposal: dict, call: dict, reasons: str) -> QWidget:
        body_widget = QWidget(self)
        body = QVBoxLayout(body_widget)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(SPACE_OUTER)
        title, note, primary = self._proposal_texts(proposal)
        head = ask_head(body_widget, title)
        self._sentence = head.text_label
        body.addWidget(head)
        line = ChatLabel(note, body_widget, wrap=True, selectable=True)
        line.setObjectName("proposalNote")
        line.setStyleSheet(_NOTE_QSS)
        body.addWidget(line)
        self._proposal_more = QWidget(body_widget)
        more = QVBoxLayout(self._proposal_more)
        more.setContentsMargins(0, 0, 0, 0)
        more.setSpacing(SPACE_CARD)
        for text in self._proposal_details(proposal, call, reasons):
            detail = ChatLabel(text, self._proposal_more, wrap=True, selectable=True)
            detail.setObjectName("proposalDetail")
            detail.setStyleSheet(_NOTE_QSS)
            more.addWidget(detail)
        self._proposal_more.hide()
        body.addWidget(self._proposal_more)

        footer = AnswerFooter(body_widget)
        links = QWidget(footer)
        row = QHBoxLayout(links)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(SPACE_CARD)
        self._details_btn = self._button(self.tr("Details"), _BTN_QUIET_LINK, self._toggle_proposal_details)
        self._details_btn.setObjectName("proposalDetails")
        locate = self._button(self.tr("Show on map"), _BTN_QUIET_LINK, self._show_proposal)
        locate.setObjectName("proposalShowOnMap")
        row.addWidget(self._details_btn)
        row.addWidget(locate)
        cancel = _pill(self._button(self.tr("Cancel"), _BTN_GHOST_PILL, lambda: self._decide("deny")))
        self._allow_btn = _pill(self._button(primary, _BTN_DELETE_PILL, lambda: self._decide("allow")))
        self._answer_buttons = [(cancel, "deny"), (self._allow_btn, "allow")]
        footer.set_widgets(links, [cancel, self._allow_btn])
        body.addWidget(footer)
        return body_widget

    def _toggle_proposal_details(self) -> None:
        shown = not self._proposal_more.isVisible()
        self._proposal_more.setVisible(shown)
        self._details_btn.setText(self.tr("Hide details") if shown else self.tr("Details"))

    def _target(self) -> dict | None:
        proposal = self._proposal
        return {"layer_id": proposal["layer_id"], "fids": proposal["fids"]} if proposal else None

    def _show_map_preview(self) -> None:
        if self._proposal is None:
            return
        if self._map_outline is None:
            self._map_outline = map_preview.Outline("delete")
        try:
            self._map_outline.show(self._target())
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Map proposal not drawn: {exc}")

    def _hide_map_preview(self, decision: str = "") -> None:
        outline = self._map_outline
        if outline is None:
            return
        geoms, crs = outline.geometries, outline.crs
        outline.hide()
        proposal = self._proposal
        if decision == "allow" and proposal is not None and proposal["kind"] == "delete" and geoms:
            layer_id = proposal["layer_id"]
            QTimer.singleShot(_FLASH_AFTER_MS, lambda: _flash_gone(layer_id, geoms, crs))

    def _show_proposal(self) -> None:
        outline = self._map_outline
        try:
            map_preview.show_on_map(self._target(), outline.geometries if outline else None,
                                    outline.crs if outline else None, RED)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Show on map failed: {exc}")


def _flash_gone(layer_id: str, geoms, crs) -> None:
    try:
        map_preview.flash(layer_id, geoms, crs, RED)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Delete flash failed: {exc}")


__all__ = ["MapProposalMixin"]

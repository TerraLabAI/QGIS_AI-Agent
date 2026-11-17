# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

from qgis.PyQt.QtCore import QLocale, Qt
from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..core.logger import log_warning
from ..core.qt_compat import qgis_iface
from .cards_click import _ClickRow
from .icons import pixmap_for
from .style import INK_2, INK_3, qcolor
from .widgets import ChatLabel, ElidedLabel

GLYPH_PX = 13
CHEVRON_PX = 12

TRANSFORMATIONS_PAGE = "mProjTransformations"


def _code(authid: str) -> str:

    return authid.split(":", 1)[1] if authid.upper().startswith("EPSG:") else authid


def _datum_steps(operation: str) -> str:



    steps = [step.removeprefix("Inverse of ") for step in operation.split(" + ") if " to " in step]
    return " + ".join(steps) or operation


def _metres(value) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return f"{value:g}".replace(".", QLocale().decimalPoint()) + " m"


class _Head(_ClickRow):


    def __init__(self, owner: TransformationRow):
        super().__init__(owner)
        self.clicked.connect(owner.toggle)


class TransformationRow(QFrame):


    def __init__(self, transformations, parent=None):
        super().__init__(parent)
        self.entries = [x for x in (transformations or ()) if isinstance(x, dict) and x.get("operation")]
        self.setObjectName("transformRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        if not self.entries:
            self.setVisible(False)
            return
        column = QVBoxLayout(self)
        column.setContentsMargins(10, 6, 10, 6)
        column.setSpacing(4)

        self._head = _Head(self)
        line = QHBoxLayout(self._head)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(6)
        globe = QLabel(self._head)
        globe.setPixmap(pixmap_for(globe, "globe", GLYPH_PX, qcolor(INK_2)))
        globe.setFixedSize(GLYPH_PX, GLYPH_PX)
        line.addWidget(globe)
        title = QLabel(self.tr("Transformation") if len(self.entries) == 1
                       else self.tr("{count} transformations").format(count=len(self.entries)), self._head)
        title.setObjectName("transformTitle")
        line.addWidget(title)
        summary = ElidedLabel(self._summary(), self._head, Qt.TextElideMode.ElideRight)
        summary.setObjectName("transformHint")
        line.addWidget(summary, 1)
        self._chevron = QLabel(self._head)
        self._chevron.setFixedSize(CHEVRON_PX, CHEVRON_PX)
        line.addWidget(self._chevron)
        for label in (globe, title, summary, self._chevron):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        column.addWidget(self._head)

        self._body = QWidget(self)
        body = QVBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        for entry in self.entries:
            said = ChatLabel(self._sentence(entry), self._body, wrap=True, selectable=True)
            said.setObjectName("transformHint")
            said.setToolTip(str(entry.get("operation") or ""))
            body.addWidget(said)
        change = QLabel(f'<a href="change" style="color: {INK_2};">{self.tr("Change in Project Properties")}</a>',
                        self._body)
        change.setObjectName("transformLink")
        change.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse
                                       | Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        change.linkActivated.connect(self._open_properties)
        body.addWidget(change, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(self._body)
        self._open = False
        self._show_fold()
        said = self.tr("Coordinate transformation: {what}").format(what=self._summary())
        self.setAccessibleName(said)
        self._head.setAccessibleName(said)

    def is_empty(self) -> bool:
        return not self.entries

    def _summary(self) -> str:
        first = self.entries[0]
        pair = f"{first.get('from', '')} → {_code(str(first.get('to', '')))}"
        accuracy = _metres(first.get("accuracy_m"))
        return f"{pair} · {accuracy}" if accuracy and len(self.entries) == 1 else pair

    def _sentence(self, entry: dict) -> str:
        operation = _datum_steps(str(entry.get("operation") or ""))
        accuracy = entry.get("accuracy_m")
        if isinstance(accuracy, (int, float)):
            said = self.tr("{layer} goes from {source} to {target} through {operation}, accurate to about "
                           "{accuracy}.")
        else:
            said = self.tr("{layer} goes from {source} to {target} through {operation}, of unknown accuracy.")
        said = said.format(layer=entry.get("layer", ""), source=entry.get("from", ""), target=entry.get("to", ""),
                           operation=operation, accuracy=_metres(accuracy))
        others = int(entry.get("candidates") or 1) - 1
        best = entry.get("best_accuracy_m")
        if isinstance(best, (int, float)) and isinstance(accuracy, (int, float)) and best < accuracy:
            said += " " + self.tr("A more accurate one ({accuracy}) needs a grid file this QGIS does not have.").format(
                accuracy=_metres(best))
        elif others == 1:
            said += " " + self.tr("The other option is no more accurate.")
        elif others > 1:
            said += " " + self.tr("The {count} other options are no more accurate.").format(count=others)
        return said

    def toggle(self) -> None:
        self._open = not self._open
        self._show_fold()

    def _show_fold(self) -> None:
        self._body.setVisible(self._open)
        self._chevron.setPixmap(pixmap_for(self._chevron, "chevron_down" if self._open else "chevron_right",
                                           CHEVRON_PX, qcolor(INK_3)))
        self._head.setToolTip(self.tr("Hide details") if self._open else self.tr("Show details"))
        self.updateGeometry()

    def _open_properties(self, _href: str = "") -> None:
        iface = qgis_iface()
        try:
            iface.showProjectPropertiesDialog(TRANSFORMATIONS_PAGE)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Project Properties not opened on Transformations: {exc}")

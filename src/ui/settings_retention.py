# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import contextlib

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import QCoreApplication, QObject, Qt
from qgis.PyQt.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from ..api.account import GenericRequestTask, get_device_platform
from ..api.terralab_client import PRODUCT_ID, TerraLabClient
from ..core.settings import Settings
from .confirm_dialog import ask_confirm
from .font_scale import scale_px_length
from .settings_pages import GHOST_BTN_QSS, ROW_NOTE_QSS, SettingRow, combo_qss

_CHOICES = (0, 30, 90, 180, 365, None)
_UNSET = object()


def _auth() -> dict:
    try:
        settings = Settings()
        if not settings.has_activation_key:
            return {}
        headers = {"Authorization": f"Bearer {settings.activation_key}", "X-Product-ID": PRODUCT_ID}
        with contextlib.suppress(Exception):
            headers["X-Device-Hash"] = settings.device_hash
            platform_label = get_device_platform()
            if platform_label:
                headers["X-Device-Platform"] = platform_label
        return headers
    except Exception:  # noqa: BLE001
        return {}


def _rank(days) -> float:

    return float("inf") if days in (None, 0) else float(days)


class RetentionRow(QObject):


    def __init__(self, dialog, group):
        super().__init__(dialog)
        self._dialog = dialog
        self._data: dict | None = None
        self._task = None
        self._pending = _UNSET
        self._control = QWidget(group)
        self._box = QHBoxLayout(self._control)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(8)
        self.row = SettingRow(self.tr("History retention"), "", self._control, group)
        self.divider = None
        self._set_visible(False)

    def _set_visible(self, on: bool) -> None:
        self.row.setVisible(on)
        if self.divider is not None:
            self.divider.setVisible(on)

    def tr(self, text: str) -> str:  # noqa: D401
        return QCoreApplication.translate("SettingsDialog", text)



    def _label(self, days) -> str:
        return {0: self.tr("No copies (0 days)"), 30: self.tr("30 days"), 90: self.tr("90 days"),
                180: self.tr("6 months"),
                365: self.tr("1 year"), None: self.tr("Until I delete it")}.get(
            days, self.tr("{n} days").format(n=days))

    def _pro_note(self) -> str:
        return self.tr("How long your run records stay on our servers, for all TerraLab "
                       "plugins. Your chats stay on this computer.")



    def load(self) -> None:
        auth = _auth()
        if not auth:
            self._set_visible(False)
            return
        if self._data is not None:
            self._render()
            return
        self._run(lambda: TerraLabClient().get_data_retention(auth=auth),
                  self._on_loaded, lambda *_: self._hide())

    def _run(self, fn, ok, fail) -> None:
        task = GenericRequestTask(self.tr("Loading history retention"), fn)
        task.succeeded.connect(ok)
        task.failed.connect(fail)
        self._task = task
        QgsApplication.taskManager().addTask(task)

    def _hide(self) -> None:
        try:
            self._set_visible(False)
        except RuntimeError:
            pass

    def _on_loaded(self, data: object) -> None:
        if not isinstance(data, dict) or data.get("tier") not in ("free", "pro", "zero"):
            self._hide()
            return
        self._data = dict(data)
        try:
            self._render()
        except RuntimeError:
            pass



    def _clear(self) -> None:
        while self._box.count():
            widget = self._box.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def _text(self, text: str) -> QLabel:
        label = QLabel(text, self._control)
        label.setStyleSheet(ROW_NOTE_QSS)
        self._box.addWidget(label)
        return label

    def _render(self, error: str = "") -> None:
        data = self._data or {}
        tier = data.get("tier")
        days = data.get("history_retention_days")
        self._clear()
        if tier == "pro":
            if self._pending is not _UNSET:
                days = self._pending
            combo = QComboBox(self._control)
            combo.setStyleSheet(combo_qss(self._dialog))
            combo.setCursor(Qt.CursorShape.PointingHandCursor)
            combo.setMinimumWidth(scale_px_length(150))
            choices = [c for c in (data.get("choices") or _CHOICES) if c is None or isinstance(c, int)]
            if 0 not in choices:
                choices.insert(0, 0)
            selectable = set(choices)
            if days not in choices and (days is None or isinstance(days, int)):
                choices.append(days)
            for value in choices:
                combo.addItem(self._label(value), value)
            for index, value in enumerate(choices):
                if value not in selectable:
                    item = combo.model().item(index)
                    if item is not None:
                        item.setEnabled(False)
            self._selectable = selectable
            combo.setCurrentIndex(choices.index(days) if days in choices else 0)
            combo.setEnabled(data.get("can_change") is not False and self._pending is _UNSET)
            combo.currentIndexChanged.connect(lambda i, c=combo: self._on_pick(c.itemData(i)))
            self._box.addWidget(combo)
            self.row.set_note(error or (self.tr("No copy of your work is written. Your "
                                                "chats stay on this computer.") if days == 0
                                        else self._pro_note()))
        elif tier == "zero":
            self._text(self.tr("Set by your contract"))
            self.row.set_note(self.tr("Zero data retention: no copy of your work is kept "
                                      "beyond what your contract allows."))
        elif days is None:
            self._text(self.tr("Until you delete it"))
            self.row.set_note(self.tr("Free plan. Pro lets you choose 30 days to 1 year."))
        else:
            self._text(self._label(days))
            if data.get("can_change"):
                button = QPushButton(self.tr("Keep until I delete it"), self._control)
                button.setStyleSheet(GHOST_BTN_QSS)
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setAutoDefault(False)
                button.setEnabled(self._pending is _UNSET)
                button.clicked.connect(lambda: self._save(None))
                self._box.addWidget(button)
            self.row.set_note(error or self.tr("Free plan. Pro lets you choose 30 days to 1 year."))
        self._set_visible(True)



    def _on_pick(self, days) -> None:
        current = (self._data or {}).get("history_retention_days")
        if days == current:
            return
        if days not in getattr(self, "_selectable", set(_CHOICES)):
            self._render()
            return
        if _rank(days) < _rank(current) and not ask_confirm(
                self._dialog, title=self.tr("Shorten history retention?"),
                note=self.tr("Older history will be deleted for good, files included, "
                             "within two days. Continue?"),
                confirm_text=self.tr("Continue"), glyph="trash",
                object_name="retentionConfirm"):
            self._render()
            return
        self._save(days)

    def _save(self, days) -> None:
        auth = _auth()
        if not auth or self._pending is not _UNSET:
            self._render()
            return
        self._pending = days
        self._render()
        self._run(lambda: TerraLabClient().set_data_retention(auth=auth, days=days),
                  self._on_saved, self._on_save_failed)

    def _on_saved(self, data: object) -> None:
        self._pending = _UNSET
        if isinstance(data, dict) and data.get("tier") in ("free", "pro", "zero"):
            self._data = dict(data)
        try:
            self._render()
            show_saved = getattr(self._dialog, "_show_saved", None)
            if callable(show_saved):
                show_saved()
        except RuntimeError:
            pass

    def _on_save_failed(self, message: str, _code: str) -> None:
        self._pending = _UNSET
        try:
            self._render(error=message or self.tr("Could not save. Try again."))
        except RuntimeError:
            pass

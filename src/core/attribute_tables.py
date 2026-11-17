# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

from contextlib import contextmanager

import qgis.core
from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent
from qgis.PyQt.QtWidgets import QApplication, QDockWidget

from . import background
from .logger import log_warning
from .qt_compat import qt_deleted


_DIALOG_PREFIX = "QgsAttributeTableDialog/"


def _open_tables(layer) -> list:
    name = _DIALOG_PREFIX + layer.id()
    return [widget for widget in QApplication.allWidgets() if widget.objectName() == name]


def _docked(table) -> bool:
    widget = table.parentWidget()
    while widget is not None:
        if isinstance(widget, QDockWidget):
            return True
        widget = widget.parentWidget()
    return False


def close_tables(layer) -> list[bool]:

    if not isinstance(layer, QgsVectorLayer) or qt_deleted(layer):
        return []
    if not background.on_main_thread():
        log_warning(f"Attribute tables of {layer.name()} left open: a source change off the main thread")
        return []
    tables = _open_tables(layer)
    docked = [_docked(table) for table in tables]
    for table in tables:
        table.close()



    for table in tables:
        QCoreApplication.sendPostedEvents(table, QEvent.Type.DeferredDelete)
        if not qt_deleted(table):
            sip.delete(table)
    return docked


def _dock_default():


    tree = getattr(qgis.core, "QgsSettingsTree", None)
    node = tree.node("attribute-table") if tree is not None else None
    return node.childSetting("attribute-table-default-docked") if node is not None else None


def reopen_tables(layer, docked: list[bool]) -> None:


    from qgis.utils import iface

    if (iface is None or qt_deleted(layer) or not layer.isValid()
            or QgsProject.instance().mapLayer(layer.id()) is not layer):
        return
    setting = _dock_default()
    default = setting.valueAsVariant() if setting is not None else None
    try:
        for was_docked in docked:
            if setting is not None:
                setting.setVariantValue(was_docked)
            iface.showAttributeTable(layer)
    finally:
        if setting is not None:
            setting.setVariantValue(default)


@contextmanager
def tables_closed(layer):


    docked = close_tables(layer)
    try:
        yield
    finally:
        if docked:
            reopen_tables(layer, docked)

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later












from __future__ import annotations

from qgis.core import QgsProject

from ..core import layout_show
from ..core.logger import log_warning
from ..core.qt_compat import qgis_iface
from ..core.result_objects import text_of


def _layout(name: str):
    return QgsProject.instance().layoutManager().layoutByName(name)


def is_report(name: str) -> bool:

    layout = _layout(name)
    return layout is not None and getattr(layout, "pageCollection", None) is None


def _view_window(name: str):

    from ..tools.map_3d import open_3d_views

    for canvas in open_3d_views() or ():
        holder = canvas.parent() if hasattr(canvas, "parent") else None
        title = holder.title() if hasattr(holder, "title") else ""
        if title == name:
            return holder
    iface = qgis_iface()
    if iface is None:
        return None
    from qgis.PyQt.QtWidgets import QDockWidget

    for dock in iface.mainWindow().findChildren(QDockWidget):
        if dock.windowTitle() == name and dock.isVisible():
            return dock
    return None


def _reopen_action(name: str):




    iface = qgis_iface()
    if iface is None:
        return None
    from qgis.PyQt.QtWidgets import QMenu

    for menu in iface.mainWindow().findChildren(QMenu):
        if menu.objectName() != "m3DMapViewsMenu":
            continue

        menu.aboutToShow.emit()
        for action in menu.actions():
            if action.text().replace("&", "") == name:
                return action
    return None


def exists(entry: dict) -> bool:

    try:
        kind, name = entry.get("kind"), text_of(entry.get("name"))
        project = QgsProject.instance()
        if kind == "layout":
            return _layout(name) is not None
        if kind == "theme":
            return project.mapThemeCollection().hasMapTheme(name)
        if kind == "table":
            return project.mapLayer(text_of(entry.get("id"))) is not None
        if kind == "view3d":
            return _view_window(name) is not None or _reopen_action(name) is not None
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Run object not checked {entry!r}: {exc}")
    return False


def bring_back(entry: dict) -> bool:

    if not exists(entry):
        return False
    iface = qgis_iface()
    if iface is None:
        return False
    kind, name = entry.get("kind"), text_of(entry.get("name"))
    try:
        project = QgsProject.instance()
        if kind == "layout":
            if not layout_show.show(name):

                return iface.openLayoutDesigner(_layout(name)) is not None
            return True
        if kind == "table":
            iface.showAttributeTable(project.mapLayer(text_of(entry.get("id"))))
            return True
        if kind == "theme":
            root = project.layerTreeRoot()
            project.mapThemeCollection().applyTheme(name, root, iface.layerTreeView().layerTreeModel())
            iface.mapCanvas().refresh()
            return True
        if kind == "view3d":
            holder = _view_window(name)
            if holder is not None:
                if hasattr(holder, "showNormal") and not hasattr(holder, "setFloating"):
                    holder.showNormal()
                holder.raise_()
                activate = getattr(holder, "requestActivate", None) or getattr(holder, "activateWindow", None)
                if callable(activate):
                    activate()
                return True
            action = _reopen_action(name)
            if action is None:
                return False
            if action.isChecked():
                action.setChecked(False)
            action.trigger()
            return True
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Run object not shown {entry!r}: {exc}")
    return False

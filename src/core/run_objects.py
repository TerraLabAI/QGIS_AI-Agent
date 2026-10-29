# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

from qgis.core import QgsProject

from . import layout_show
from .logger import log_warning

KINDS = ("layout", "view3d", "table", "theme")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def result_objects(name: str, args, result) -> list[dict]:

    try:
        if not isinstance(result, dict) or "_error" in result:
            return []
        out: list[dict] = []

        def add(kind, label, ident="") -> None:
            label = _text(label)
            if kind in KINDS and label:
                out.append({"kind": kind, "name": label, **({"id": ident} if _text(ident) else {})})

        add("layout", layout_show.built_layout(name, args if isinstance(args, dict) else {}))
        stated = result.get("object")
        if isinstance(stated, dict):
            add(stated.get("kind"), stated.get("name"), stated.get("id"))
        changed = result.get("changed")
        for key in ("layouts_added", "layouts_changed"):
            names = changed.get(key) if isinstance(changed, dict) else None
            for label in (names if isinstance(names, dict) else ()):
                add("layout", label)
        return out
    except Exception:  # noqa: BLE001
        return []


def merge(kept: list, found: list) -> None:

    known = {(entry["kind"], entry.get("id") or entry["name"]) for entry in kept}
    for entry in found:
        key = (entry["kind"], entry.get("id") or entry["name"])
        if key not in known:
            known.add(key)
            kept.append(entry)


def _iface():
    try:
        from qgis.utils import iface
    except ImportError:
        return None
    return iface


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
    iface = _iface()
    if iface is None:
        return None
    from qgis.PyQt.QtWidgets import QDockWidget

    for dock in iface.mainWindow().findChildren(QDockWidget):
        if dock.windowTitle() == name and dock.isVisible():
            return dock
    return None


def _reopen_action(name: str):




    iface = _iface()
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
        kind, name = entry.get("kind"), _text(entry.get("name"))
        project = QgsProject.instance()
        if kind == "layout":
            return _layout(name) is not None
        if kind == "theme":
            return project.mapThemeCollection().hasMapTheme(name)
        if kind == "table":
            return project.mapLayer(_text(entry.get("id"))) is not None
        if kind == "view3d":
            return _view_window(name) is not None or _reopen_action(name) is not None
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Run object not checked {entry!r}: {exc}")
    return False


def bring_back(entry: dict) -> bool:

    if not exists(entry):
        return False
    iface = _iface()
    if iface is None:
        return False
    kind, name = entry.get("kind"), _text(entry.get("name"))
    try:
        project = QgsProject.instance()
        if kind == "layout":
            if not layout_show.show(name):

                return iface.openLayoutDesigner(_layout(name)) is not None
            return True
        if kind == "table":
            iface.showAttributeTable(project.mapLayer(_text(entry.get("id"))))
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

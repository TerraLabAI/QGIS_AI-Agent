# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

from ..core import siblings
from ..core.siblings import sibling_info
from .external_links import open_external_url


DOCK_MIN_HEIGHT = 420


def _give_room(dock) -> None:






    try:
        if dock.height() >= DOCK_MIN_HEIGHT:
            return
        window = dock.parent()
        if not callable(getattr(window, "resizeDocks", None)):
            from qgis.utils import iface
            window = iface.mainWindow() if iface is not None else None
        resize = getattr(window, "resizeDocks", None)
        if not callable(resize):
            return
        from qgis.PyQt.QtCore import Qt
        wanted = max(DOCK_MIN_HEIGHT, dock.sizeHint().height())
        resize([dock], [min(wanted, max(200, window.height() - 200))], Qt.Orientation.Vertical)
    except Exception:  # noqa: BLE001
        return


def activate_dock(plugin) -> bool:









    for attr in ("dock_widget", "_dock_widget", "dock"):
        dock = getattr(plugin, attr, None)
        if dock is None:
            continue
        try:
            dock.show()
            dock.raise_()


            if dock.isVisible():
                _give_room(dock)
                return True
        except Exception:  # noqa: BLE001
            continue  # nosec B112
    for attr in ("toggle_dock_widget", "show_dock_widget", "_toggle_dock", "run"):
        fn = getattr(plugin, attr, None)
        if callable(fn):
            try:
                fn()
                return True
            except Exception:  # noqa: BLE001
                continue  # nosec B112
    return False


def open_sibling(product_id: str) -> str:





    info = sibling_info(product_id)
    if not info:
        return ""
    _, plugin = siblings.find(siblings.by_product(product_id))
    if plugin is not None and activate_dock(plugin):
        return "opened"
    return open_plugin_manager(info["name"], info["url"])


def open_plugin_manager(plugin_name: str, fallback_url: str) -> str:




















    try:
        from qgis.PyQt.QtCore import QTimer
        from qgis.utils import iface

        if iface.pluginManagerInterface() is None:
            raise RuntimeError("plugin manager unavailable")
        QTimer.singleShot(0, lambda: _reveal_plugin(plugin_name))
        show_plugin_manager(0)
        return "manager"
    except Exception:  # noqa: BLE001
        open_external_url(fallback_url)
        return "website"


def open_plugin_manager_later(plugin_name: str, fallback_url: str) -> str:











    from qgis.PyQt.QtCore import QTimer

    if _plugin_manager_dialog() is not None:
        reveal_in_open_manager(plugin_name, tab=0, filter_text=True)
        return "revealed"
    QTimer.singleShot(0, lambda: open_plugin_manager(plugin_name, fallback_url))
    return "queued"


def show_plugin_manager(tab: int = 0) -> None:


















    from qgis.utils import iface

    try:
        import pyplugin_installer

        pyplugin_installer.instance().showPluginManagerWhenReady(int(tab))
        return
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    iface.pluginManagerInterface().showPluginManager(int(tab))


def _plugin_manager_dialog():
    from qgis.PyQt.QtWidgets import QApplication

    return next(
        (w for w in QApplication.instance().topLevelWidgets()
         if w.metaObject().className() == "QgsPluginManager" and w.isVisible()),
        None,
    )


def _filter_edit(dialog):

    from qgis.PyQt.QtWidgets import QLineEdit

    for name in ("leFilter", "mLeFilter", "mFilterLineEdit"):
        edit = dialog.findChild(QLineEdit, name)
        if edit is not None:
            return edit
    try:
        from qgis.gui import QgsFilterLineEdit

        edit = next((e for e in dialog.findChildren(QgsFilterLineEdit) if e.isVisible()), None)
        if edit is not None:
            return edit
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    return next((e for e in dialog.findChildren(QLineEdit) if e.isVisible()), None)


def _select_row(dialog, plugin_name: str) -> bool:







    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QListView

    view = dialog.findChild(QListView, "vwPlugins")
    if view is None:
        return False
    model = view.model()
    if model is None:
        return False
    wanted = plugin_name.strip().casefold()
    found = None
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        label = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "").strip().casefold()
        if label == wanted:
            found = index
            break


    if found is None and model.rowCount() == 1:
        found = model.index(0, 0)
    if found is None:
        return False
    if view.currentIndex() == found and view.selectionModel().isSelected(found):
        return True
    view.setCurrentIndex(found)
    view.scrollTo(found)
    return True







_REVEAL_ATTEMPTS = 90
_REVEAL_MS = 140


def reveal_in_open_manager(plugin_name: str, tab: int = -1, filter_text: bool = False,
                           attempts: int = _REVEAL_ATTEMPTS, confirmed: int = 0) -> None:












    from qgis.PyQt.QtCore import QTimer
    from qgis.PyQt.QtWidgets import QListWidget

    try:
        dialog = _plugin_manager_dialog()
        if dialog is not None:


            if confirmed == 0 and tab >= 0:
                tabs = dialog.findChild(QListWidget, "mOptionsListWidget")
                if tabs is not None and tabs.currentRow() != tab:
                    tabs.setCurrentRow(tab)
            edit = _filter_edit(dialog) if filter_text else None
            if edit is not None and edit.text() != plugin_name:
                edit.setText(plugin_name)
                confirmed = 0
            elif _select_row(dialog, plugin_name):
                confirmed += 1
            else:
                confirmed = 0
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    if confirmed < 2 and attempts > 0:
        QTimer.singleShot(_REVEAL_MS, lambda: reveal_in_open_manager(
            plugin_name, tab, filter_text, attempts - 1, confirmed))


def _reveal_plugin(plugin_name: str) -> None:

    reveal_in_open_manager(plugin_name, tab=0, filter_text=True)

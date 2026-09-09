# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

from collections.abc import Mapping

from qgis.PyQt.QtGui import QIcon

from ..core import catalog
from .external_links import open_external_url
from .shared import PRODUCT_ID, SITE_URL, QAction











_SIBLINGS_LOCAL: dict[str, dict] = {
    "ai-edit": {"keys": ("AI_Edit", "QGIS_AI-Edit"), "folder": "AI_Edit"},
    "ai-segmentation": {"keys": ("AI_Segmentation", "QGIS_AI-Segmentation"), "folder": "AI_Segmentation"},
}


def _tagged(url: str, content: str) -> str:






    if not url or not url.startswith(SITE_URL):
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}utm_source=qgis&utm_medium=plugin&utm_campaign={PRODUCT_ID}&utm_content={content}"


def sibling_info(product_id: str) -> dict | None:





    local = _SIBLINGS_LOCAL.get(product_id)
    if local is None:
        return None
    served = catalog.plugin_roster().get(product_id) or {}
    return {
        **local,
        "product_id": product_id,
        "name": served.get("name") or "",
        "label": served.get("label") or "",
        "url": _tagged(served.get("url") or "", "cross_promo"),
        "tutorial_url": _tagged(served.get("tutorial_url") or "", "cross_promo_guide"),
        "thumbnail_url": served.get("thumbnail_url") or "",
    }


class _SiblingsView(Mapping):








    def __getitem__(self, product_id):
        info = sibling_info(product_id)
        if info is None:
            raise KeyError(product_id)
        return info

    def __iter__(self):
        return iter(_SIBLINGS_LOCAL)

    def __len__(self):
        return len(_SIBLINGS_LOCAL)


SIBLINGS = _SiblingsView()












_QUICKMAPSERVICES_LOCAL = {"keys": ("quick_map_services", "QuickMapServices"), "folder": "quick_map_services"}


def quickmapservices_info() -> dict:

    served = catalog.plugin_roster().get("quickmapservices") or {}
    return {**_QUICKMAPSERVICES_LOCAL, "name": served.get("name") or "QuickMapServices",
            "url": served.get("url") or ""}


def _find_installed_plugin(keys: tuple[str, ...]):
    try:
        import qgis.utils
        for key in keys:
            plugin = qgis.utils.plugins.get(key)
            if plugin is not None:
                return plugin
        for name, plugin in qgis.utils.plugins.items():
            if plugin is not None and name.startswith(keys):
                return plugin
    except Exception:  # noqa: BLE001
        pass  # nosec B110
    return None


def is_sibling_installed(product_id: str) -> bool:
    local = _SIBLINGS_LOCAL.get(product_id)
    return bool(local and _find_installed_plugin(local["keys"]) is not None)



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


def _activate_dock(plugin) -> bool:









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


def open_sibling_page(product_id: str) -> None:

    sibling = SIBLINGS.get(product_id)
    if sibling and sibling["url"]:
        open_external_url(sibling["url"])


def open_sibling_tutorial(product_id: str) -> None:

    sibling = SIBLINGS.get(product_id)
    if sibling and sibling["tutorial_url"]:
        open_external_url(sibling["tutorial_url"])


def open_sibling(product_id: str) -> str:





    sibling = SIBLINGS.get(product_id)
    if not sibling:
        return ""
    plugin = _find_installed_plugin(sibling["keys"])
    if plugin is not None and _activate_dock(plugin):
        return "opened"
    return open_plugin_manager(sibling["name"], sibling["url"])


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


def make_sibling_action(parent, iface, product_id: str, label: str, tooltip: str,
                        icon: QIcon | None = None) -> QAction:


    del iface
    action = QAction(icon or QIcon(), label, parent)
    action.setToolTip(tooltip)
    action.triggered.connect(lambda: open_sibling(product_id))
    return action



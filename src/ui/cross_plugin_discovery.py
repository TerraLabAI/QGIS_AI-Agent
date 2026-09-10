# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cross-plugin discovery: expose the sibling TerraLab plugins in the UI."""










from __future__ import annotations

from qgis.PyQt.QtGui import QIcon

from .external_links import open_external_url
from .shared import QAction, build_utm_url









SIBLINGS = {
    "ai-edit": {
        "keys": ("AI_Edit", "QGIS_AI-Edit"),
        "folder": "AI_Edit",
        "name": "AI Edit by TerraLab",
        "label": "AI Edit",
        "url": build_utm_url("/ai-edit", "cross_promo"),
        "tutorial_url": build_utm_url("/blog/ai-edit-complete-guide", "cross_promo_guide"),
        "thumbnail_url": "https://terra-lab.ai/blog/ai-edit-complete-guide/og.jpg",
    },
    "ai-segmentation": {
        "keys": ("AI_Segmentation", "QGIS_AI-Segmentation"),
        "folder": "AI_Segmentation",
        "name": "AI Segmentation by TerraLab",
        "label": "AI Segmentation",
        "url": build_utm_url("/ai-segmentation", "cross_promo"),
        "tutorial_url": build_utm_url("/blog/ai-segmentation-complete-guide",
                                      "cross_promo_guide"),
        "thumbnail_url": "https://terra-lab.ai/blog/ai-segmentation-complete-guide/og.jpg",
    },
}










QUICKMAPSERVICES = {
    "keys": ("quick_map_services", "QuickMapServices"),
    "folder": "quick_map_services",
    "name": "QuickMapServices",
    "url": "https://plugins.qgis.org/plugins/quick_map_services/",
}


def is_quickmapservices_installed() -> bool:
    """True when QuickMapServices is installed *and* started in this QGIS."""





    return _find_installed_plugin(QUICKMAPSERVICES["keys"]) is not None


def install_quickmapservices() -> str:
    """Open the plugin manager on the QuickMapServices card. ``"manager"`` or ``"website"``."""
    return open_plugin_manager(QUICKMAPSERVICES["name"], QUICKMAPSERVICES["url"])


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
    sibling = SIBLINGS.get(product_id)
    return bool(sibling and _find_installed_plugin(sibling["keys"]) is not None)


def _activate_dock(plugin) -> bool:
    """Ensure a sibling plugin's dock widget is visible."""








    for attr in ("dock_widget", "_dock_widget", "dock"):
        dock = getattr(plugin, attr, None)
        if dock is None:
            continue
        try:
            dock.show()
            dock.raise_()


            if dock.isVisible():
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
    """Open the sibling's product page in the browser, always the website."""
    sibling = SIBLINGS.get(product_id)
    if sibling:
        open_external_url(sibling["url"])


def open_sibling_tutorial(product_id: str) -> None:
    """Open the sibling's written guide on the blog."""
    sibling = SIBLINGS.get(product_id)
    if sibling:
        open_external_url(sibling["tutorial_url"])


def open_ai_edit_page() -> None:
    open_sibling_page("ai-edit")


def open_ai_segmentation_page() -> None:
    open_sibling_page("ai-segmentation")


def open_sibling(product_id: str) -> str:
    """Open the sibling if it is installed, else the plugin manager on its card."""




    sibling = SIBLINGS.get(product_id)
    if not sibling:
        return ""
    plugin = _find_installed_plugin(sibling["keys"])
    if plugin is not None and _activate_dock(plugin):
        return "opened"
    return open_plugin_manager(sibling["name"], sibling["url"])


def open_plugin_manager(plugin_name: str, fallback_url: str) -> str:
    """Open the QGIS Plugin Manager on ``plugin_name``'s own card."""



















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


def show_plugin_manager(tab: int = 0) -> None:
    """Show the manager on ``tab``, fetching the repository list first."""

















    from qgis.utils import iface

    try:
        import pyplugin_installer

        pyplugin_installer.instance().showPluginManagerWhenReady(int(tab))
        return
    except Exception:  # noqa: BLE001
        pass  # nosec B110 - fall through to the plain window
    iface.pluginManagerInterface().showPluginManager(int(tab))


def _plugin_manager_dialog():
    from qgis.PyQt.QtWidgets import QApplication

    return next(
        (w for w in QApplication.instance().topLevelWidgets()
         if w.metaObject().className() == "QgsPluginManager" and w.isVisible()),
        None,
    )


def _filter_edit(dialog):
    """The manager's search field. Named ``leFilter``, with fallbacks."""
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
    """Select the row named ``plugin_name`` in the manager's plugin list."""






    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import QListView

    view = dialog.findChild(QListView, "vwPlugins")
    if view is None:
        return False
    model = view.model()
    if model is None:
        return False
    wanted = plugin_name.strip().casefold()
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        label = str(model.data(index, Qt.ItemDataRole.DisplayRole) or "").strip().casefold()
        if label != wanted:
            continue
        if view.currentIndex() == index and view.selectionModel().isSelected(index):
            return True
        view.setCurrentIndex(index)
        view.scrollTo(index)
        return True
    return False







_REVEAL_ATTEMPTS = 90
_REVEAL_MS = 140


def reveal_in_open_manager(plugin_name: str, tab: int = -1, filter_text: bool = False,
                           attempts: int = _REVEAL_ATTEMPTS, confirmed: int = 0) -> None:
    """Select ``plugin_name``'s row in the Plugin Manager, once it is up."""











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
        pass  # nosec B110 - landing on the card is a nicety, the window is open
    if confirmed < 2 and attempts > 0:
        QTimer.singleShot(_REVEAL_MS, lambda: reveal_in_open_manager(
            plugin_name, tab, filter_text, attempts - 1, confirmed))


def _reveal_plugin(plugin_name: str) -> None:
    """Filter the manager to ``plugin_name`` on the All tab and select its row."""
    reveal_in_open_manager(plugin_name, tab=0, filter_text=True)


def make_sibling_action(parent, iface, product_id: str, label: str, tooltip: str,
                        icon: QIcon | None = None) -> QAction:
    """A QAction that opens the sibling if installed, else the Plugin Manager on its card (product page as a last-resort fallback)."""

    del iface
    action = QAction(icon or QIcon(), label, parent)
    action.setToolTip(tooltip)
    action.triggered.connect(lambda: open_sibling(product_id))
    return action


def make_ai_edit_action(parent, iface, label: str, tooltip: str,
                        icon: QIcon | None = None) -> QAction:
    return make_sibling_action(parent, iface, "ai-edit", label, tooltip, icon)


def make_ai_segmentation_action(parent, iface, label: str, tooltip: str,
                                icon: QIcon | None = None) -> QAction:
    return make_sibling_action(parent, iface, "ai-segmentation", label, tooltip, icon)

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







































from __future__ import annotations

from qgis.core import QgsProject
from qgis.PyQt.QtCore import QCoreApplication, QEvent

from .logger import log_warning

_CONNECTED = False


def _designers() -> list:
    try:
        from qgis.utils import iface

        return list(iface.openLayoutDesigners()) if iface is not None else []
    except Exception:  # noqa: BLE001
        return []


def open_layouts() -> list[str]:

    names = []
    for designer in _designers():
        try:

            layout = designer.masterLayout()
            if layout is not None:
                names.append(str(layout.name()))
        except Exception:  # noqa: BLE001  # nosec B112
            continue
    return names


def close_designers() -> int:

    closed = 0
    for designer in _designers():
        try:
            window = designer.window()
            designer.close()



            if window is not None:
                QCoreApplication.sendPostedEvents(window, QEvent.Type.DeferredDelete)
            closed += 1
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Layout designer not closed before the project was cleared: {exc}")
    return closed


def reopen(names) -> int:

    opened = 0
    try:
        from qgis.utils import iface

        manager = QgsProject.instance().layoutManager()
        for name in names or ():
            layout = manager.layoutByName(name)
            if layout is not None and iface is not None:
                iface.openLayoutDesigner(layout)
                opened += 1
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layout designer not reopened: {exc}")
    return opened


def quiet_atlas(designer) -> bool:






    try:
        from qgis.gui import QgsMapLayerComboBox
        from qgis.PyQt import sip
        from qgis.PyQt.QtWidgets import QComboBox

        window = designer.window()


        found = window.findChild(QComboBox, "mAtlasCoverageLayerComboBox") if window else None
        if found is None or not found.inherits("QgsMapLayerComboBox"):
            return False
        combo = found if isinstance(found, QgsMapLayerComboBox) else sip.cast(found, QgsMapLayerComboBox)
        master = designer.masterLayout()
        atlas = master.atlas() if master is not None and hasattr(master, "atlas") else None
        if combo.allowEmptyLayer() or (atlas is not None and atlas.enabled()):
            return False
        combo.setAllowEmptyLayer(True)
        return True
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layout designer atlas combo left as it is: {exc}")
        return False


def _iface():
    try:
        from qgis.utils import iface

        return iface
    except Exception:  # noqa: BLE001
        return None


def install() -> None:


    global _CONNECTED
    if _CONNECTED:
        return
    try:
        QgsProject.instance().aboutToBeCleared.connect(close_designers)
        _CONNECTED = True
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layout designer guard not installed: {exc}")
    iface = _iface()
    try:
        if iface is not None:
            iface.layoutDesignerOpened.connect(quiet_atlas)
        for designer in _designers():
            quiet_atlas(designer)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layout designer atlas guard not installed: {exc}")


def shutdown() -> None:

    global _CONNECTED
    if not _CONNECTED:
        return
    try:
        QgsProject.instance().aboutToBeCleared.disconnect(close_designers)
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layout designer guard not disconnected: {exc}")
    iface = _iface()
    if iface is not None:
        try:
            iface.layoutDesignerOpened.disconnect(quiet_atlas)
        except Exception:  # nosec B110
            pass
    _CONNECTED = False

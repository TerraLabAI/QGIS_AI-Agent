# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

from qgis.core import QgsProject

from .logger import log_warning
from .tool_registry import spec


def _layout_named(args, key: str) -> str:
    value = args.get(key) if key and isinstance(args, dict) else None
    return value.strip() if isinstance(value, str) else ""


def built_layout(name: str, args) -> str:

    return _layout_named(args, getattr(spec(name), "builds_layout_at", ""))


def removed_layout(name: str, args) -> str:

    return _layout_named(args, getattr(spec(name), "removes_layout_at", ""))


def _master_name(designer) -> str:
    try:
        master = designer.masterLayout()
        return master.name() if master is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def show(layout_name: str) -> str:




    if not layout_name:
        return ""
    try:
        from qgis.utils import iface
    except ImportError:
        return ""
    if iface is None:
        return ""
    try:
        layout = QgsProject.instance().layoutManager().layoutByName(layout_name)

        if layout is None or getattr(layout, "pageCollection", None) is None:
            return ""


        already = any(_master_name(designer) == layout.name()
                      for designer in (iface.openLayoutDesigners() or ()))
        designer = iface.openLayoutDesigner(layout)
        if designer is None:
            return ""
        if already:
            return "raised"
        view = designer.view() if hasattr(designer, "view") else None
        if view is not None:
            view.zoomFull()
        return "opened"
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Layout designer not shown for {layout_name!r}: {exc}")
        return ""

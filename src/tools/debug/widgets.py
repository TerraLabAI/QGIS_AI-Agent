# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import os
from typing import Any

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QAbstractScrollArea,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QListView,
    QListWidget,
    QMenu,
    QPlainTextEdit,
    QRadioButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabBar,
    QTableView,
    QTabWidget,
    QTextEdit,
    QToolBox,
    QTreeView,
    QWidget,
)

from ...core.tool_registry import tool_error
from .._images import (  # noqa: F401
    image_to_base64,
    normalize_fmt,
    scale_image,
)
from .._widgets import (  # noqa: F401
    _is_password_field,
    cap_text,
    descendants,
    find_plugin,
    fingerprint_marker,
    geometry,
    is_alive,
    label_and_tooltip,
    plugin_widgets,
    process_events,
    redact_secrets,
    safe_call,
    top_level_roots,
    widget_ident,
    widget_label,
    widget_path,
    widget_summary,
    widget_text,
)


def grab_widget(
    widget: QWidget,
    max_width: int,
    save_path: str = "",
    fmt: str = "png",
    quality: int = 95,
    full_page: bool = False,
    pixmap=None,
) -> dict:









    target = widget
    scrolled = ""
    if full_page:
        area = widget if isinstance(widget, QScrollArea) else nearest_scroll_area(widget)
        content = area.widget() if isinstance(area, QScrollArea) else None
        if content is not None and content.height() > 0:
            target = content
            scrolled = area.metaObject().className()
    if target.width() <= 0 or target.height() <= 0:
        return tool_error("Widget has no size")
    fmt = normalize_fmt(fmt)
    if pixmap is None:
        pixmap = target.grab()
    image = scale_image(pixmap.toImage(), max_width)
    out = {"width": image.width(), "height": image.height(), "format": fmt}
    if scrolled:
        out["full_page"] = True
        out["scroll_area"] = scrolled
    if save_path:


        ext = os.path.splitext(save_path)[1].lstrip(".").lower()
        if ext:
            out["format"] = normalize_fmt(ext, ext)
        if not image.save(save_path):
            return tool_error(f"Could not write screenshot to {save_path}")
        out["saved_path"] = save_path
    else:
        out["image_base64"] = image_to_base64(image, fmt, quality)
    return out



_ROLE_TYPES = (
    (QAbstractButton, "button"),
    (QCheckBox, "checkbox"),
    (QRadioButton, "radio"),
    (QComboBox, "combobox"),
    (QLineEdit, "textbox"),
    (QPlainTextEdit, "textbox"),
    (QTextEdit, "textbox"),
    (QSpinBox, "spinbox"),
    (QDoubleSpinBox, "spinbox"),
    (QSlider, "slider"),
    (QTabBar, "tabs"),
    (QTabWidget, "tabs"),
    (QToolBox, "tabs"),
    (QListWidget, "list"),
    (QMenu, "menu"),


    (QAbstractScrollArea, "scrollarea"),
)


def item_view_role(view: QWidget) -> str:

    if isinstance(view, QTreeView):
        return "tree"
    if isinstance(view, QTableView):
        return "table"
    if isinstance(view, QListView):
        return "list"
    return "itemview"


def interactive_kind(widget: QWidget) -> str | None:





    if isinstance(widget, QCheckBox):
        return "checkbox"
    if isinstance(widget, QRadioButton):
        return "radio"
    if isinstance(widget, QComboBox):
        return "combobox"
    if isinstance(widget, QAbstractItemView):
        return item_view_role(widget)
    for cls, token in _ROLE_TYPES:
        if isinstance(widget, cls):
            return token
    return None




def tab_state(widget: QWidget) -> tuple[list[str], int]:





    if not isinstance(widget, (QTabBar, QTabWidget, QToolBox)):
        return [], -1
    getter = getattr(widget, "tabText", None) or getattr(widget, "itemText", None)
    if not callable(getter):
        return [], -1
    labels = []
    try:
        for i in range(widget.count()):
            labels.append(_clean_tab_text(getter(i)))
        return labels, widget.currentIndex()
    except Exception:
        return [], -1


def _clean_tab_text(text) -> str:
    return (str(text or "")).replace("&", "").strip()




def nearest_scroll_area(widget: QWidget) -> QAbstractScrollArea | None:

    node = widget.parentWidget() if widget is not None else None
    while node is not None:
        if isinstance(node, QAbstractScrollArea):
            return node
        node = node.parentWidget()
    return None


def scroll_state(area: QWidget) -> dict | None:





    if not isinstance(area, QAbstractScrollArea):
        return None
    try:
        bar = area.verticalScrollBar()
        viewport_h = area.viewport().height()
        if bar is None or bar.maximum() <= 0:
            return None
        return {
            "y": bar.value(),
            "max_y": bar.maximum(),
            "viewport_h": viewport_h,
            "content_h": bar.maximum() + viewport_h,
        }
    except Exception:
        return None




_ITEM_ROLES = {"tree": "treeitem", "list": "listitem", "table": "row", "itemview": "item"}


def item_path_of(index) -> str:

    parts = []
    node = index
    while node is not None and node.isValid():
        parts.append(str(node.row()))
        node = node.parent()
    return "/".join(reversed(parts))


def item_check_state(model, index):

    try:
        raw = model.data(index, Qt.ItemDataRole.CheckStateRole)
    except Exception:
        return None
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value == int(Qt.CheckState.PartiallyChecked):
        return "partial"
    return value == int(Qt.CheckState.Checked)


def _cell_text(model, index, max_text: int) -> str:
    try:
        raw = model.data(index, Qt.ItemDataRole.DisplayRole)
    except Exception:
        return ""
    if raw is None:
        return ""
    return cap_text(redact_secrets(str(raw)), max_text)


def _item_row_node(view, model, index, path: str, role_token: str, columns: int, max_text: int) -> dict | None:

    from .refs import get_ref_store

    text = _cell_text(model, index, max_text)
    checked = item_check_state(model, index)
    children = model.rowCount(index)
    if not text and checked is None and not children:


        return None
    node: dict[str, Any] = {
        "ref": get_ref_store().assign_item(view, index),
        "path": path,
        "role": role_token,
    }
    if columns > 1:
        cells = [text]
        for col in range(1, columns):
            cells.append(_cell_text(model, index.sibling(index.row(), col), max_text))
        while cells and not cells[-1]:
            cells.pop()
        node["cells"] = cells
    elif text:
        node["text"] = text
    if checked is not None:
        node["checked"] = checked
    if children:
        node["children"] = children
        if hasattr(view, "isExpanded"):
            node["expanded"] = bool(view.isExpanded(index))
    try:
        if not (int(model.flags(index)) & int(Qt.ItemFlag.ItemIsEnabled)):
            node["enabled"] = False
    except Exception:  # nosec B110
        pass
    try:
        selection = view.selectionModel()
        if selection is not None and selection.isSelected(index):
            node["selected"] = True
        if view.currentIndex() == index:
            node["current"] = True
    except Exception:  # nosec B110
        pass
    return node


def item_view_rows(view: QWidget, max_rows: int = 50, max_text: int = 0) -> tuple[list[dict], int, bool]:





    model = view.model() if hasattr(view, "model") else None
    if model is None:
        return [], 0, False
    role_token = _ITEM_ROLES.get(item_view_role(view), "item")
    columns = min(max(model.columnCount(), 1), 8)
    rows: list[dict] = []
    truncated = False
    is_tree = hasattr(view, "isExpanded")

    def walk(parent, prefix: list[int]):
        nonlocal truncated
        for row in range(model.rowCount(parent)):
            if len(rows) >= max_rows:
                truncated = True
                return
            index = model.index(row, 0, parent)
            if not index.isValid():
                continue
            path = prefix + [row]
            node = _item_row_node(view, model, index, "/".join(str(p) for p in path),
                                  role_token, columns, max_text)
            if node is not None:
                rows.append(node)
            if is_tree and view.isExpanded(index):
                walk(index, path)
                if truncated:
                    return

    root = view.rootIndex()
    walk(root, [])
    return rows, model.rowCount(root), truncated


def widget_value(widget: QWidget) -> Any:





    if isinstance(widget, QComboBox):
        return redact_secrets(widget.currentText())
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, QLineEdit):
        raw = widget.text()
        if _is_password_field(widget):



            return fingerprint_marker(raw) if raw else ""
        return redact_secrets(raw)
    if isinstance(widget, (QPlainTextEdit, QTextEdit)):
        try:
            return redact_secrets(widget.toPlainText())
        except Exception:
            return None
    return None


def widget_node(
    widget: QWidget,
    ref: str,
    include_geometry: bool = False,
    max_text: int = 0,
    max_rows: int = 50,
) -> dict:












    node: dict[str, Any] = {"ref": ref, "class": widget.metaObject().className()}
    role = interactive_kind(widget)
    if role:
        node["role"] = role
    name = safe_call(widget, "objectName")
    if name:
        node["name"] = name


    text, tip = label_and_tooltip(widget, max_text)
    if text and text != name:
        node["text"] = text
    if tip:
        node["tooltip"] = tip
    if not widget.isEnabled():
        node["enabled"] = False
    if not widget.isVisible():
        node["visible"] = False
    if isinstance(widget, QAbstractButton) and widget.isCheckable():
        node["checked"] = widget.isChecked()
    value = widget_value(widget)
    if value not in (None, ""):
        if isinstance(value, str):
            value = cap_text(value, max_text)
        node["value"] = value


        if node.get("text") == value:
            node.pop("text")
    is_ro = getattr(widget, "isReadOnly", None)
    if callable(is_ro):
        try:
            if is_ro():
                node["read_only"] = True
        except Exception:  # nosec B110
            pass
    if widget.hasFocus():
        node["focused"] = True
    tabs, current_tab = tab_state(widget)
    if tabs:
        node["tabs"] = tabs
        node["current"] = current_tab
    scroll = scroll_state(widget)
    if scroll:
        node["scroll"] = scroll
    if isinstance(widget, QAbstractItemView) and max_rows > 0:
        rows, row_count, truncated = item_view_rows(widget, max_rows, max_text)
        node["row_count"] = row_count
        if rows:
            node["rows"] = rows
        if truncated:
            node["more"] = True
    if include_geometry:
        node["geometry"] = geometry(widget)
    return node


def matches(widget: QWidget, query: str | None, widget_type: str | None = None) -> bool:
    if widget_type and widget_type.lower() not in widget.metaObject().className().lower():
        return False
    if not query:
        return True
    haystack = " ".join((
        widget.metaObject().className(),
        widget_text(widget),
        widget_path(widget),
    )).lower()
    return query.lower() in haystack




__all__ = [
    "find_plugin",
    "is_alive",
    "plugin_widgets",
    "widget_label",
    "widget_summary",
]

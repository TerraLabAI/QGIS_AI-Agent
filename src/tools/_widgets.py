# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plugin lookup and widget text helpers shared by the public tools and debug/."""






from __future__ import annotations

import hashlib
import re
from typing import Any

from qgis.PyQt.QtCore import QPoint, QRect
from qgis.PyQt.QtWidgets import QApplication, QWidget
from qgis.utils import iface, plugins

from ..core.qt_compat import enum_member



AI_EDIT_KEYS = ("QGIS_AI-Edit-Team", "QGIS_AI-Edit", "AI_Edit", "ai_edit")
AI_SEGMENT_KEYS = (
    "QGIS_AI-Segmentation-Team",
    "QGIS_AI-Segmentation",
    "QGIS_AI_Segmentation_Team",
    "AI_Segmentation",
    "ai_segmentation",
)


def _app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication is not available")
    return app


def process_events():
    _app().processEvents()


def is_alive(widget: QWidget | None) -> bool:
    """True if the C++ object behind a wrapped widget still exists."""
    if widget is None:
        return False
    try:
        from qgis.PyQt import sip

        if sip.isdeleted(widget):
            return False
    except Exception:  # nosec B110 - deleted Qt object is unavailable
        pass
    try:
        widget.objectName()
    except RuntimeError:
        return False
    return True


def safe_call(obj: Any, method: str) -> str:
    fn = getattr(obj, method, None)
    if not callable(fn):
        return ""
    try:
        value = fn()
    except Exception as err:


        return f"<error: {err.__class__.__name__}: {err}>"
    if value is None:
        return ""
    return str(value)


def cap_text(text: str, max_text: int = 0) -> str:
    """Cut text only when the caller asked for a cut, and say how much was cut."""
    if max_text and len(text) > max_text:
        return text[:max_text] + f"... [+{len(text) - max_text} chars]"
    return text











_SECRET_RE = re.compile(
    r"(tl_[A-Za-z0-9]{8,}"
    r"|sk-[A-Za-z0-9_\-]{8,}"
    r"|eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}"
    r"|Bearer\s+[A-Za-z0-9_\-\.]{8,})"
)


def secret_fingerprint(text: str) -> dict:
    """Identify a secret without emitting it."""




    raw = text or ""
    return {
        "prefix": raw[:3],
        "length": len(raw),
        "sha256_8": hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:8],
    }


def fingerprint_marker(text: str) -> str:
    fp = secret_fingerprint(text)
    return (
        f"***REDACTED***[prefix={fp['prefix']} length={fp['length']} "
        f"sha256_8={fp['sha256_8']}]"
    )


def _replace_secret(match) -> str:
    value = match.group(0)
    if value[:6].lower() == "bearer":


        parts = value.split(None, 1)
        return "Bearer " + fingerprint_marker(parts[1] if len(parts) > 1 else "")
    return fingerprint_marker(value)


def redact_secrets(text: str) -> str:
    if not text:
        return text
    return _SECRET_RE.sub(_replace_secret, text)


def _is_password_field(widget: QWidget) -> bool:
    try:
        from qgis.PyQt.QtWidgets import QLineEdit
        return isinstance(widget, QLineEdit) and widget.echoMode() != enum_member(QLineEdit, "EchoMode", "Normal")
    except Exception:
        return False


def widget_text(widget: QWidget) -> str:
    if _is_password_field(widget):

        parts = [safe_call(widget, "placeholderText"), safe_call(widget, "accessibleName"),
                 safe_call(widget, "objectName"), safe_call(widget, "toolTip")]
        return redact_secrets(" ".join(p.strip() for p in parts if p and p.strip()))
    parts = [
        safe_call(widget, "text"),
        safe_call(widget, "currentText"),
        safe_call(widget, "placeholderText"),
        safe_call(widget, "windowTitle"),
        safe_call(widget, "title"),
        safe_call(widget, "accessibleName"),
        safe_call(widget, "objectName"),
        safe_call(widget, "toolTip"),
    ]
    return redact_secrets(" ".join(part.strip() for part in parts if part and part.strip()))


def widget_label(widget: QWidget, max_text: int = 0) -> str:
    return cap_text(widget_text(widget), max_text)


def displayed_text(widget: QWidget, max_text: int = 0) -> str:
    """The text a user actually reads on the widget."""






    if _is_password_field(widget):
        return cap_text(redact_secrets(safe_call(widget, "placeholderText").strip()), max_text)
    parts = [
        safe_call(widget, "text"),
        safe_call(widget, "currentText"),
        safe_call(widget, "placeholderText"),
        safe_call(widget, "windowTitle"),
        safe_call(widget, "title"),
        safe_call(widget, "accessibleName"),
    ]
    seen: list[str] = []
    for part in parts:
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return cap_text(redact_secrets(" ".join(seen)), max_text)


def label_and_tooltip(widget: QWidget, max_text: int = 0) -> tuple[str, str]:
    """(label, tooltip) with the tooltip never repeated inside the label."""




    text = displayed_text(widget, max_text)
    tip = cap_text(redact_secrets((safe_call(widget, "toolTip") or "").strip()), max_text)
    if not text and tip:
        return tip, ""
    if tip and tip.lower() == text.lower():
        return text, ""
    return text, tip


def widget_path(widget: QWidget) -> str:
    parts = []
    current = widget
    while current is not None:
        label = current.metaObject().className()
        obj_name = safe_call(current, "objectName")
        text = widget_label(current)
        if obj_name:
            label += f"#{obj_name}"
        if text and text != obj_name:
            label += f"[{text[:60]}]"
        parts.append(label)
        current = current.parentWidget()
    return " / ".join(reversed(parts))


def geometry(widget: QWidget) -> dict:
    rect = widget.geometry()
    try:
        global_top_left = widget.mapToGlobal(QPoint(0, 0))
        global_rect = QRect(global_top_left, rect.size())
    except Exception:
        global_rect = rect
    return {
        "x": rect.x(),
        "y": rect.y(),
        "width": rect.width(),
        "height": rect.height(),
        "global_x": global_rect.x(),
        "global_y": global_rect.y(),
    }


def widget_summary(widget: QWidget) -> dict:
    return {
        "class": widget.metaObject().className(),
        "object_name": safe_call(widget, "objectName"),
        "accessible_name": safe_call(widget, "accessibleName"),
        "text": widget_label(widget),
        "visible": widget.isVisible(),
        "enabled": widget.isEnabled(),
        "path": widget_path(widget),
        "geometry": geometry(widget),
    }


def widget_ident(widget: QWidget) -> dict:
    """Just enough to recognise which widget was acted on."""





    ident: dict[str, Any] = {"class": widget.metaObject().className()}
    name = safe_call(widget, "objectName")
    if name:
        ident["object_name"] = name
    text, _tip = label_and_tooltip(widget)
    if text and text != name:
        ident["text"] = text
    if not widget.isEnabled():
        ident["enabled"] = False
    if not widget.isVisible():
        ident["visible"] = False
    return ident


def top_level_roots() -> list[QWidget]:
    roots = [iface.mainWindow()]
    roots.extend(w for w in _app().topLevelWidgets() if w is not roots[0])
    return [w for w in roots if w is not None]


def descendants(root: QWidget) -> list[QWidget]:
    widgets = [root]
    widgets.extend(root.findChildren(QWidget))
    return widgets


def plugin_candidates(name: str) -> list[str]:
    normalized = (name or "").strip()
    lower = normalized.lower().replace("_", "-")
    if lower in ("ai-edit", "aiedit", "ai edit"):
        return list(AI_EDIT_KEYS)
    if lower in ("ai-segmentation", "aisegmentation", "ai segmentation", "ai-segment"):
        return list(AI_SEGMENT_KEYS)
    candidates = [normalized]
    candidates.extend(k for k in plugins if lower and lower in k.lower().replace("_", "-"))
    return [c for c in candidates if c]


def find_plugin(name: str):
    for key in plugin_candidates(name):
        plugin = plugins.get(key)
        if plugin is not None:
            return key, plugin
    return None, None


def plugin_widgets(plugin) -> list[QWidget]:
    widgets = []
    for attr in ("_dock_widget", "dock_widget", "_dock", "dock", "widget", "_widget"):
        value = getattr(plugin, attr, None)
        if isinstance(value, QWidget):
            widgets.append(value)
    return widgets

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Accessibility snapshot + ref-aware UI driving (Playwright-style)."""
from __future__ import annotations

import time

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QTabBar,
    QTabWidget,
    QTextEdit,
    QToolBox,
    QWidget,
)

from ...core.serialization import dump_json
from ...core.tool_registry import Tool, ToolRegistry
from ..adapters import get_adapter
from .refs import get_ref_store, is_item_ref
from .widgets import (
    candidate_widgets,
    descendants,
    grab_widget,
    index_from_item_path,
    interactive_kind,
    is_alive,
    item_check_state,
    item_path_of,
    process_events,
    redact_secrets,
    scroll_into_view,
    tab_rect_center,
    tab_state,
    top_level_roots,
    widget_ident,
    widget_node,
    widget_summary,
    widget_text,
    widget_value,
)


def register_snapshot_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="accessibility_snapshot",
        input_schema={
            "type": "object",
            "properties": {
                "plugin_name": {
                    "type": "string",
                },
                "root_query": {
                    "type": "string",
                },
                "root_ref": {
                    "type": "string",
                },
                "interactive_only": {"type": "boolean"},
                "include_geometry": {"type": "boolean"},
                "max_nodes": {"type": "integer"},
                "max_rows": {
                    "type": "integer",
                },
                "max_text": {
                    "type": "integer",
                },
                "diff_baseline": {
                    "type": "boolean",
                },
            },
            "required": [],
        },
        handler=_accessibility_snapshot,
    ))

    registry.register(Tool(
        name="ref_action",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "click", "double_click", "right_click", "set_text", "select", "select_tab", "check", "uncheck",
                        "expand", "collapse", "screenshot", "focus", "hover", "scroll"
                    ],
                },
                "ref": {
                    "type": "string",
                },
                "query": {"type": "string"},
                "occurrence": {"type": "integer"},
                "text": {"type": "string"},
                "value": {
                    "type": "string",
                },
                "option_index": {
                    "type": "integer",
                },
                "item_path": {
                    "type": "string",
                },
                "column": {
                    "type": "integer",
                },
                "match": {
                    "type": "string",
                    "enum": ["exact", "contains"],
                },
                "submit": {"type": "boolean"},
                "max_width": {
                    "type": "integer",
                },
                "full_page": {
                    "type": "boolean",
                },
                "format": {
                    "type": "string",
                    "enum": ["png", "jpeg"],
                },
                "quality": {"type": "integer"},
                "delta": {
                    "type": "integer",
                },
                "defer": {
                    "type": "boolean",
                },
                "programmatic": {
                    "type": "boolean",
                },
                "timeout_ms": {
                    "type": "integer",
                },
                "no_wait": {
                    "type": "boolean",
                },
                "verbose": {
                    "type": "boolean",
                },
            },
            "required": ["action"],
        },
        handler=_ref_action,
    ))

    registry.register(Tool(
        name="assert_ui",
        input_schema={
            "type": "object",
            "properties": {
                "condition": {
                    "type": "string",
                    "enum": [
                        "visible", "hidden", "enabled", "disabled", "editable", "checked", "unchecked", "focused",
                        "value_equals", "value_contains", "text_present", "text_absent", "count_equals"
                    ],
                },
                "ref": {
                    "type": "string",
                },
                "query": {
                    "type": "string",
                },
                "text": {
                    "type": "string",
                },
                "expected": {
                    "type": ["string", "integer", "number"],
                },
                "plugin_name": {"type": "string"},
                "root_query": {
                    "type": "string",
                },
                "timeout_ms": {
                    "type": "integer",
                },
            },
            "required": ["condition"],
        },
        handler=_assert_ui,
    ))

    registry.register(Tool(
        name="close_dialog",
        input_schema={
            "type": "object",
            "properties": {
                "all": {
                    "type": "boolean",
                },
                "defer": {
                    "type": "boolean",
                },
            },
            "required": [],
        },
        handler=_close_dialog,
    ))

    registry.register(Tool(
        name="select_menu_item",
        input_schema={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
            },
            "required": ["text"],
        },
        handler=_select_menu_item,
    ))

    registry.register(Tool(
        name="press_key",
        input_schema={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                },
                "ref": {"type": "string"},
                "query": {"type": "string"},
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["ctrl", "shift", "alt", "meta"]},
                },
                "defer": {
                    "type": "boolean",
                },
            },
            "required": ["key"],
        },
        handler=_press_key,
    ))


def _scope_roots(args: dict):
    plugin_name = args.get("plugin_name")
    root_query = args.get("root_query")
    if plugin_name:
        adapter = get_adapter(plugin_name)
        roots = adapter.snapshot_roots()
        if roots:
            return roots, adapter.display_name()

        return top_level_roots(), adapter.display_name()
    if root_query:
        return candidate_widgets(root_query, visible_only=True), root_query
    return top_level_roots(), "qgis_window"


def _collect(roots, interactive_only: bool, max_nodes: int):
    seen = set()
    widgets = []
    truncated = False
    for root in roots:
        if not isinstance(root, QWidget):
            continue
        for w in descendants(root):
            key = id(w)
            if key in seen:
                continue
            seen.add(key)
            if not w.isVisible():
                continue
            if interactive_only and interactive_kind(w) is None:
                continue
            widgets.append(w)
            if len(widgets) >= max_nodes:
                truncated = True
                return widgets, truncated
    return widgets, truncated


_DEFAULT_MAX_TEXT = 120
_SNAPSHOT_BUDGET = 24_000


def _accessibility_snapshot(args: dict) -> dict:
    interactive_only = args.get("interactive_only", True)
    include_geometry = args.get("include_geometry", False)
    max_nodes = min(max(int(args.get("max_nodes", 200) or 200), 1), 600)


    raw_max_text = args.get("max_text")
    max_text = _DEFAULT_MAX_TEXT if raw_max_text is None else max(int(raw_max_text or 0), 0)
    max_rows = min(max(int(args.get("max_rows", 50) or 0), 0), 500)
    process_events()

    root_ref = args.get("root_ref")
    if root_ref:
        widget, error = get_ref_store().resolve(root_ref)
        if error:
            return error
        roots, root_label = [widget], f"ref:{root_ref}"
    else:
        roots, root_label = _scope_roots(args)
    widgets, truncated = _collect(roots, interactive_only, max_nodes)

    store = get_ref_store()
    ref_map = store.assign(widgets)
    refs = list(ref_map.keys())


    nodes = []
    size = 0
    omitted = 0
    for i, w in enumerate(widgets):
        node = widget_node(w, refs[i], include_geometry, max_text, max_rows)
        size += len(dump_json(node)) + 1
        if nodes and size > _SNAPSHOT_BUDGET:
            omitted = len(widgets) - i
            break
        nodes.append(node)

    result = {
        "root": root_label,
        "count": len(nodes),
    }
    if omitted:
        result["more"] = True
        result["nodes_omitted"] = omitted
        result["hint"] = f"Stopped at {_SNAPSHOT_BUDGET:,} characters; narrow the scope (root_ref, root_query)."


    if not interactive_only:
        result["interactive_only"] = False
    if truncated:
        result["truncated"] = True
        result["hint"] = f"Stopped at max_nodes={max_nodes}; raise it or narrow the scope."
    if args.get("diff_baseline", False):
        result["diff"] = store.diff(root_label, nodes)
    else:
        result["snapshot"] = nodes
    return result


def _resolve_target(args: dict):
    """Return (widget, error). Prefer ref, fall back to query."""
    ref = args.get("ref")
    if ref:
        return get_ref_store().resolve(ref)
    if args.get("query"):
        from .widgets import resolve_widget
        return resolve_widget(args)
    return None, {"_error": "Provide either 'ref' (from accessibility_snapshot) or 'query'."}


def _expand_widget(result, widget, args: dict):
    """Put the full widget summary back when the caller asked for verbose."""
    if isinstance(result, dict) and args.get("verbose") and isinstance(result.get("widget"), dict):
        result["widget"] = widget_summary(widget)
    return result


def _resolve_item_target(args: dict):
    """(view, index, error) when the call targets an item-view row."""





    ref = (args.get("ref") or "").strip()
    if is_item_ref(ref):
        return get_ref_store().resolve_item(ref)
    path = args.get("item_path")
    if path in (None, ""):
        return None, None, None
    widget, error = _resolve_target(args)
    if error:
        return None, None, error
    if not isinstance(widget, QAbstractItemView):
        return None, None, {
            "_error": "item_path needs a tree/list/table target",
            "_code": "NOT_AN_ITEM_VIEW",
            "widget": widget_ident(widget),
        }
    index, error = index_from_item_path(widget, path)
    if error:
        return None, None, error
    return widget, index, None


def _ref_action(args: dict) -> dict:
    action = args["action"]
    view, index, item_error = _resolve_item_target(args)
    if item_error is not None:
        return item_error
    if view is not None:
        return _item_action(action, view, index, args)
    widget, error = _resolve_target(args)
    if error:
        return error


    rebound = bool(args.get("ref")) and get_ref_store().last_rebound is not None
    if action == "click" and args.get("defer"):



        from qgis.PyQt.QtCore import QTimer

        QTimer.singleShot(0, lambda w=widget: _do_click(w))
        out = {"clicked": True, "deferred": True, "widget": widget_ident(widget),
               "note": "Wait ~1s, then inspect with accessibility_snapshot/screenshot; dismiss with close_dialog."}
        if rebound:
            out["rebound"] = True
        return _expand_widget(out, widget, args)




    notes: dict = {}
    if action in _ACTIONABLE_ACTIONS and not args.get("no_wait"):
        timeout_ms = min(max(int(args.get("timeout_ms", 2000) or 2000), 0), 20000)
        not_ready, notes = _wait_actionable(widget, action, timeout_ms)
        if not_ready is not None:
            return not_ready

    process_events()
    try:
        result = _dispatch_action(action, widget, args)
    except Exception as err:
        return {"_error": f"{action} failed: {err}", "widget": widget_ident(widget)}
    if result is None:
        return {"_error": f"Unsupported action: {action}"}





    if (action in _MUTATING_ACTIONS and isinstance(result, dict)
            and "_error" not in result and not result.get("menu_opened")):
        loop = _check_no_progress(action, widget, args)
        if loop is not None:
            if loop.get("stop"):
                return loop["error"]
            result["_warning"] = loop["message"]
    if isinstance(result, dict) and "_error" not in result:
        if rebound:
            result["rebound"] = True
        result.update(notes)
    return _expand_widget(result, widget, args)


def _dispatch_action(action: str, widget, args: dict):
    if action == "click":
        return _do_click(widget, programmatic=args.get("programmatic", False))
    if action == "double_click":
        return _do_click(widget, kind="double")
    if action == "right_click":
        return _do_click(widget, kind="right")
    if action == "focus":
        widget.setFocus(Qt.FocusReason.OtherFocusReason)
        process_events()
        return {"focused": True, "widget": widget_ident(widget)}
    if action == "set_text":
        return _do_set_text(widget, args.get("text", ""), args.get("submit", False))
    if action == "select":
        return _do_select(widget, args.get("value", ""), args.get("match", "contains"), args.get("option_index"))
    if action == "select_tab":
        return _do_select_tab(widget, args)
    if action in ("check", "uncheck"):
        return _do_check_widget(widget, action == "check")
    if action in ("expand", "collapse"):
        return {
            "_error": f"'{action}' applies to a row of a tree, not to a widget.",
            "_code": "UNSUPPORTED_ACTION",
            "_suggestion": "Pass the row's @iN ref (from the view's `rows` in accessibility_snapshot), "
                           "or a view ref plus item_path.",
            "widget": widget_ident(widget),
        }
    if action == "screenshot":
        max_width = min(max(int(args.get("max_width", 1600) or 1600), 100), 4000)
        result = grab_widget(
            widget,
            max_width,
            fmt=args.get("format", "png"),
            quality=min(max(int(args.get("quality", 95) or 95), 10), 100),
            full_page=bool(args.get("full_page")),
        )
        if "_error" not in result:
            result["widget"] = widget_ident(widget)
        return result
    if action == "hover":
        from qgis.PyQt.QtTest import QTest

        widget.setFocus(Qt.FocusReason.MouseFocusReason)
        QTest.mouseMove(widget, widget.rect().center())
        process_events()
        return {"hovered": True, "widget": widget_ident(widget)}
    if action == "scroll":
        return _do_scroll(widget, int(args.get("delta", -300) or -300))
    return None




_MUTATING_ACTIONS = frozenset({"click", "set_text", "select", "select_tab", "check", "uncheck"})


def _target_sig(widget) -> str:
    try:
        return f"{widget.metaObject().className()}#{widget.objectName()}"
    except Exception:
        return str(id(widget))


def _extra_state(widget) -> str:
    """State the value/checked pair misses: the current row of a view, the current page of a tab bar."""


    try:
        if isinstance(widget, QAbstractItemView):
            model = widget.model()
            rows = model.rowCount(widget.rootIndex()) if model is not None else 0
            selection = widget.selectionModel()


            selected = int(selection.hasSelection()) if selection is not None else 0
            return f"{item_path_of(widget.currentIndex())}/{rows}/{selected}"
        if isinstance(widget, (QTabBar, QTabWidget, QToolBox)):
            return f"tab{widget.currentIndex()}/{widget.count()}"
    except Exception:
        return ""
    return ""


def _ui_fingerprint() -> str:
    """Cheap hash of every visible interactive widget's state in the window."""






    import hashlib

    parts = []
    for root in top_level_roots():
        if not isinstance(root, QWidget):
            continue
        for w in descendants(root):
            try:
                if not w.isVisible() or interactive_kind(w) is None:
                    continue
                checkable = getattr(w, "isCheckable", None)
                checked = w.isChecked() if (callable(checkable) and checkable()) else ""
                parts.append(
                    f"{w.metaObject().className()}|{w.objectName()}|{int(w.isEnabled())}"
                    f"|{widget_value(w)}|{checked}|{_extra_state(w)}"
                )
                if len(parts) >= 400:
                    break
            except Exception:  # nosec B112 - the debug report reads what answers and skips what does not
                continue
    return hashlib.md5("\n".join(parts).encode("utf-8", "ignore")).hexdigest()  # nosec B324 - a fingerprint


def _check_no_progress(action: str, widget, args: dict, extra: str = ""):
    """Return None, a {message} warning, or a {stop, error} abort signal."""
    try:
        key = f"{action}:{_target_sig(widget)}:{extra}:{args.get('text', '')}{args.get('value', '')}"
        fingerprint = _ui_fingerprint()
    except Exception:
        return None
    count = get_ref_store().note_action(key, fingerprint)
    if count >= 3:
        return {
            "stop": True,
            "error": {
                "_error": f"Aborting: '{action}' on this target repeated {count + 1}× with no UI change.",
                "_code": "NO_PROGRESS",
                "_suggestion": (
                    "This action has no effect. Re-run accessibility_snapshot, target a different "
                    "widget, check for a blocking dialog (close_dialog) or an error (get_python_errors)."
                ),
                "widget": widget_ident(widget),
            },
        }
    if count >= 1:
        return {
            "message": (
                f"No UI state change since the last identical '{action}' on this target, it likely "
                "had no effect. Re-snapshot and try a different approach rather than repeating it."
            )
        }
    return None








_ACTION_REQUIREMENTS = {
    "click": (True, True, True, True, False),
    "double_click": (True, True, True, True, False),
    "right_click": (True, True, True, True, False),
    "hover": (True, False, True, True, False),
    "set_text": (True, True, False, False, True),
    "select": (True, True, False, False, False),
    "select_tab": (True, True, False, False, False),
    "check": (True, True, False, False, False),
    "uncheck": (True, True, False, False, False),
    "scroll": (True, False, False, False, False),
    "focus": (False, False, False, False, False),
}
_ACTIONABLE_ACTIONS = frozenset(_ACTION_REQUIREMENTS)


def _receives_events(widget) -> bool:
    """True if the widget (or a descendant) is the topmost one at its center."""





    try:
        center = widget.rect().center()
        hit = QApplication.instance().widgetAt(widget.mapToGlobal(center))
        if hit is None:
            return False
        node = hit
        while node is not None:
            if node is widget:
                return True
            node = node.parentWidget()
        return widget.isAncestorOf(hit)
    except Exception:
        return True


def _global_rect(widget):
    """Top-left in GLOBAL coords + size."""

    try:
        from qgis.PyQt.QtCore import QPoint
        top_left = widget.mapToGlobal(QPoint(0, 0))
        return (top_left.x(), top_left.y(), widget.width(), widget.height())
    except Exception:
        return None


def _is_actionable(widget, reqs) -> tuple[bool, str]:
    """Single-shot check of the static conditions for this action. Returns (ok, failed)."""
    visible, enabled, _stable, _receives, editable = reqs
    if not is_alive(widget):
        return False, "destroyed"
    if visible:
        if not widget.isVisible():
            return False, "not visible"
        try:
            if widget.visibleRegion().isEmpty():
                return False, _CLIPPED
        except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
            pass
    if enabled and not widget.isEnabled():
        return False, "disabled"
    if editable:
        is_ro = getattr(widget, "isReadOnly", None)
        if callable(is_ro) and is_ro():
            return False, "read-only"
    return True, ""


_CLIPPED = "off-screen / fully clipped"


def _wait_actionable(widget, action: str, timeout_ms: int):
    """Poll until the widget is actionable for `action`, pumping the event loop."""









    reqs = _ACTION_REQUIREMENTS.get(action, (True, True, False, False, False))
    _v, _e, need_stable, need_receives, _ed = reqs
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    last_rect = None
    last_fail = "unknown"
    notes: dict = {}
    scrolled = False
    while True:
        ok, fail = _is_actionable(widget, reqs)
        if not ok and fail == _CLIPPED and not scrolled:


            scrolled = True
            area = scroll_into_view(widget)
            if area:
                notes["scrolled_into_view"] = area
                ok, fail = _is_actionable(widget, reqs)
        if ok:
            stable = True
            if need_stable:
                rect = _global_rect(widget)
                stable = rect is not None and rect == last_rect
                last_rect = rect
            receives = _receives_events(widget) if need_receives else True
            if stable and receives:
                return None, notes
            last_fail = "occluded by another widget" if (stable and not receives) else "still moving (animating)"
        else:
            last_fail = fail
        if time.monotonic() >= deadline:
            return {
                "_error": f"Target not actionable for '{action}': {last_fail}",
                "_code": "NOT_ACTIONABLE",
                "_suggestion": (
                    "Re-run accessibility_snapshot to see current state. The target may be disabled, "
                    "hidden, still loading, covered by a dialog, or on a tab that is not showing "
                    "(switch with ref_action select_tab). Scrolling it into view was already tried. "
                    "Use assert_ui to wait for it first."
                ),
                "widget": widget_ident(widget),
                "scrolled_into_view": notes.get("scrolled_into_view", ""),
            }, notes
        process_events()
        time.sleep(0.016)




def _scope_contains_text(args: dict, needle: str):
    """Return (found, where) for `needle` across visible widgets in scope."""
    if not needle:
        return False, None
    low = needle.lower()
    roots, _ = _scope_roots(args)
    for root in roots:
        if not isinstance(root, QWidget):
            continue
        for w in descendants(root):
            try:
                if not w.isVisible():
                    continue
                if low in (widget_text(w) or "").lower():
                    return True, (widget_summary(w).get("text") or w.metaObject().className())
            except Exception:  # nosec B112 - the debug report reads what answers and skips what does not
                continue
    return False, None


def _check_item_condition(condition: str, view, index, args: dict) -> tuple[bool, dict]:
    """assert_ui conditions against one row of an item view (@iN ref)."""
    model = view.model()
    raw = model.data(index)
    text = "" if raw is None else redact_secrets(str(raw))
    checked = item_check_state(model, index)
    rect = view.visualRect(index)
    on_screen = (view.isVisible() and not rect.isEmpty()
                 and view.viewport().rect().intersects(rect))
    try:
        enabled = bool(int(model.flags(index)) & int(Qt.ItemFlag.ItemIsEnabled))
    except Exception:
        enabled = True
    observed = {"path": item_path_of(index), "text": text, "visible": on_screen, "enabled": enabled}
    if checked is not None:
        observed["checked"] = checked

    if condition == "visible":
        return on_screen, observed
    if condition == "hidden":
        return not on_screen, observed
    if condition == "enabled":
        return enabled and on_screen, observed
    if condition == "disabled":
        return not enabled, observed
    if condition == "checked":
        return checked is True, observed
    if condition == "unchecked":
        return checked is False, observed
    if condition == "focused":
        return view.currentIndex() == index, observed
    if condition == "value_equals":
        return text == str(args.get("expected")), observed
    if condition == "value_contains":
        return str(args.get("expected") or "").lower() in text.lower(), observed
    return False, {"error": f"condition '{condition}' does not apply to a row ref", **observed}


def _check_condition(condition: str, args: dict) -> tuple[bool, dict]:
    if condition in ("text_present", "text_absent"):
        needle = args.get("text") or ""
        found, where = _scope_contains_text(args, needle)
        observed = {"text": needle, "present": found, "in": where}
        return (found if condition == "text_present" else not found), observed

    if condition == "count_equals":
        query = args.get("query")
        matches = candidate_widgets(query, visible_only=True) if query else []
        n = len(matches)
        try:
            expected = int(args.get("expected"))
        except (TypeError, ValueError):
            return False, {"count": n, "error": "expected must be an integer"}
        return n == expected, {"count": n, "expected": expected}

    if is_item_ref(args.get("ref") or ""):
        view, index, error = get_ref_store().resolve_item(args["ref"])
        if error is not None:
            if condition in ("hidden", "disabled", "unchecked"):
                return True, {"found": False}
            return False, {"found": False, "error": error.get("_error")}
        return _check_item_condition(condition, view, index, args)

    widget, error = _resolve_target(args)
    if error is not None:

        if condition in ("hidden", "disabled", "unchecked"):
            return True, {"found": False}
        return False, {"found": False, "error": error.get("_error")}

    observed = {"visible": widget.isVisible(), "enabled": widget.isEnabled()}
    checkable = getattr(widget, "isCheckable", None)
    is_checkable = callable(checkable) and checkable()
    if is_checkable:
        observed["checked"] = widget.isChecked()
    value = widget_value(widget)
    if value not in (None, ""):
        observed["value"] = value

    if condition == "visible":
        return widget.isVisible(), observed
    if condition == "hidden":
        return not widget.isVisible(), observed
    if condition == "enabled":
        return widget.isEnabled() and widget.isVisible(), observed
    if condition == "disabled":
        return not widget.isEnabled(), observed
    if condition == "editable":
        is_ro = getattr(widget, "isReadOnly", None)
        read_only = callable(is_ro) and is_ro()
        observed["read_only"] = read_only
        return widget.isEnabled() and not read_only, observed
    if condition == "focused":
        focused = widget.hasFocus()
        observed["focused"] = focused
        return focused, observed
    if condition == "checked":
        return bool(observed.get("checked")), observed
    if condition == "unchecked":
        return is_checkable and not widget.isChecked(), observed
    if condition == "value_equals":
        expected = args.get("expected")
        return str(value) == str(expected), {"value": value, "expected": expected}
    if condition == "value_contains":
        expected = args.get("expected") or ""
        return str(expected).lower() in str(value or "").lower(), {"value": value, "expected": expected}
    return False, {"error": f"unknown condition: {condition}"}


def _assert_ui(args: dict) -> dict:
    condition = args["condition"]
    timeout_ms = min(max(int(args.get("timeout_ms", 3000) or 3000), 0), 120000)
    deadline = time.monotonic() + timeout_ms / 1000.0
    observed: dict = {}
    while True:
        ok, observed = _check_condition(condition, args)
        if ok:
            elapsed = int((timeout_ms / 1000.0 - (deadline - time.monotonic())) * 1000)
            return {"ok": True, "condition": condition, "observed": observed, "elapsed_ms": max(0, elapsed)}
        if time.monotonic() >= deadline:
            return {"ok": False, "condition": condition, "observed": observed, "timeout_ms": timeout_ms}
        process_events()
        time.sleep(0.05)


def _do_scroll(widget, delta: int) -> dict:
    from qgis.PyQt.QtCore import QPoint, QPointF
    from qgis.PyQt.QtGui import QWheelEvent

    center = widget.rect().center()
    glob = widget.mapToGlobal(center)
    try:
        event = QWheelEvent(
            QPointF(center), QPointF(glob), QPoint(0, 0), QPoint(0, delta),
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.NoScrollPhase, False,
        )
    except Exception:
        return {"_error": "Scroll not supported on this Qt build", "widget": widget_ident(widget)}
    QApplication.instance().sendEvent(widget, event)
    process_events()
    return {"scrolled": True, "delta": delta, "widget": widget_ident(widget)}


_KEY_MAP = {
    "escape": Qt.Key.Key_Escape, "esc": Qt.Key.Key_Escape,
    "enter": Qt.Key.Key_Return, "return": Qt.Key.Key_Return,
    "tab": Qt.Key.Key_Tab, "backtab": Qt.Key.Key_Backtab,
    "backspace": Qt.Key.Key_Backspace, "delete": Qt.Key.Key_Delete, "del": Qt.Key.Key_Delete,
    "insert": Qt.Key.Key_Insert, "ins": Qt.Key.Key_Insert,
    "space": Qt.Key.Key_Space, "home": Qt.Key.Key_Home, "end": Qt.Key.Key_End,
    "up": Qt.Key.Key_Up, "down": Qt.Key.Key_Down, "left": Qt.Key.Key_Left, "right": Qt.Key.Key_Right,
    "pageup": Qt.Key.Key_PageUp, "pagedown": Qt.Key.Key_PageDown,
}

for _fn in range(1, 13):
    _KEY_MAP[f"f{_fn}"] = getattr(Qt.Key, f"Key_F{_fn}")

_MOD_MAP = {
    "ctrl": Qt.KeyboardModifier.ControlModifier,
    "shift": Qt.KeyboardModifier.ShiftModifier,
    "alt": Qt.KeyboardModifier.AltModifier,
    "meta": Qt.KeyboardModifier.MetaModifier,
}


def _press_key(args: dict) -> dict:
    from qgis.PyQt.QtTest import QTest
    from qgis.utils import iface

    key_name = (args.get("key") or "").strip()
    if not key_name:
        return {"_error": "key is required"}


    target = None
    if args.get("ref") or args.get("query"):
        target, error = _resolve_target(args)
        if error:
            return error
    if target is None:
        target = QApplication.instance().focusWidget()
    if target is None and iface is not None:
        target = iface.mapCanvas()
    if target is None:
        return {"_error": "No target widget for key press"}

    modifiers = Qt.KeyboardModifier.NoModifier
    for m in (args.get("modifiers") or []):
        modifiers |= _MOD_MAP.get(str(m).lower(), Qt.KeyboardModifier.NoModifier)

    low = key_name.lower()
    if low not in _KEY_MAP and len(key_name) != 1:
        return {"_error": f"Unknown key: {key_name}"}

    def _send_key():
        if low in _KEY_MAP:
            QTest.keyClick(target, _KEY_MAP[low], modifiers)
        else:
            QTest.keyClicks(target, key_name, modifiers)

    if args.get("defer"):


        from qgis.PyQt.QtCore import QTimer

        QTimer.singleShot(0, _send_key)
        return {"deferred": True, "pressed": key_name, "target": widget_ident(target)}

    process_events()
    _send_key()
    process_events()
    return {"pressed": key_name, "target": widget_ident(target)}


def _try_close(widget) -> bool:
    for method in ("reject", "accept", "close"):
        fn = getattr(widget, method, None)
        if callable(fn):
            try:
                fn()
                return True
            except Exception:  # nosec B112 - the debug report reads what answers and skips what does not
                continue
    return False


def _close_dialog(args: dict) -> dict:
    if args.get("defer"):


        from qgis.PyQt.QtCore import QTimer

        QTimer.singleShot(0, lambda: _do_close_dialog(args))
        return {"deferred": True,
                "note": "close scheduled on the event loop, use for a modal blocking the handler."}
    return _do_close_dialog(args)


def _do_close_dialog(args: dict) -> dict:
    from qgis.PyQt.QtWidgets import QDialog, QMenu

    app = QApplication.instance()
    closed = []

    popup = app.activePopupWidget()
    if isinstance(popup, QMenu) and popup.isVisible():
        label = widget_summary(popup).get("text") or popup.metaObject().className()
        try:
            popup.hide()
            popup.close()
            closed.append(label)
        except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
            pass
    if args.get("all"):
        for w in list(app.topLevelWidgets()):
            if isinstance(w, QDialog) and w.isVisible():
                label = widget_summary(w).get("text") or w.metaObject().className()
                if _try_close(w):
                    closed.append(label)
    else:
        dlg = app.activeModalWidget() or app.activeWindow()
        if isinstance(dlg, QDialog) and dlg.isVisible():
            label = widget_summary(dlg).get("text") or dlg.metaObject().className()
            if _try_close(dlg):
                closed.append(label)
    process_events()
    return {"closed": closed, "count": len(closed)}


def _clean_action_text(text: str) -> str:
    """Menu entry text without '&' accelerator markers, trimmed."""
    return (text or "").replace("&", "").strip()


def _open_menus() -> list:
    """Every currently-visible QMenu (active popup first, then any top-level)."""
    from qgis.PyQt.QtWidgets import QMenu

    app = QApplication.instance()
    menus = []
    popup = app.activePopupWidget()
    if isinstance(popup, QMenu) and popup.isVisible():
        menus.append(popup)
    for w in app.topLevelWidgets():
        if isinstance(w, QMenu) and w.isVisible() and w not in menus:
            menus.append(w)
    return menus


def _menu_actions(menu) -> list:
    """(action, clean_text) for a menu's entries and its submenus one level deep."""
    items = []
    for action in menu.actions():
        if action.isSeparator():
            continue
        text = _clean_action_text(action.text())
        sub = action.menu()
        if sub is not None:
            for sub_action in sub.actions():
                if sub_action.isSeparator():
                    continue
                sub_text = _clean_action_text(sub_action.text())
                if sub_text:
                    items.append((sub_action, sub_text))
            if text:
                items.append((action, text))
        elif text:
            items.append((action, text))
    return items


def _select_menu_item(args: dict) -> dict:
    text = (args.get("text") or "").strip()
    if not text:
        return {"_error": "text is required",
                "_suggestion": "Pass the menu entry text to trigger."}
    menus = _open_menus()
    if not menus:
        return {"_error": "No open QMenu found.",
                "_code": "NO_MENU_OPEN",
                "_suggestion": "open the menu first (ref_action click on the menu button)"}
    needle = text.lower()
    available = []
    for menu in menus:
        for action, atext in _menu_actions(menu):
            available.append(atext)
            if needle in atext.lower():
                process_events()
                action.trigger()
                process_events()
                return {"triggered": action.text()}
    return {"_error": f"No menu entry matched '{text}'.",
            "_code": "NO_MATCH",
            "available": available,
            "_suggestion": "Pick one of the available entries (case-insensitive substring)."}


def _do_click(widget, kind: str = "left", programmatic: bool = False) -> dict:
    from qgis.PyQt.QtTest import QTest

    if programmatic and kind == "left" and isinstance(widget, QAbstractButton):



        widget.click()
        process_events()
        return {"clicked": True, "programmatic": True, "widget": widget_ident(widget)}

    if isinstance(widget, QTabBar):



        tabs, current = tab_state(widget)
        if widget.tabAt(widget.rect().center()) < 0:
            return {
                "_error": "A click at the centre of this tab bar lands on no tab.",
                "_code": "TAB_CLICK_MISSED",
                "tabs": tabs,
                "current": current,
                "_suggestion": "Use action=select_tab with value=<tab name>, or option_index.",
                "widget": widget_ident(widget),
            }
    if kind == "double":
        widget.setFocus(Qt.FocusReason.MouseFocusReason)
        QTest.mouseDClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget.rect().center())
        process_events()
        return {"double_clicked": True, "widget": widget_ident(widget)}
    if kind == "right":
        widget.setFocus(Qt.FocusReason.MouseFocusReason)
        QTest.mouseClick(widget, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, widget.rect().center())
        process_events()
        return {"right_clicked": True, "widget": widget_ident(widget),
                "note": "if a context menu opened, drive it with select_menu_item or dismiss with press_key Escape"}





    menu_getter = getattr(widget, "menu", None)
    if callable(menu_getter):
        try:
            if menu_getter() is not None and hasattr(widget, "showMenu"):
                from qgis.PyQt.QtCore import QTimer

                QTimer.singleShot(0, widget.showMenu)
                return {"clicked": True, "menu_opened": True, "widget": widget_ident(widget),
                        "note": "menu is open, use select_menu_item to pick an entry or press_key Escape to dismiss"}
        except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
            pass
    if isinstance(widget, QComboBox):
        widget.showPopup()
    else:




        widget.setFocus(Qt.FocusReason.MouseFocusReason)
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, widget.rect().center())
    process_events()
    return {"clicked": True, "widget": widget_ident(widget)}


def _do_set_text(widget, text: str, submit: bool) -> dict:
    if isinstance(widget, QLineEdit):
        widget.setFocus()
        widget.clear()
        widget.setText(text)
    elif isinstance(widget, (QTextEdit, QPlainTextEdit)):
        widget.setFocus()
        widget.setPlainText(text)
    elif isinstance(widget, QComboBox) and widget.isEditable():
        widget.setEditText(text)
    elif isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        widget.setValue(float(text) if isinstance(widget, QDoubleSpinBox) else int(float(text)))
    else:
        return {"_error": "Widget does not support direct text setting", "widget": widget_ident(widget)}
    if submit:
        from qgis.PyQt.QtTest import QTest

        QTest.keyClick(widget, Qt.Key.Key_Return)
    process_events()
    return {"set": True, "text": text, "widget": widget_ident(widget)}


def _do_select(widget, value: str, match: str, option_index=None) -> dict:
    if not isinstance(widget, QComboBox):
        return {"_error": "Target is not a QComboBox", "widget": widget_ident(widget)}
    if option_index is not None:
        try:
            idx = int(option_index)
        except (TypeError, ValueError):
            return {"_error": f"option_index must be an integer, got {option_index!r}",
                    "widget": widget_ident(widget)}
        count = widget.count()
        if idx < 0 or idx >= count:
            return {
                "_error": f"option_index {idx} out of range (0..{count - 1})",
                "count": count,
                "values": [widget.itemText(i) for i in range(count)],
                "widget": widget_ident(widget),
            }
        widget.setCurrentIndex(idx)
        process_events()
        return {"selected": widget.currentText(), "index": idx, "widget": widget_ident(widget)}
    target = value.lower()
    found = -1
    for idx in range(widget.count()):
        item_low = widget.itemText(idx).lower()
        if (match == "exact" and item_low == target) or (match != "exact" and target in item_low):
            found = idx
            break
    if found < 0:
        return {
            "_error": f"Combo value not found: {value}",
            "values": [widget.itemText(i) for i in range(widget.count())],
            "widget": widget_ident(widget),
        }
    widget.setCurrentIndex(found)
    process_events()
    return {"selected": widget.currentText(), "index": found, "widget": widget_ident(widget)}


def _resolve_tab_index(labels: list[str], value: str, option_index, match: str):
    """(index, error) for a tab named by text or by option_index."""
    if option_index is not None:
        try:
            index = int(option_index)
        except (TypeError, ValueError):
            return -1, {"_error": f"option_index must be an integer, got {option_index!r}"}
        if index < 0 or index >= len(labels):
            return -1, {"_error": f"option_index {index} out of range (0..{len(labels) - 1})",
                        "tabs": labels}
        return index, None
    target = (value or "").strip().lower()
    if not target:
        return -1, {"_error": "select_tab needs value (the tab name) or option_index", "tabs": labels}
    for index, label in enumerate(labels):
        if label.lower() == target:
            return index, None
    if match != "exact":
        for index, label in enumerate(labels):
            if target in label.lower():
                return index, None
    return -1, {"_error": f"No tab named '{value}'", "_code": "NO_MATCH", "tabs": labels}


def _do_select_tab(widget, args: dict) -> dict:
    """Switch a QTabBar, QTabWidget or QToolBox to a named page."""





    from qgis.PyQt.QtTest import QTest

    if not isinstance(widget, (QTabBar, QTabWidget, QToolBox)):
        return {"_error": "Target has no tabs", "_code": "NO_TABS",
                "_suggestion": "Snapshot the window and pick a node whose role is 'tabs'.",
                "widget": widget_ident(widget)}
    labels, before = tab_state(widget)
    if not labels:
        return {"_error": "This tab bar is empty", "_code": "NO_TABS", "widget": widget_ident(widget)}
    index, error = _resolve_tab_index(labels, args.get("value", ""), args.get("option_index"),
                                      args.get("match", "contains"))
    if error is not None:
        error["widget"] = widget_ident(widget)
        return error

    bar = widget.tabBar() if isinstance(widget, QTabWidget) else (widget if isinstance(widget, QTabBar) else None)
    via = ""
    if bar is not None:
        center = tab_rect_center(bar, index)
        if center is not None:
            bar.setFocus(Qt.FocusReason.MouseFocusReason)
            QTest.mouseClick(bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
            process_events()
            via = "click"
    if widget.currentIndex() != index:
        widget.setCurrentIndex(index)
        process_events()
        via = f"{via}+setCurrentIndex" if via else "setCurrentIndex"
    now = widget.currentIndex()
    if now != index:
        return {"_error": f"Tab '{labels[index]}' did not become current", "_code": "NO_PROGRESS",
                "tabs": labels, "current": now, "widget": widget_ident(widget)}
    return {"selected": labels[index], "index": index, "via": via, "was": before,
            "tabs": labels, "widget": widget_ident(widget)}


def _do_check_widget(widget, want: bool) -> dict:
    """check/uncheck a checkbox, a checkable button or a checkable group box."""
    checkable = getattr(widget, "isCheckable", None)
    set_checked = getattr(widget, "setChecked", None)
    is_checked = getattr(widget, "isChecked", None)
    if not (callable(set_checked) and callable(is_checked)) or (callable(checkable) and not checkable()):
        return {"_error": "This widget cannot be checked", "_code": "NOT_CHECKABLE",
                "_suggestion": "Use action=click, or pass a row @iN ref for a checkable row.",
                "widget": widget_ident(widget)}
    before = bool(is_checked())
    if before == want:
        return {"checked": before, "changed": False, "widget": widget_ident(widget)}
    set_checked(want)
    process_events()
    now = bool(is_checked())
    result = {"checked": now, "changed": now != before, "widget": widget_ident(widget)}
    if now != want:
        result["_error"] = f"The widget refused to become {'checked' if want else 'unchecked'}"
    return result




_ITEM_ACTIONS = frozenset({
    "click", "double_click", "right_click", "check", "uncheck", "expand", "collapse",
})


def _item_ident(view, index) -> dict:
    """Just enough to recognise which row was acted on."""
    ident = {"view": view.metaObject().className(), "path": item_path_of(index)}
    model = view.model()
    try:
        text = model.data(index)
        if text:
            ident["text"] = redact_secrets(str(text))
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass
    state = item_check_state(model, index)
    if state is not None:
        ident["checked"] = state
    return ident


def _item_click_point(view, index):
    """(point in viewport coords, error) for a row, scrolling it into view first."""
    try:
        view.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass
    process_events()
    rect = view.visualRect(index)
    visible = rect.intersected(view.viewport().rect())
    if rect.isEmpty() or visible.isEmpty():
        return None, {
            "_error": "This row has no visible rectangle, even after scrolling to it.",
            "_code": "NOT_ACTIONABLE",
            "_suggestion": "The view may be collapsed, filtered or hidden. Re-run accessibility_snapshot.",
            "item": _item_ident(view, index),
        }
    return visible.center(), None


def _item_action(action: str, view, index, args: dict) -> dict:
    if action not in _ITEM_ACTIONS:
        return {
            "_error": f"Action '{action}' does not apply to a row.",
            "_code": "UNSUPPORTED_ACTION",
            "supported": sorted(_ITEM_ACTIONS),
            "_suggestion": "Target the view itself with its @wN ref for screenshot, scroll or focus.",
            "item": _item_ident(view, index),
        }
    column = args.get("column")
    if column:
        sibling = index.sibling(index.row(), int(column))
        if not sibling.isValid():
            return {"_error": f"Column {column} does not exist on this row", "_code": "ITEM_NOT_FOUND",
                    "item": _item_ident(view, index)}
        index = sibling

    notes: dict = {}
    if not args.get("no_wait"):
        timeout_ms = min(max(int(args.get("timeout_ms", 2000) or 2000), 0), 20000)
        wait_as = "click" if action in ("click", "double_click", "right_click") else "select"
        not_ready, notes = _wait_actionable(view, wait_as, timeout_ms)
        if not_ready is not None:
            return not_ready

    if action in ("check", "uncheck"):
        result = _do_check_item(view, index, action == "check")
    elif action in ("expand", "collapse"):
        result = _do_expand_item(view, index, action == "expand")
    else:
        result = _do_click_item(view, index, action, args)
    if isinstance(result, dict) and "_error" not in result:
        result.update(notes)
        if action in _MUTATING_ACTIONS and not result.get("menu_opened"):
            loop = _check_no_progress(action, view, args, extra=item_path_of(index))
            if loop is not None:
                if loop.get("stop"):
                    return loop["error"]
                result["_warning"] = loop["message"]
    return result


def _do_click_item(view, index, action: str, args: dict) -> dict:
    from qgis.PyQt.QtTest import QTest

    point, error = _item_click_point(view, index)
    if error is not None:
        return error
    viewport = view.viewport()
    if action == "double_click":
        QTest.mouseDClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
        process_events()
        return {"double_clicked": True, "item": _item_ident(view, index)}
    if action == "right_click":



        QTest.mouseClick(viewport, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
        process_events()
        _post_context_menu(viewport, point)
        return {"right_clicked": True, "menu_opened": True, "item": _item_ident(view, index),
                "note": "context menu requested; pick an entry with select_menu_item or dismiss with press_key Escape"}
    QTest.mouseClick(viewport, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    process_events()
    return {"clicked": True, "current": view.currentIndex() == index, "item": _item_ident(view, index)}


def _post_context_menu(viewport, point):
    from qgis.PyQt.QtCore import QTimer
    from qgis.PyQt.QtGui import QContextMenuEvent

    global_point = viewport.mapToGlobal(point)

    def _send():
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point, global_point)
        QApplication.instance().sendEvent(viewport, event)

    QTimer.singleShot(0, _send)


def _do_check_item(view, index, want: bool) -> dict:
    model = view.model()
    before = item_check_state(model, index)
    if before is None:
        return {"_error": "This row has no check box", "_code": "NOT_CHECKABLE",
                "item": _item_ident(view, index)}
    if before is want:
        return {"checked": before, "changed": False, "item": _item_ident(view, index)}
    state = Qt.CheckState.Checked if want else Qt.CheckState.Unchecked
    accepted = bool(model.setData(index, state, Qt.ItemDataRole.CheckStateRole))
    process_events()
    now = item_check_state(model, index)
    result = {"checked": now, "changed": now != before, "item": _item_ident(view, index)}
    if now is not want:
        result["_error"] = f"The model refused to {'check' if want else 'uncheck'} this row"
        result["accepted"] = accepted
    return result


def _do_expand_item(view, index, want: bool) -> dict:
    setter = getattr(view, "setExpanded", None)
    getter = getattr(view, "isExpanded", None)
    if not (callable(setter) and callable(getter)):
        return {"_error": "This view has no expandable rows", "_code": "NOT_A_TREE",
                "item": _item_ident(view, index)}
    before = bool(getter(index))
    if before == want:
        return {"expanded": before, "changed": False, "item": _item_ident(view, index)}
    setter(index, want)
    process_events()
    now = bool(getter(index))
    return {"expanded": now, "changed": now != before, "children": view.model().rowCount(index),
            "item": _item_ident(view, index)}

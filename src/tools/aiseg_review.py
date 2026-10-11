# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core.logger import log_warning
from ..core.tool_registry import Tool, ToolRegistry

ACTIONS = ("status", "set", "objects", "remove", "merge", "undo")
SAVE_TOOL = "ai_segment_review_save"

AISEG_REVIEW_API = 6
_ACTION_DANGER = {"status": "read", "objects": "read"}


def available() -> bool:

    return _api() is not None


def sync(registry: ToolRegistry) -> bool:








    try:
        wanted = available()
    except Exception as e:  # noqa: BLE001
        log_warning(f"AI Segmentation review check failed: {e}")
        wanted = False
    if wanted == registry.has_tool("ai_segment_review"):
        return False
    if wanted:
        register(registry)
    else:
        registry.unregister("ai_segment_review")
        registry.unregister(SAVE_TOOL)
    return True


def register(registry: ToolRegistry) -> None:

    try:
        registry.register(Tool(
            name="ai_segment_review",
            danger="write",
            label=QT_TRANSLATE_NOOP("AIAgent", "AI Segmentation review[ ({action})]"),
            input_schema={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "settings": {
                        "type": "object",
                        "properties": {
                            "confidence": {"type": "number"},
                            "min_size_m2": {"type": "number"},
                            "max_size_m2": {"type": "number"},
                            "simplify": {"type": "number"},
                            "smooth": {"type": "boolean"},
                            "expand": {"type": "integer"},
                            "fill_holes": {"type": "boolean"},
                            "fill_holes_max_m2": {"type": "number"},
                            "clean": {"type": "number"},
                            "ortho": {"type": "boolean"},
                            "shared_borders": {"type": "boolean"},
                            "points_pct": {"type": "integer"},
                            "display_mode": {"type": "string",
                                             "enum": ["normal", "outline", "confidence", "random"]},
                            "min_patch_m2": {"type": "number"},
                        },
                    },
                    "index": {"type": "integer"},
                    "indices": {"type": "array", "items": {"type": "integer"}},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                    "sort_by": {"type": "string", "enum": ["confidence", "area", "index"]},
                },
                "required": ["action"],
            },
            handler=_review,
            action_danger=_ACTION_DANGER,
        ))
        registry.register(Tool(
            name="ai_segment_review_save",
            danger="write",
            label=QT_TRANSLATE_NOOP("AIAgent", "Save the AI Segmentation review"),
            input_schema={"type": "object", "properties": {}},
            handler=_save,
        ))
    except Exception as e:  # noqa: BLE001
        log_warning(f"ai_segment_review not registered: {e}")


def _api():

    from ._widgets import sibling_api_version
    from .integration_tools import AISEG_KEYS, _find_plugin
    _, plugin = _find_plugin(AISEG_KEYS)
    if not plugin or (sibling_api_version(plugin) or 0) < AISEG_REVIEW_API:
        return None
    return getattr(plugin, "mcp_api", None)


def _outdated(method: str) -> dict:
    return {"_error": f"The installed AI Segmentation has no {method}; a newer AI Segmentation has it.",
            "code": "PLUGIN_OUTDATED"}


def _review(args: dict) -> dict:
    action = str(args.get("action") or "")
    api = _api()
    if api is None:
        return {"_error": ("AI Segmentation is not running, or its version has no agent review "
                           "(a newer AI Segmentation has it)."), "code": "PLUGIN_OUTDATED"}
    try:
        if action == "status":
            fn = getattr(api, "review_status", None)
            return fn() if callable(fn) else _outdated("review_status")
        if action == "objects":
            fn = getattr(api, "review_objects", None)
            if not callable(fn):
                return _outdated("review_objects")
            kwargs = {k: args[k] for k in ("offset", "limit", "sort_by") if args.get(k) is not None}
            return fn(**kwargs)
        if action == "set":
            settings = args.get("settings")
            if not isinstance(settings, dict) or not settings:
                return {"_error": "set takes settings, one or more review settings."}
            fn = getattr(api, "review_set", None)
            return fn(settings) if callable(fn) else _outdated("review_set")
        if action == "remove":
            if args.get("index") is None:
                return {"_error": "remove takes index, from objects."}
            fn = getattr(api, "review_remove_object", None)
            return fn(int(args["index"])) if callable(fn) else _outdated("review_remove_object")
        if action == "merge":
            fn = getattr(api, "review_merge_objects", None)
            return fn(list(args.get("indices") or [])) if callable(fn) else _outdated("review_merge_objects")
        if action == "undo":
            fn = getattr(api, "review_undo_last", None)
            return fn() if callable(fn) else _outdated("review_undo_last")
    except Exception as e:  # noqa: BLE001
        log_warning(f"AI Segmentation review {action} failed: {e}")
        return {"_error": f"AI Segmentation review {action} failed: {e}"}
    return {"_error": f"action must be one of {list(ACTIONS)}."}


def _save(_args: dict) -> dict:

    api = _api()
    if api is None:
        return {"_error": "AI Segmentation is not running, or its version has no agent review.",
                "code": "PLUGIN_OUTDATED"}
    fn = getattr(api, "review_save", None)
    if not callable(fn):
        return _outdated("review_save")
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        log_warning(f"AI Segmentation review save failed: {e}")
        return {"_error": f"AI Segmentation review save failed: {e}"}


def review_open() -> bool:

    api = _api()
    kind = getattr(api, "_review_kind", None) if api is not None else None
    try:
        return callable(kind) and kind() is not None
    except Exception:  # noqa: BLE001
        return False


def last_saved_layer() -> str:

    from .integration_tools import AISEG_KEYS, _find_plugin
    _, plugin = _find_plugin(AISEG_KEYS)
    last = getattr(plugin, "_last_auto_result", None) if plugin else None
    if isinstance(last, dict) and last.get("review_exported"):
        return str(last.get("layer_name") or "")
    return ""

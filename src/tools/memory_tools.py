# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

from ..core.profile import (
    load_memory_notes,
    notes_for_project,
    remove_memory_note,
)
from ..core.settings import Settings
from ..core.tool_registry import Tool, ToolRegistry

_DISABLED = {"stored": False, "reason": "memory_disabled",
             "message": "The user turned the memory off in Settings > Memory."}


def _project_path() -> str:

    try:
        from qgis.core import QgsProject
        return QgsProject.instance().fileName() or ""
    except Exception:  # noqa: BLE001
        return ""


def _enabled(settings) -> bool:
    return bool(getattr(settings, "memory_enabled", True))


def _shown(note: dict) -> dict:
    return {"id": note["id"], "text": note["text"], "kind": note["kind"], "scope": note["scope"]}


def _forget(args: dict) -> dict:
    settings = Settings()
    if not _enabled(settings):
        return dict(_DISABLED)
    wanted = str(args.get("id") or args.get("text") or "").strip()
    notes = notes_for_project(settings, _project_path())
    if not wanted:
        return {"removed": False, "notes": [_shown(n) for n in notes],
                "message": "forget needs the id of the note to remove."}
    if remove_memory_note(settings, wanted):
        return {"removed": True, "id": wanted, "notes": len(load_memory_notes(settings))}
    return {"removed": False, "id": wanted, "notes": [_shown(n) for n in notes],
            "message": "No note has that id or that exact text. These are the stored notes."}


def register_memory_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="forget",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": [],
        },
        handler=_forget,
    ))

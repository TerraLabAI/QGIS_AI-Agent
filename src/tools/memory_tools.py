# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The two tools that write the user's memory."""

























from __future__ import annotations

from ..core.profile import (
    DEFAULT_MEMORY_SCOPE,
    MEMORY_KINDS,
    MEMORY_MAX_NOTES,
    MEMORY_SCOPES,
    add_memory_note,
    load_memory_notes,
    memory_note_conflict,
    notes_for_project,
    project_key,
    remove_memory_note,
)
from ..core.settings import Settings
from ..core.tool_registry import Tool, ToolRegistry

_DISABLED = {"stored": False, "reason": "memory_disabled",
             "message": "The user turned the memory off in Settings > Memory. Do not retry."}


def _project_path() -> str:
    """Path of the open project, "" when unsaved. Same source as the chat list."""
    try:
        from qgis.core import QgsProject
        return QgsProject.instance().fileName() or ""
    except Exception:  # noqa: BLE001 - no QGIS in a unit test is not an error
        return ""


def _enabled(settings) -> bool:
    return bool(getattr(settings, "memory_enabled", True))


def _shown(note: dict) -> dict:
    return {"id": note["id"], "text": note["text"], "kind": note["kind"], "scope": note["scope"]}


def _remember(args: dict) -> dict:
    settings = Settings()
    if not _enabled(settings):
        return dict(_DISABLED)
    text = str(args.get("text") or "").strip()
    if not text:
        return {"_error": "text is required: one short sentence to remember."}
    kind = str(args.get("kind") or "").strip().lower()
    scope = str(args.get("scope") or "").strip().lower()
    replaces = str(args.get("replaces") or "").strip()
    if kind and kind not in MEMORY_KINDS:
        return {"_error": f"kind must be one of {', '.join(MEMORY_KINDS)}."}
    if scope and scope not in MEMORY_SCOPES:
        return {"_error": f"scope must be one of {', '.join(MEMORY_SCOPES)}."}

    path = _project_path()
    if scope == "project" and not path:
        return {"stored": False, "reason": "project_unsaved",
                "message": "The project has not been saved, so a note cannot be pinned to it. "
                           "Save the project first, or store this as a user note (scope user)."}

    if replaces:
        note = add_memory_note(settings, text, "ai", kind, scope, project_key(path), replaces)
        if note is None:
            return {"stored": False, "reason": "unknown_id", "id": replaces,
                    "message": "No note has that id. Call remember without replaces, or list what is "
                               "stored by calling forget with no arguments."}
        return {"stored": True, "updated": True, "note": _shown(note),
                "notes": len(load_memory_notes(settings))}








    conflict = memory_note_conflict(
        settings, text, scope or DEFAULT_MEMORY_SCOPE, project_key(path))
    if conflict is not None:
        same = conflict["text"].casefold() == text.casefold()
        return {
            "stored": False,
            "reason": "duplicate" if same else "similar_note_exists",
            "existing": _shown(conflict),
            "message": ("That note is already stored, word for word. Nothing to do." if same else
                        "A stored note already covers this. If the user is correcting it, call remember "
                        f"again with replaces=\"{conflict['id']}\" and the new wording. If it is genuinely "
                        "a different fact, say it in words that do not repeat the stored one."),
        }

    note = add_memory_note(settings, text, "ai", kind, scope, project_key(path))
    if note is None:
        return {"stored": False, "reason": "not_stored",
                "message": "The note was empty after cleaning. Do not retry."}
    return {"stored": True, "note": _shown(note),
            "notes": len(load_memory_notes(settings)), "max_notes": MEMORY_MAX_NOTES}


def _forget(args: dict) -> dict:
    settings = Settings()
    if not _enabled(settings):
        return dict(_DISABLED)
    wanted = str(args.get("id") or args.get("text") or "").strip()
    notes = notes_for_project(settings, _project_path())
    if not wanted:
        return {"removed": False, "notes": [_shown(n) for n in notes],
                "message": "Call forget again with the id of the note to remove."}
    if remove_memory_note(settings, wanted):
        return {"removed": True, "id": wanted, "notes": len(load_memory_notes(settings))}
    return {"removed": False, "id": wanted, "notes": [_shown(n) for n in notes],
            "message": "No note has that id or that exact text. These are the stored notes."}


def register_memory_tools(registry: ToolRegistry) -> None:
    registry.register(Tool(
        name="remember",
        input_schema={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                },
                "kind": {
                    "type": "string",
                    "enum": list(MEMORY_KINDS),
                },
                "scope": {
                    "type": "string",
                    "enum": list(MEMORY_SCOPES),
                },
                "replaces": {
                    "type": "string",
                },
            },
            "required": ["text"],
        },
        handler=_remember,



        danger="write",
    ))
    registry.register(Tool(
        name="forget",
        input_schema={
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": [],
        },
        handler=_forget,
        danger="write",
    ))

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import os
import time

from . import restore_guard
from .checkpoints import KIND_BEFORE
from .logger import log
from .snapshot import diff_changed, unsaved_edit_layers
from .tool_registry import tool_error


class _ExecutorUndo:
    def _own_unchanged_point(self, thread_id: str) -> frozenset:








        current = self.history.current(thread_id) if thread_id else None
        if current is None or current.kind != KIND_BEFORE or current.run_id not in getattr(self, "_threads", {}):
            return frozenset()
        entries = self.history.entries(thread_id)
        index = next((i for i, e in enumerate(entries) if e.id == current.id), 0)
        behind = entries[index - 1] if index > 0 else None
        try:


            if diff_changed(current.snapshot.diff()) or behind is None or behind.snapshot is None \
                    or diff_changed(behind.snapshot.diff()):
                return frozenset()
            return frozenset({current.id})
        except Exception as exc:  # noqa: BLE001
            log(f"Own before point not compared: {exc}")
            return frozenset()

    def restore_previous(self, thread_id: str) -> dict:

        history = self.history
        entry = history.previous(thread_id, self._own_unchanged_point(thread_id)) if thread_id else None
        if entry is None:
            return tool_error("This chat has no earlier state to go back to.", hint="undo_no_earlier_state")
        if not history.belongs_here(entry):
            return tool_error("The earlier state belongs to another project.", hint="undo_other_project")


        current = history.current(thread_id)
        reference = current or next(iter(reversed(history.entries(thread_id))), None)
        try:
            changed = restore_guard.maybe_changed(history, thread_id, entry, reference)
        except Exception as exc:  # noqa: BLE001
            log(f"Hand-edit check for qgis_undo failed: {exc}")
            return tool_error("Whether a file changed by hand since the AI agent's last step could not be "
                              "read, so nothing was restored. The chat's Undo button asks the user first.",
                              hint="undo_hand_changed")
        if changed:
            names = ", ".join(os.path.basename(path) for path in changed[:5])
            return tool_error(f"{names} changed since the AI agent's last step, possibly by the user's hand; "
                              "going back would write an older copy over it. Nothing was restored. The chat's "
                              "Undo button asks the user and keeps their version first.",
                              hint="undo_hand_changed")


        editing = [str(layer.name()) for layer in unsaved_edit_layers()]
        if editing:
            return tool_error(f"{', '.join(editing[:5])} has unsaved edits in QGIS; going back would discard "
                              "them. Nothing was restored. The user can save or discard them, or use the "
                              "chat's Undo button, which saves them in the history first.",
                              hint="undo_unsaved_edits")
        started = time.monotonic()
        result = entry.snapshot.restore(file_name=entry.file_to_keep(),
                                        extra_copies=history.file_versions(thread_id, entry))
        history.project_restored()
        from . import telemetry
        from . import telemetry_events as ev

        telemetry.track(ev.AGENT_UNDO_USED, {
            "ok": bool(result.get("ok")), "kind": "typed", "steps_back": 1, "run_id": entry.run_id,
            "duration_ms": int((time.monotonic() - started) * 1000), "refused": bool(result.get("refused")),
            "files_not_restored": len(result.get("file_restore_errors") or []),
            "memory_incomplete": len(result.get("memory_layers_incomplete") or [])})
        log(f"qgis_undo restored {entry.kind} run {entry.run_index}: {result.get('message') or result}")
        if result.get("project_read"):

            history.mark_current(entry)
        if not result.get("ok"):
            if not result.get("refused"):
                from .telemetry_errors import track_restore_failure
                track_restore_failure(result, "typed")
            return tool_error(str(result.get("message") or "The earlier state could not be fully restored."),
                              hint="undo_partly_restored")
        return {"undone": True, "restored_to": f"before request {entry.run_index}",
                "request": str(entry.prompt or "")[:200],
                "files_put_back": list(result.get("files_put_back") or result.get("files_restored") or [])[:20],
                "note": ("The edit was already saved, so the whole project went back to before that request, "
                         "as the chat's Undo arrow does; Redo brings it back.")}

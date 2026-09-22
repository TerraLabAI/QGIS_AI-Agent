# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

from .logger import log


class _ExecutorUndo:
    def restore_previous(self, thread_id: str) -> dict:

        history = self.history
        entry = history.previous(thread_id) if thread_id else None
        if entry is None:
            return {"_error": "This chat has no earlier state to go back to.",
                    "suggestion": "Nothing this chat did can be undone from here."}
        if not history.belongs_here(entry):
            return {"_error": "The earlier state belongs to another project.",
                    "suggestion": "Opening that project again goes back to it."}
        result = entry.snapshot.restore(file_name=entry.file_to_keep(),
                                        extra_copies=history.file_versions(thread_id, entry))
        history.project_restored()
        log(f"qgis_undo restored {entry.kind} run {entry.run_index}: {result.get('message') or result}")
        if result.get("project_read"):

            history.mark_current(entry)
        if not result.get("ok"):
            return {"_error": str(result.get("message") or "The earlier state could not be fully restored."),
                    "suggestion": "The chat's Undo button shows the points and what was not restored."}
        return {"undone": True, "restored_to": f"before request {entry.run_index}",
                "request": str(entry.prompt or "")[:200],
                "files_put_back": list(result.get("files_put_back") or result.get("files_restored") or [])[:20],
                "note": ("The edit was already saved, so the whole project went back to before that request, "
                         "as the chat's Undo arrow does; Redo brings it back.")}

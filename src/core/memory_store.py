# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The memory, on the user's disk, as files they can read."""






























from __future__ import annotations

import hashlib
import os
import re




NOTE_FILE_MAX_BYTES = 8192



STORE_MAX_BYTES = 512_000
_DIR_NAME = os.path.join("ai_agent", "memory")
_INDEX = "MEMORY.md"
_NOTES = "notes"
_DIGEST = ".digest"
_FRONT_RE = re.compile(r"^([A-Za-z_]+):\s*(.*)$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

_INDEX_HEADER = """\
# AI Agent memory

What the assistant remembers about you between conversations. These files stay
on this computer; only the notes that apply to the project you have open are
sent with a message, and nothing here is stored on our servers.

Edit the sentence under the frontmatter in a note to reword it, or delete the
file to forget it. The plugin reads this folder back the next time it starts a
conversation. This index is rewritten from the notes, so changing it does
nothing.
"""


def memory_dir() -> str:
    """The folder the notes live in, or "" when there is no QGIS profile to put it in."""
    try:
        from qgis.core import QgsApplication
        root = QgsApplication.qgisSettingsDirPath() or ""
    except Exception:  # noqa: BLE001 - no QGIS is not an error, only no folder
        return ""
    root = str(root).strip()
    return os.path.join(root, _DIR_NAME) if root else ""


def digest(notes: list) -> str:
    """A short fingerprint of what a list of notes says, independent of order."""
    parts = sorted(
        "{}|{}|{}|{}".format(n.get("id", ""), n.get("text", ""), n.get("scope", ""), n.get("project", ""))
        for n in notes if isinstance(n, dict)
    )
    return hashlib.sha1("\n".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _note_text(note: dict) -> str:
    return str(note.get("text") or "").strip()


def _front(note: dict) -> str:
    return (
        "---\n"
        f"id: {note.get('id', '')}\n"
        f"kind: {note.get('kind', '')}\n"
        f"scope: {note.get('scope', '')}\n"
        f"project: {note.get('project', '')}\n"
        f"source: {note.get('source', '')}\n"
        f"created: {note.get('created_at', '')}\n"
        f"updated: {note.get('updated_at', '')}\n"
        "---\n"
    )


def _index_line(note: dict) -> str:
    where = "this project only" if note.get("scope") == "project" else "everywhere"
    who = "noted by the AI" if note.get("source") == "ai" else "added by you"
    return "- [{text}]({notes}/{id}.md) - {kind}, {where}, {who}".format(
        text=_note_text(note).replace("]", ")"), notes=_NOTES, id=note.get("id", ""),
        kind=note.get("kind", ""), where=where, who=who)


def write_notes(notes: list) -> bool:
    """Mirror ``notes`` into the folder."""




    root = memory_dir()
    if not root:
        return False
    try:
        notes_dir = os.path.join(root, _NOTES)
        os.makedirs(notes_dir, exist_ok=True)
        wanted = {}
        for note in notes:
            note_id = str(note.get("id") or "")
            if not _SAFE_ID.match(note_id) or not _note_text(note):
                continue
            wanted[note_id] = note
        for name in os.listdir(notes_dir):
            if name.endswith(".md") and name[:-3] not in wanted:
                _remove(os.path.join(notes_dir, name))
        for note_id, note in wanted.items():
            _write(os.path.join(notes_dir, note_id + ".md"), _front(note) + _note_text(note) + "\n")
        lines = [_index_line(note) for note in notes if str(note.get("id") or "") in wanted]
        body = _INDEX_HEADER + "\n" + ("\n".join(lines) if lines else "_No notes yet._") + "\n"
        _write(os.path.join(root, _INDEX), body)
        _write(os.path.join(root, _DIGEST), digest(notes) + "\n")
        return True
    except Exception:  # noqa: BLE001 - a read-only profile is not an error the user can act on
        return False


def read_notes() -> list | None:
    """The notes as the folder has them, or None when the folder settles nothing."""






    root = memory_dir()
    if not root or not os.path.isdir(root):
        return None
    try:
        notes_dir = os.path.join(root, _NOTES)
        if not os.path.isdir(notes_dir):
            return None
        found = []
        total = 0
        for name in sorted(os.listdir(notes_dir)):
            if not name.endswith(".md") or not _SAFE_ID.match(name[:-3]):
                continue
            path = os.path.join(notes_dir, name)
            size = os.path.getsize(path)
            total += size
            if total > STORE_MAX_BYTES:
                return None
            if size > NOTE_FILE_MAX_BYTES:
                continue
            note = _parse(path, name[:-3])
            if note is not None:
                found.append(note)
        found.sort(key=lambda n: (str(n.get("created_at") or ""), str(n.get("id") or "")))
        if digest(found) == _read(os.path.join(root, _DIGEST)).strip():
            return None
        return found
    except Exception:  # noqa: BLE001 - an unreadable folder falls back to the settings store
        return None


def _parse(path: str, note_id: str) -> dict | None:
    """One note file: the frontmatter it still has, and the sentence under it."""
    raw = _read(path)
    if not raw:
        return None
    front: dict = {}
    body = raw
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            for line in parts[1].splitlines():
                match = _FRONT_RE.match(line.strip())
                if match:
                    front[match.group(1).lower()] = match.group(2).strip()
            body = parts[2]
    text = " ".join(body.split())
    if not text:
        return None
    return {
        "id": front.get("id") or note_id,
        "text": text,
        "kind": front.get("kind", ""),
        "scope": front.get("scope", ""),
        "project": front.get("project", ""),
        "source": front.get("source", "user"),
        "created_at": front.get("created", ""),
        "updated_at": front.get("updated", ""),
    }


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(NOTE_FILE_MAX_BYTES)
    except Exception:  # noqa: BLE001 - a missing or unreadable file reads as nothing
        return ""


def _write(path: str, body: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(body)
    os.replace(tmp, path)


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass

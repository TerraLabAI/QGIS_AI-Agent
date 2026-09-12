# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later































from __future__ import annotations

import hashlib
import locale
import os
import re

from .host_platform import IS_WINDOWS, retry_file_op
from .writeback import write_atomic




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

    try:
        from qgis.core import QgsApplication
        root = QgsApplication.qgisSettingsDirPath() or ""
    except Exception:  # noqa: BLE001
        return ""
    root = str(root).strip()
    return os.path.join(root, _DIR_NAME) if root else ""


def digest(notes: list) -> str:

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





    root = memory_dir()
    if not root:
        return False
    try:
        notes_dir = os.path.join(root, _NOTES)
        os.makedirs(notes_dir, exist_ok=True)
        wanted = {}
        for note in notes:
            if not isinstance(note, dict):
                continue
            note_id = str(note.get("id") or "")
            if not _SAFE_ID.match(note_id) or not _note_text(note):
                continue
            wanted[note_id] = note
        for name in os.listdir(notes_dir):
            if name.endswith(".md") and name[:-3] not in wanted:
                _remove(os.path.join(notes_dir, name))
        for note_id, note in wanted.items():
            _write(os.path.join(notes_dir, note_id + ".md"), _front(note) + _note_text(note) + "\n")
        lines = [_index_line(note) for note in wanted.values()]
        body = _INDEX_HEADER + "\n" + ("\n".join(lines) if lines else "_No notes yet._") + "\n"
        _write(os.path.join(root, _INDEX), body)
        _write(os.path.join(root, _DIGEST), digest(notes) + "\n")
        return True
    except Exception:  # noqa: BLE001
        return False


def read_notes() -> list | None:







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
            if os.path.islink(path) or not os.path.isfile(path):
                return None
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
    except Exception:  # noqa: BLE001
        return None


def _parse(path: str, note_id: str) -> dict | None:

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
        "id": note_id,
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
        with open(path, "rb") as handle:
            raw = handle.read(NOTE_FILE_MAX_BYTES)
    except Exception:  # noqa: BLE001
        return ""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):

        return raw.decode("utf-16", errors="replace")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:




        encoding = "mbcs" if IS_WINDOWS else (
            locale.getpreferredencoding(False) or "utf-8")
        return raw.decode(encoding, errors="replace")


def _write(path: str, body: str) -> None:











    expected = body.encode("utf-8")
    if not os.path.islink(path):
        try:
            with open(path, "rb") as handle:
                held = handle.read(2 * len(expected) + 2)
            if held.replace(b"\r\n", b"\n") == expected.replace(b"\r\n", b"\n"):
                return
        except OSError:
            pass
    write_atomic(path, body)


def _remove(path: str) -> None:









    try:
        retry_file_op(os.remove, path)
    except FileNotFoundError:
        return

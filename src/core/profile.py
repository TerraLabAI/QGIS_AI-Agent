# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

































from __future__ import annotations

import contextlib
import hashlib
import os
import re
from datetime import datetime, timezone

from . import memory_store

PROFILE_MAX_CHARS = 1500


PROFILE_LINE_MAX_CHARS = 80
MEMORY_MAX_NOTES = 40
MEMORY_NOTE_MAX_CHARS = 300

REPLY_STYLES = ("concise", "balanced", "detailed")



DEFAULT_REPLY_STYLE = "concise"
QUESTION_POLICIES = ("balanced", "minimal", "confirm")
DEFAULT_QUESTION_POLICY = "balanced"
EXPERTISE_LEVELS = ("", "beginner", "intermediate", "expert")
UNIT_SYSTEMS = ("metric", "imperial")
DEFAULT_UNITS = "metric"
LAYER_NAMINGS = ("human", "snake_case")
DEFAULT_LAYER_NAMING = "human"
MEMORY_SOURCES = ("user", "ai")



MEMORY_KINDS = ("preference", "fact", "convention")
DEFAULT_MEMORY_KIND = "fact"




MEMORY_SCOPES = ("user", "project")
DEFAULT_MEMORY_SCOPE = "user"



REPLY_LANGUAGES = (
    ("en", "English"),
    ("de", "Deutsch"),
    ("es", "Español"),
    ("fr", "Français"),
    ("id", "Bahasa Indonesia"),
    ("it", "Italiano"),
    ("ja", "日本語"),
    ("nl", "Nederlands"),
    ("pl", "Polski"),
    ("pt_BR", "Português (Brasil)"),
    ("zh_CN", "简体中文"),
    ("zh_TW", "繁體中文"),
)

_WS = re.compile(r"\s+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def clean_text(text: object, limit: int) -> str:

    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return value[:limit]


def clean_note_text(text: object) -> str:

    return _WS.sub(" ", str(text or "")).strip()[:MEMORY_NOTE_MAX_CHARS]


def normalize_reply_style(value: object) -> str:
    value = str(value or "").strip().lower()
    return value if value in REPLY_STYLES else DEFAULT_REPLY_STYLE


def _pick(value: object, choices: tuple, default: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in choices else default


def normalize_question_policy(value: object) -> str:
    return _pick(value, QUESTION_POLICIES, DEFAULT_QUESTION_POLICY)


def normalize_expertise(value: object) -> str:
    return _pick(value, EXPERTISE_LEVELS, "")


def normalize_units(value: object) -> str:
    return _pick(value, UNIT_SYSTEMS, DEFAULT_UNITS)


def normalize_layer_naming(value: object) -> str:
    return _pick(value, LAYER_NAMINGS, DEFAULT_LAYER_NAMING)


def normalize_reply_language(value: object) -> str:
    value = str(value or "").strip()
    return value if any(code == value for code, _name in REPLY_LANGUAGES) else ""





def _note_key(text: str) -> str:
    return clean_note_text(text).casefold()


def project_key(path: object) -> str:






    text = str(path or "").strip()
    if not text:
        return ""
    return hashlib.sha1(os.path.normcase(text).encode(), usedforsecurity=False).hexdigest()[:12]


def _note_id(text: str, created_at: str) -> str:





    return "m" + hashlib.sha1(f"{created_at}|{text}".encode(), usedforsecurity=False).hexdigest()[:8]


def normalize_note(raw: object) -> dict | None:







    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        return None
    text = clean_note_text(raw.get("text"))
    if not text:
        return None
    source = str(raw.get("source") or "user")
    created = str(raw.get("created_at") or _now_iso())
    kind = str(raw.get("kind") or "").strip().lower()
    scope = str(raw.get("scope") or "").strip().lower()
    project = str(raw.get("project") or "").strip()[:32]
    if scope not in MEMORY_SCOPES:
        scope = DEFAULT_MEMORY_SCOPE
    if scope == "user":
        project = ""
    elif not project:





        project = ""
    return {
        "id": str(raw.get("id") or "").strip()[:16] or _note_id(text, created),
        "text": text,
        "kind": kind if kind in MEMORY_KINDS else DEFAULT_MEMORY_KIND,
        "scope": scope,
        "project": project,
        "created_at": created,
        "updated_at": str(raw.get("updated_at") or created),
        "source": source if source in MEMORY_SOURCES else "user",
    }


def _scoped_key(note: dict) -> tuple:







    return (note.get("scope") or "", note.get("project") or "", _note_key(note.get("text", "")))


def _dedup(notes: list) -> list:

    seen_ids: set[str] = set()
    seen_text: set[tuple] = set()
    kept = []
    for raw in notes:
        note = normalize_note(raw)
        if note is None:
            continue
        key = _scoped_key(note)
        if note["id"] in seen_ids or key in seen_text:
            continue
        seen_ids.add(note["id"])
        seen_text.add(key)
        kept.append(note)
    return kept


def _same_scope(note: dict, scope: str, project: str) -> bool:

    if scope == "user":
        return (note.get("scope") or "") == "user"
    return (note.get("scope") or "") == scope and (note.get("project") or "") == (project or "")




_NOTE_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "on", "at", "to", "and", "or", "for", "with", "is", "are", "was",
    "user", "users", "their", "them", "they", "his", "her", "its", "that", "this", "it", "as", "by",
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "en", "pour", "avec", "dans", "sur",
    "utilisateur", "son", "sa", "ses", "est", "sont",
})
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)




NOTE_SIMILARITY = 0.45


def _content_words(text: str) -> set:
    return {w for w in (m.group(0).casefold() for m in _WORD_RE.finditer(str(text or "")))
            if len(w) > 2 and w not in _NOTE_STOPWORDS}


def similar_note(notes: list, text: str, threshold: float = NOTE_SIMILARITY) -> dict | None:






    words = _content_words(text)
    if not words:
        return None
    best: dict | None = None
    best_score = 0.0
    for note in notes:
        other = _content_words(note.get("text", ""))
        if not other:
            continue
        score = len(words & other) / len(words | other)
        if score > best_score:
            best, best_score = note, score
    return best if best_score >= threshold else None


def load_memory_notes(settings) -> list:










    try:
        stored = _dedup(settings.memory_notes)[-MEMORY_MAX_NOTES:]
    except Exception:  # noqa: BLE001
        stored = []
    try:
        edited = memory_store.read_notes()
    except Exception:  # noqa: BLE001
        edited = None
    if edited is None:
        return stored
    kept = _dedup(edited)[-MEMORY_MAX_NOTES:]
    _remember_removed(settings, stored, kept)
    try:
        settings.memory_notes = kept
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    memory_store.write_notes(kept)
    return kept


def save_memory_notes(settings, notes: list) -> list:






    try:
        before = _dedup(settings.memory_notes)
    except Exception:  # noqa: BLE001
        before = []
    _remember_removed(settings, before, notes)
    kept = _dedup(notes)[-MEMORY_MAX_NOTES:]
    settings.memory_notes = kept
    memory_store.write_notes(kept)
    return kept


def add_memory_note(settings, text: str, source: str = "ai", kind: str = "",
                    scope: str = "", project: str = "", replaces: str = "") -> dict | None:














    clean = clean_note_text(text)
    if not clean:
        return None
    notes = load_memory_notes(settings)
    wanted_id = str(replaces or "").strip()
    if wanted_id:
        for index, note in enumerate(notes):
            if note["id"] == wanted_id:
                notes[index] = {
                    **note,
                    "text": clean,
                    "kind": kind if kind in MEMORY_KINDS else note["kind"],
                    "scope": scope if scope in MEMORY_SCOPES else note["scope"],
                    "project": project if scope == "project" else ("" if scope == "user" else note["project"]),
                    "updated_at": _now_iso(),
                    "source": source if source in MEMORY_SOURCES else note["source"],
                }
                save_memory_notes(settings, notes)





                wrote = notes[index]
                stored = load_memory_notes(settings)
                kept = next((n for n in stored if n["id"] == wrote["id"]), None)
                if kept is not None:
                    return kept
                same = _scoped_key(wrote)
                return next((n for n in stored if _scoped_key(n) == same), None)



    scope_now = scope if scope in MEMORY_SCOPES else DEFAULT_MEMORY_SCOPE
    project_now = "" if scope_now == "user" else str(project or "").strip()[:32]



    key = _note_key(clean)
    for note in notes:
        if _note_key(note["text"]) == key and _same_scope(note, scope_now, project_now):
            return None




    if source == "ai":
        neighbours = [n for n in notes if _same_scope(n, scope_now, project_now)]
        if similar_note(neighbours, clean) is not None:
            return None
    created = _now_iso()
    note = normalize_note({
        "id": _note_id(clean, created), "text": clean, "kind": kind, "scope": scope,
        "project": project, "created_at": created, "updated_at": created,
        "source": source if source in MEMORY_SOURCES else "ai",
    })
    if note is None:
        return None
    notes.append(note)
    save_memory_notes(settings, notes)
    if source == "user":
        _unforget(settings, clean)
    return note


def memory_note_conflict(settings, text: str, scope: str = "", project: str = "") -> dict | None:










    clean = clean_note_text(text)
    if not clean:
        return None
    notes = load_memory_notes(settings)
    if scope in MEMORY_SCOPES:
        here = "" if scope == "user" else str(project or "").strip()[:32]
        notes = [n for n in notes if _same_scope(n, scope, here)]
    key = _note_key(clean)
    for note in notes:
        if _note_key(note["text"]) == key:
            return note
    return similar_note(notes, clean)



FORGOTTEN_MAX = 20
FORGOTTEN_MAX_CHARS = 200


def _remember_removed(settings, before: list, after: list) -> None:




    left = {str(n.get("id") or "") for n in after if isinstance(n, dict)}
    gone = [clean_note_text(n.get("text", ""))[:FORGOTTEN_MAX_CHARS] for n in before
            if isinstance(n, dict) and str(n.get("id") or "") not in left]
    gone = [t for t in gone if t]
    if not gone:
        return
    try:
        known = list(getattr(settings, "memory_forgotten", []) or [])
        keys = {_note_key(t) for t in gone}
        settings.memory_forgotten = ([t for t in known if _note_key(t) not in keys] + gone)[-FORGOTTEN_MAX:]
    except Exception:  # noqa: BLE001
        return


def record_declined(settings, text: str) -> None:


    clean = clean_note_text(text)[:FORGOTTEN_MAX_CHARS]
    if not clean:
        return
    from .threads import now_iso

    known = [_declined_item(t) for t in (getattr(settings, "memory_declined", []) or [])]
    key = _note_key(clean)
    kept = [t for t in known if t and _note_key(t["text"]) != key]
    settings.memory_declined = (kept + [{"text": clean, "at": now_iso()}])[-FORGOTTEN_MAX:]


def _declined_item(raw) -> dict | None:

    if isinstance(raw, str):
        return {"text": raw, "at": ""} if raw.strip() else None
    if isinstance(raw, dict) and str(raw.get("text") or "").strip():
        return {"text": str(raw["text"]), "at": str(raw.get("at") or "")}
    return None


def _unforget(settings, text: str) -> None:

    try:
        known = list(getattr(settings, "memory_forgotten", []) or [])
        key = _note_key(text)
        kept = [t for t in known if _note_key(t) != key and similar_note([{"text": t}], text) is None]
        if len(kept) != len(known):
            settings.memory_forgotten = kept
    except Exception:  # noqa: BLE001
        return


def remove_memory_note(settings, text: str) -> bool:

    wanted = str(text or "").strip()
    key = _note_key(wanted)
    notes = load_memory_notes(settings)
    kept = [n for n in notes if n["id"] != wanted and _note_key(n["text"]) != key]
    if len(kept) == len(notes):
        return False
    save_memory_notes(settings, kept)
    return True


def clear_memory_notes(settings) -> None:
    with contextlib.suppress(Exception):
        _remember_removed(settings, _dedup(settings.memory_notes), [])
    settings.memory_notes = []
    memory_store.write_notes([])





def notes_for_project(settings, project_path: str = "") -> list:







    key = project_key(project_path)
    return [n for n in load_memory_notes(settings)
            if n["scope"] == "user" or (key and n["project"] == key)]


def driven_session() -> bool:






    wanted = str(os.environ.get("QGIS_AGENT_PROFILE") or "").strip()
    if not wanted:
        return False
    try:
        from qgis.core import QgsApplication

        folder = str(QgsApplication.qgisSettingsDirPath() or "")
    except Exception:  # noqa: BLE001
        return False
    return os.path.basename(os.path.normpath(folder)) == wanted


def profile_context(settings, project_path: str = "") -> dict:











    context: dict = {}



    name = clean_text(getattr(settings, "profile_name", ""), PROFILE_LINE_MAX_CHARS)
    if name:
        context["name"] = name
    role = clean_text(getattr(settings, "profile_role", ""), PROFILE_LINE_MAX_CHARS)
    if role:
        context["role"] = role
    about = clean_text(getattr(settings, "profile_about", ""), PROFILE_MAX_CHARS)
    if about:
        context["about"] = about
    instructions = clean_text(getattr(settings, "profile_instructions", ""), PROFILE_MAX_CHARS)
    if instructions:
        context["instructions"] = instructions
    language = normalize_reply_language(getattr(settings, "reply_language", ""))
    if language:
        context["reply_language"] = language
    style = normalize_reply_style(getattr(settings, "reply_style", DEFAULT_REPLY_STYLE))
    if style:
        context["reply_style"] = style
    policy = normalize_question_policy(getattr(settings, "question_policy", DEFAULT_QUESTION_POLICY))
    if policy != DEFAULT_QUESTION_POLICY:
        context["question_policy"] = policy
    expertise = normalize_expertise(getattr(settings, "expertise", ""))
    if expertise:
        context["expertise"] = expertise
    units = normalize_units(getattr(settings, "units", DEFAULT_UNITS))
    if units != DEFAULT_UNITS:
        context["units"] = units
    naming = normalize_layer_naming(getattr(settings, "layer_naming", DEFAULT_LAYER_NAMING))
    if naming != DEFAULT_LAYER_NAMING:
        context["layer_naming"] = naming
    if bool(getattr(settings, "memory_enabled", True)):
        notes = notes_for_project(settings, project_path)
        if notes:
            context["memory"] = [note["text"] for note in notes]


        context["memory_notes"] = [
            {"id": n["id"], "text": n["text"], "kind": n["kind"], "scope": n["scope"]}
            for n in notes
        ]
        forgotten = [t for t in (getattr(settings, "memory_forgotten", None) or []) if isinstance(t, str)]
        if forgotten:

            context["forgotten"] = forgotten[-FORGOTTEN_MAX:]
        declined = [d for d in map(_declined_item, getattr(settings, "memory_declined", None) or []) if d]
        if declined:


            context["declined"] = declined[-FORGOTTEN_MAX:]
    return context

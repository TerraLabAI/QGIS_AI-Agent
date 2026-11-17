# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

























from __future__ import annotations

import base64
import binascii
import json
import os
import re
import tempfile
import time

from .host_platform import remove_quietly, retry_file_op
from .logger import log_warning
from .policy import state_dir
from .writeback import WriteBehind

NEW_CHAT = "_new"
MAX_DRAFTS = 50
MAX_CHARS = 30000
MAX_AGE_DAYS = 30
MAX_ITEMS = 20

_FILE = "drafts.json"
_IMAGES = "draft-images"

_IMAGE_GRACE_S = 120
_IMAGE_ID = re.compile(r"^[0-9a-f]{32}$")
_writer: WriteBehind | None = None

_drafts: dict[str, dict] = {}



_changed: dict[str, dict] = {}

_new_images: dict[str, str] = {}


def _path(tag: str) -> str:
    return os.path.join(state_dir(), "accounts", tag, _FILE)


def _image_path(tag: str, image_id: str) -> str:
    return os.path.join(state_dir(), "accounts", tag, _IMAGES, image_id + ".png")


def _load(tag: str) -> dict:
    drafts = _drafts.get(tag)
    if drafts is None:
        drafts = _drafts[tag] = _read(tag)
    return drafts


def _text(value, cap: int = 512) -> str:
    return value[:cap] if isinstance(value, str) else ""


def _list(value) -> list:
    return value if isinstance(value, list) else []


def _size(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 100000 else 0


def _entry(raw) -> dict | None:

    if not isinstance(raw, dict):
        return None
    text = _text(raw.get("text"), MAX_CHARS)
    chips = []
    for chip in _list(raw.get("chips")):
        if not isinstance(chip, dict) or not _text(chip.get("value")):
            continue
        at = chip.get("at")
        if not isinstance(at, int) or isinstance(at, bool) or not 0 <= at < len(text):
            continue
        chips.append({key: _text(chip.get(key)) for key in ("kind", "label", "value", "glyph")} | {"at": at})
    cards = [{key: _text(card.get(key)) for key in ("kind", "label", "value")}
             for card in _list(raw.get("cards")) if isinstance(card, dict) and _text(card.get("value"))]
    pastes = []
    for paste in _list(raw.get("pastes")):
        at = paste.get("at") if isinstance(paste, dict) else None
        words = _text(paste.get("text"), MAX_CHARS) if isinstance(paste, dict) else ""
        if isinstance(at, int) and not isinstance(at, bool) and 0 <= at <= len(text) and words.strip():
            pastes.append({"at": at, "text": words})
    files = [path for path in _list(raw.get("files")) if _text(path, 4096) and len(path) <= 4096]
    images = []
    for image in _list(raw.get("images")):
        if isinstance(image, dict) and _IMAGE_ID.match(_text(image.get("id"))):
            images.append({"id": image["id"], "name": _text(image.get("name"), 255) or "pasted.png",
                           "width": _size(image.get("width")), "height": _size(image.get("height"))})
    if not (text.strip() or cards or pastes or files or images):
        return None
    stamp = raw.get("t")
    entry = {"text": text, "t": float(stamp) if isinstance(stamp, (int, float)) else 0.0}
    for key, items in (("chips", chips), ("cards", cards), ("pastes", pastes), ("files", files),
                       ("images", images)):
        if items:
            entry[key] = items[:MAX_ITEMS]
    return entry


def _read(tag: str) -> dict:
    drafts = {}
    try:
        with open(_path(tag), encoding="utf-8") as fh:
            raw = json.load(fh).get("drafts", {})
        horizon = time.time() - MAX_AGE_DAYS * 86400
        for key, item in raw.items():
            entry = _entry(item)
            if entry is not None and entry["t"] >= horizon:
                drafts[str(key)] = entry
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return drafts


def get(tag: str, key: str) -> dict:





    entry = _load(tag).get(key) or {}
    images = []
    for image in entry.get("images", ()):
        path = _image_path(tag, image["id"])
        data = _new_images.get(path)
        if data is None:
            try:
                with open(path, "rb") as fh:
                    data = base64.b64encode(fh.read()).decode("ascii")
            except OSError:
                continue
        images.append({"id": image["id"], "kind": "image", "name": image["name"], "mime": "image/png",
                       "data_base64": data, "width": image["width"], "height": image["height"]})
    return {"text": entry.get("text", ""), "chips": list(entry.get("chips", ())),
            "cards": list(entry.get("cards", ())), "pastes": list(entry.get("pastes", ())),
            "files": list(entry.get("files", ())), "images": images}


def chats_with_drafts(tag: str) -> set:

    return set(_load(tag))


def put(tag: str, key: str, draft) -> None:





    drafts = _load(tag)
    raw = {"text": draft} if isinstance(draft, str) else dict(draft or {})
    raw["text"] = str(raw.get("text") or "")[:MAX_CHARS]
    entry = _entry(raw)
    if entry is None:
        if drafts.pop(key, None) is None:
            return
        _changed.setdefault(tag, {})[key] = None
    else:
        old = drafts.get(key)
        if old is not None and {**old, "t": 0} == {**entry, "t": 0}:
            return
        for image in raw.get("images") or ():
            data = image.get("data_base64") if isinstance(image, dict) else None
            path = _image_path(tag, str(image.get("id") or "")) if data else ""
            if data and _IMAGE_ID.match(str(image.get("id") or "")) and not os.path.exists(path):
                _new_images[path] = str(data)
        entry["t"] = time.time()
        drafts[key] = _changed.setdefault(tag, {})[key] = entry
    _schedule(tag)


def _schedule(tag: str) -> None:
    global _writer
    if _writer is None:
        _writer = WriteBehind(name="ai-agent-drafts")
    path = _path(tag)
    folder = os.path.join(os.path.dirname(path), _IMAGES)

    def produce() -> str | None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except OSError as exc:
            log_warning(f"Draft folder not writable: {exc}")
            return None
        drafts = _read(tag)
        for key, entry in _changed.pop(tag, {}).items():
            if entry is None:
                drafts.pop(key, None)
            else:
                drafts[key] = entry
        while len(drafts) > MAX_DRAFTS:
            drafts.pop(min(drafts, key=lambda k: drafts[k]["t"]))
        _drafts[tag] = drafts
        return json.dumps({"v": 2, "drafts": drafts}, ensure_ascii=False)

    _writer.schedule(path, produce)


    if os.path.isdir(folder) or any(os.path.dirname(p) == folder for p in list(_new_images)):
        _writer.schedule_job(lambda: _store_images(path, folder))


def _write_png(path: str, data: bytes) -> None:

    fd, tmp = tempfile.mkstemp(prefix=".draft-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        retry_file_op(os.replace, tmp, path)
    finally:
        remove_quietly(tmp)


def _store_images(path: str, folder: str) -> None:

    try:
        with open(path, encoding="utf-8") as fh:
            entries = json.load(fh).get("drafts", {}).values()
        named = {str(image.get("id")) + ".png" for entry in entries if isinstance(entry, dict)
                 for image in entry.get("images") or () if isinstance(image, dict)}
    except (OSError, ValueError, AttributeError, TypeError):
        return
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        log_warning(f"Draft pictures not writable: {exc}")
        return
    for image_path in [p for p in list(_new_images) if os.path.dirname(p) == folder]:
        data = _new_images.get(image_path)
        if data is not None and os.path.basename(image_path) in named:
            try:
                _write_png(image_path, base64.b64decode(data))
            except (OSError, ValueError, binascii.Error) as exc:
                log_warning(f"Draft picture not written: {exc}")
        _new_images.pop(image_path, None)
    horizon = time.time() - _IMAGE_GRACE_S
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        image_path = os.path.join(folder, name)
        try:
            unnamed = name not in named and os.path.getmtime(image_path) < horizon
        except OSError:
            continue
        if unnamed:
            remove_quietly(image_path)


def flush() -> None:

    if _writer is not None:
        _writer.flush(wait=True)

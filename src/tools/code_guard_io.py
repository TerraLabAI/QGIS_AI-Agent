# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import builtins as py_builtins
import errno
import os

from ..core.security import anchor, note_own_paths, validate_path
from ..core.text_encoding import detect_encoding, python_codec

_NO_FILE_CHANGE = ("a snippet removes only a file this conversation wrote (os.remove), never renames or overwrites"
                   " a file, and writes a new file name instead.")







WRITTEN_FILES: list[str] = []


MAX_WRITTEN_FILES = 20


def note_written(path) -> None:

    try:
        text = os.fspath(path)
    except (TypeError, ValueError):
        return
    if (isinstance(text, str) and text and text not in WRITTEN_FILES
            and len(WRITTEN_FILES) < MAX_WRITTEN_FILES):
        WRITTEN_FILES.append(text)

    note_own_paths([text])


def read_refusal(path, error: str) -> OSError:









    text = os.fspath(path)
    if validate_path(str(text)) is None and not os.path.lexists(text):
        missing = FileNotFoundError(error)
        missing.errno = errno.ENOENT
        return missing
    return PermissionError(error)


def _read_encoding(file) -> str:




    try:
        with py_builtins.open(file, "rb") as handle:
            head = handle.read(1 << 20)
    except OSError:
        return "utf-8-sig"
    found = detect_encoding(head)
    return "utf-8-sig" if found == "UTF-8" else python_codec(found)


def _guarded_open(file, mode="r", *args, **kwargs):

    if isinstance(file, int):
        raise PermissionError("execute_code cannot open a file descriptor.")

    file = anchor(file)
    writing = any(flag in str(mode) for flag in "wax+")


    remedy = "This call has no overwrite option; a new file name avoids it." if writing else None

    error = validate_path(os.fspath(file), write=writing, overwrite=False if writing else None,
                           overwrite_remedy=remedy, scoped=not writing)
    if error:
        raise PermissionError(error) if writing else read_refusal(file, error)
    if writing:
        note_written(file)











    if "b" not in str(mode):
        new_csv = writing and os.path.splitext(os.fspath(file))[1].lower() == ".csv"
        if len(args) < 2 and "encoding" not in kwargs:
            kwargs["encoding"] = "utf-8-sig" if new_csv else "utf-8" if writing else _read_encoding(file)
        if new_csv and len(args) < 4:
            kwargs.setdefault("newline", "")
    return py_builtins.open(file, mode, *args, **kwargs)

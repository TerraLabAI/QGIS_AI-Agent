# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




















from __future__ import annotations

import os
import re
import time

from .logger import log_warning
from .policy import AGENT_HOME, ensure_agent_directories

_PREFIX = "freeze-stack-"
_SUFFIX = ".txt"

_READ_BYTES = 65_536

_KEEP_S = 7 * 24 * 3600.0
_HEADER = re.compile(r"^(?:Current thread|Thread) 0x([0-9a-fA-F]+)")
_FRAME = re.compile(r'^\s+File "(.*)", line \d+ in (.+)$')


def path_for(pid: int) -> str:

    return os.path.join(AGENT_HOME, f"{_PREFIX}{int(pid)}{_SUFFIX}")


def pid_of(name: str) -> int:

    if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
        return 0
    try:
        return int(name[len(_PREFIX):len(name) - len(_SUFFIX)])
    except ValueError:
        return 0


class FreezeStack:


    def __init__(self) -> None:
        self._file = None
        self._broken = False
        self.armed = False

    def _open(self):
        if self._file is None and not self._broken:
            try:
                ensure_agent_directories()
                _prune_old()
                path = path_for(os.getpid())


                self._file = open(path, "w+b", buffering=0)  # noqa: SIM115
            except OSError as exc:
                self._broken = True
                log_warning(f"Freeze stack file not opened: {exc}")
        return self._file

    def arm(self, seconds: float) -> None:

        handle = self._open()
        if handle is None:
            return
        try:
            import faulthandler

            faulthandler.dump_traceback_later(max(1.0, float(seconds)), repeat=False, file=handle, exit=False)
            self.armed = True
        except Exception as exc:  # noqa: BLE001
            self._broken = True
            log_warning(f"Freeze stack not armed: {type(exc).__name__}: {exc}")

    def take(self) -> str:

        if not self.armed or self._file is None:
            return ""
        try:
            import faulthandler


            faulthandler.cancel_dump_traceback_later()
        except Exception:  # noqa: BLE001  # nosec B110
            pass
        self.armed = False
        try:
            fd = self._file.fileno()
            size = os.fstat(fd).st_size
            if not size:
                return ""
            os.lseek(fd, 0, os.SEEK_SET)
            data = os.read(fd, min(size, _READ_BYTES))
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            return data.decode("utf-8", "replace")
        except OSError as exc:
            log_warning(f"Freeze stack not read: {exc}")
            return ""

    def close(self) -> None:

        self.take()
        handle, self._file = self._file, None
        if handle is None:
            return
        path = handle.name if isinstance(handle.name, str) else ""
        try:
            handle.close()
            if path:
                os.remove(path)
        except OSError:  # nosec B110
            pass


def _prune_old() -> None:

    now = time.time()
    try:
        names = os.listdir(AGENT_HOME)
    except OSError:
        return
    for name in names:
        if not (name.startswith(_PREFIX) and name.endswith(_SUFFIX)):
            continue
        path = os.path.join(AGENT_HOME, name)
        try:
            if now - os.path.getmtime(path) > _KEEP_S:
                os.remove(path)
        except OSError:  # nosec B110
            pass


def main_thread_frames_in(dump: str, ident: int | None, limit: int) -> str:





    if not dump or ident is None:
        return ""
    names: list[str] = []
    inside = False
    for line in dump.splitlines():
        header = _HEADER.match(line)
        if header:
            if inside:
                break
            inside = int(header.group(1), 16) == ident
            continue
        if not inside:
            continue
        frame = _FRAME.match(line)
        if frame is None:
            if not line.strip():
                break
            continue
        names.append(f"{os.path.basename(frame.group(1))}:{frame.group(2).strip()}")
        if len(names) >= limit:
            break
    return " < ".join(names)

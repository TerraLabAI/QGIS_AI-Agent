# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A body written to a file while it arrives, and the inflation that goes with it."""

from __future__ import annotations

import shutil
import zlib

from .logger import log_warning
from .net_failure import FetchTooLarge, FetchTruncated










STREAM_KEEP_FREE_BYTES = 1024 * 1024 * 1024


STREAM_INFLATE_PIECE = 1024 * 1024


class Streamed:
    """What ``fetch_to_file`` hands back: the finished file, never its bytes."""

    __slots__ = ("path", "size", "wire_bytes", "headers", "url")

    def __init__(self, path: str, size: int, wire_bytes: int, headers: dict, url: str):
        self.path = path
        self.size = size
        self.wire_bytes = wire_bytes
        self.headers = headers
        self.url = url


def free_disk_bytes(directory: str) -> int | None:
    """Free bytes on the disk that holds *directory*, or None when it cannot say."""
    try:
        return int(shutil.disk_usage(directory).free)
    except (OSError, ValueError):
        return None


class _StreamInflater:
    """``_inflate`` for a body that arrives in pieces and goes straight to a file."""









    def __init__(self, encoding: str, max_bytes: int):
        self._kind = (encoding or "").strip().lower().split(",")[0].strip()
        self.max_bytes = max_bytes
        self.written = 0
        self.inflated = self._kind in ("gzip", "x-gzip", "deflate")
        self._gzip = "gzip" in self._kind
        self._options = (16 + zlib.MAX_WBITS,) if self._gzip else (zlib.MAX_WBITS, -zlib.MAX_WBITS)
        self._option = 0
        self._worker = zlib.decompressobj(self._options[0]) if self.inflated else None
        self._fresh = True
        self._first = True
        self._head = b""
        self._ignoring = False
        self._fed = 0

    def _put(self, data: bytes, handle) -> None:
        if not data:
            return
        self.written += len(data)
        if self.written > self.max_bytes:
            if self.inflated:
                raise FetchTooLarge(f"The answer expands to more than {self.max_bytes} bytes.")
            raise FetchTooLarge(f"The answer is larger than the {self.max_bytes} bytes this load may write.")
        handle.write(data)

    def write(self, block: bytes, handle) -> None:
        if not self.inflated:
            self._put(block, handle)
            return
        self._fed += len(block)
        if self._first and self._fresh:
            if len(self._head) + len(block) > STREAM_INFLATE_PIECE:
                raise FetchTooLarge("The compressed answer's header exceeds the 1 MB parsing budget.")
            self._head += block
        self._feed(block, handle)

    def _feed(self, data: bytes, handle) -> None:
        while data and not self._ignoring:
            if self._worker is None:

                self._worker = zlib.decompressobj(self._options[0])
                self._fresh = True
            try:
                out = self._worker.decompress(data, STREAM_INFLATE_PIECE)
            except zlib.error:
                if not self._fresh:
                    raise FetchTruncated("The compressed answer is damaged partway through, so the file is "
                                         "incomplete. Try again.") from None
                if self._first:
                    self._fall_back(handle)
                else:
                    log_warning("Bytes after a complete gzip stream were not a further member; "
                                "they are ignored.")
                    self._ignoring = True
                return
            if out:
                self._fresh = False
                self._head = b""
                self._put(out, handle)
            if self._worker.eof:
                self._fresh = False
                self._first = False
                self._head = b""
                if not self._gzip:
                    self._ignoring = True
                    return
                data = self._worker.unused_data
                self._worker = None
            else:
                data = self._worker.unconsumed_tail

    def _fall_back(self, handle) -> None:
        """The first stream did not open: the next window size, else the bytes as they came."""
        head = self._head
        self._option += 1
        if self._option < len(self._options):
            self._worker = zlib.decompressobj(self._options[self._option])
            self._fresh = True
            self._feed(head, handle)
            return
        log_warning(f"Body announced as {self._kind} did not decompress; passing it through as it came.")
        self.inflated = False
        self._head = b""
        self._put(head, handle)

    def close(self, handle) -> None:
        if not self.inflated or self._ignoring or not self._fed:
            return
        if self._worker is not None:
            self._put(self._worker.flush(), handle)
            if not self._worker.eof:
                raise FetchTruncated(
                    "The compressed answer ended before its own end marker, so the file is "
                    "incomplete. The connection dropped; try again.")

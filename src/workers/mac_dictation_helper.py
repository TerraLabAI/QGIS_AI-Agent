# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















import ctypes
import ctypes.util
import os
import subprocess  # nosec B404
import sys
import threading
import time

SAMPLE_RATE = 16000
_NOT_DETERMINED, _RESTRICTED, _DENIED, _AUTHORIZED = 0, 1, 2, 3

_ASK_SECONDS = 120


class _Format(ctypes.Structure):
    _fields_ = [("rate", ctypes.c_double), ("format", ctypes.c_uint32), ("flags", ctypes.c_uint32),
                ("bytes_per_packet", ctypes.c_uint32), ("frames_per_packet", ctypes.c_uint32),
                ("bytes_per_frame", ctypes.c_uint32), ("channels", ctypes.c_uint32),
                ("bits", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]


class _Buffer(ctypes.Structure):
    _fields_ = [("capacity", ctypes.c_uint32), ("data", ctypes.c_void_p), ("size", ctypes.c_uint32),
                ("user", ctypes.c_void_p), ("packet_capacity", ctypes.c_uint32),
                ("packets", ctypes.c_void_p), ("packet_count", ctypes.c_uint32)]


_Callback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Buffer),
                             ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p)


def microphone_status() -> int:

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AVFoundation.framework/AVFoundation")
    cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    send = ctypes.CFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)(("objc_msgSend", objc))
    return int(send(objc.objc_getClass(b"AVCaptureDevice"),
                    objc.sel_registerName(b"authorizationStatusForMediaType:"),
                    cf.CFStringCreateWithCString(None, b"soun", 0x08000100)))


def _fresh_status() -> int:





    here = os.path.dirname(os.path.abspath(__file__))
    python = os.path.join(here, "..", "MacOS", "pyhelper")
    try:
        command = [python if os.path.isfile(python) else sys.executable,
                   "-s", "-B", os.path.abspath(__file__), "--status"]
# nosec B603
        out = subprocess.run(command, capture_output=True, timeout=5, check=False).stdout  # nosec B603
        return int(out.strip() or _NOT_DETERMINED)
    except (OSError, ValueError, subprocess.SubprocessError):
        return _NOT_DETERMINED


class _Queue:


    def __init__(self, sink):
        self._lib = lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AudioToolbox.framework/AudioToolbox")
        lib.AudioQueueEnqueueBuffer.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Buffer), ctypes.c_uint32,
                                                ctypes.c_void_p]
        lib.AudioQueueNewInput.argtypes = [ctypes.POINTER(_Format), _Callback, ctypes.c_void_p, ctypes.c_void_p,
                                           ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        lib.AudioQueueAllocateBuffer.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                                 ctypes.POINTER(ctypes.POINTER(_Buffer))]
        lib.AudioQueueStart.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.AudioQueueStop.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        lib.AudioQueueDispose.argtypes = [ctypes.c_void_p, ctypes.c_bool]
        self._sink = sink
        self._live = True

        def filled(_user, queue, buffer, _when, _count, _packets):
            got = buffer.contents
            if got.size and self._live:
                self._sink(ctypes.string_at(got.data, got.size))
            if self._live:
                lib.AudioQueueEnqueueBuffer(queue, buffer, 0, None)

        self._filled = _Callback(filled)

        wanted = _Format(float(SAMPLE_RATE), 0x6C70636D, 0x4 | 0x8, 2, 1, 2, 1, 16, 0)
        self._queue = ctypes.c_void_p()
        self.ok = lib.AudioQueueNewInput(ctypes.byref(wanted), self._filled, None, None, None, 0,
                                         ctypes.byref(self._queue)) == 0
        if not self.ok:
            return
        for _ in range(3):
            buffer = ctypes.POINTER(_Buffer)()

            lib.AudioQueueAllocateBuffer(self._queue, SAMPLE_RATE // 20 * 2, ctypes.byref(buffer))
            lib.AudioQueueEnqueueBuffer(self._queue, buffer, 0, None)
        self.ok = lib.AudioQueueStart(self._queue, None) == 0

    def close(self) -> None:
        self._live = False
        if self._queue:
            self._lib.AudioQueueStop(self._queue, True)
            self._lib.AudioQueueDispose(self._queue, True)
            self._queue = ctypes.c_void_p()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def main(folder: str, max_seconds: float, parent: int) -> int:
    def say(status: str) -> None:
        part = os.path.join(folder, "status.part")
        with open(part, "w", encoding="utf-8") as fh:
            fh.write(status)
        os.replace(part, os.path.join(folder, "status"))

    stop = os.path.join(folder, "stop")

    def should_leave() -> bool:
        return os.path.exists(stop) or not _alive(parent) or not os.path.isdir(folder)

    status = microphone_status()
    if status in (_DENIED, _RESTRICTED):
        say("denied")
        return 0
    if status == _NOT_DETERMINED:
        say("asking")


        heard = []

        def listen(data: bytes) -> None:
            if data.strip(b"\0"):
                heard.append(True)

        probe = _Queue(listen)
        asked = checked = time.monotonic()
        while True:
            status = microphone_status()
            if status == _NOT_DETERMINED and heard:
                status = _AUTHORIZED
            if status == _NOT_DETERMINED and time.monotonic() - checked > 1.0:
                status, checked = _fresh_status(), time.monotonic()
            if status != _NOT_DETERMINED:
                break
            if should_leave():
                probe.close()
                return 0
            if time.monotonic() - asked > _ASK_SECONDS:
                probe.close()
                say("unanswered")
                return 0
            time.sleep(0.1)
        probe.close()
        if status != _AUTHORIZED:
            say("denied")
            return 0

    lock = threading.Lock()
    limit = int(max_seconds * SAMPLE_RATE) * 2
    written = [0]
    with open(os.path.join(folder, "audio.pcm"), "ab", buffering=0) as audio:
        def keep(data: bytes) -> None:
            with lock:
                room = limit - written[0]
                if room > 0:
                    audio.write(data[:room])
                    written[0] += min(len(data), room)

        queue = _Queue(keep)
        if not queue.ok:
            queue.close()
            say("error")
            return 1
        say("recording")
        while not should_leave() and written[0] < limit:
            time.sleep(0.03)
        queue.close()
        with lock:
            pass
    if os.path.isdir(folder):
        say("done")
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--status"]:
        print(microphone_status())
        sys.exit(0)
    sys.exit(main(sys.argv[1], float(sys.argv[2]), int(sys.argv[3])))

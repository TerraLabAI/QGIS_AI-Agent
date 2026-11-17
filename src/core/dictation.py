# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import base64
import io
import sys
import time
import uuid
import wave
from array import array

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

from . import protocol, telemetry
from . import telemetry_events as ev
from .dev_env import dev_env
from .qt_compat import one_shot

SAMPLE_RATE = 16_000
_CHUNK_MS = 50

_QUIET_PEAK = 260
_ANSWER_TIMEOUT_MS = 75_000
_DEFAULT_MAX_SECONDS = 180

IDLE, STARTING, ASKING, RECORDING, TRANSCRIBING = "idle", "starting", "asking", "recording", "transcribing"


def wav_bytes(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(SAMPLE_RATE)
        clip.writeframes(pcm)
    return out.getvalue()


def _samples(pcm: bytes) -> array:
    samples = array("h")
    samples.frombytes(pcm[:len(pcm) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples


def peak(pcm: bytes) -> int:
    samples = _samples(pcm)
    return max(max(samples), -min(samples)) if samples else 0


def loudness(pcm: bytes) -> float:

    return min(1.0, (peak(pcm) / 20000.0) ** 0.6)


class Converter:






    def __init__(self, rate: int, channels: int, kind: str):
        self._channels = max(1, int(channels))
        self._kind = kind
        self._step = max(1, int(rate)) / SAMPLE_RATE
        self._left = b""
        self._mono: list[float] = []
        self._at = 0.0
        self.passthrough = (int(rate), self._channels, kind) == (SAMPLE_RATE, 1, "h") and sys.byteorder == "little"

    def feed(self, raw: bytes) -> bytes:
        if self.passthrough:
            raw = self._left + raw
            self._left = raw[len(raw) // 2 * 2:]
            return raw[:len(raw) // 2 * 2]
        raw = self._left + raw
        frame = array(self._kind).itemsize * self._channels
        usable = len(raw) // frame * frame
        self._left = raw[usable:]
        values = array(self._kind)
        values.frombytes(raw[:usable])
        scale = {"h": 1.0, "i": 1.0 / 65536, "f": 32767.0, "B": 256.0}[self._kind]
        offset = 128 if self._kind == "B" else 0
        n = self._channels
        mono = self._mono
        if n == 1:
            mono.extend((value - offset) * scale for value in values)
        else:
            mono.extend((sum(values[i:i + n]) / n - offset) * scale for i in range(0, len(values), n))
        out = array("h")
        step = self._step
        while self._at + step <= len(mono):
            start, end = int(self._at), max(int(self._at) + 1, int(self._at + step))
            value = sum(mono[start:end]) / (end - start)
            out.append(max(-32768, min(32767, int(value))))
            self._at += step
        done = int(self._at)
        del mono[:done]
        self._at -= done
        if sys.byteorder == "big":
            out.byteswap()
        return out.tobytes()


class Recorder(QObject):


    asking = pyqtSignal()
    started = pyqtSignal()
    chunk = pyqtSignal(bytes)
    failed = pyqtSignal(str)
    finished = pyqtSignal()

    def start(self, max_seconds: int) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def abort(self) -> None:
        raise NotImplementedError


def _no_error(error) -> bool:
    name = getattr(error, "name", None)
    return name == "NoError" if name else int(error) == 0


class QtRecorder(Recorder):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._source = None
        self._device = None
        self._convert: Converter | None = None

    def start(self, max_seconds: int) -> None:
        try:
            from qgis.PyQt import QtMultimedia as media
        except ImportError:
            self.failed.emit("unsupported")
            return
        wanted = media.QAudioFormat()
        wanted.setSampleRate(SAMPLE_RATE)
        wanted.setChannelCount(1)
        if hasattr(media, "QAudioSource"):
            device = media.QMediaDevices.defaultAudioInput()
            formats = media.QAudioFormat.SampleFormat
            if device.isNull():
                self.failed.emit("no_microphone")
                return


            chosen = device.preferredFormat()
            kind = {formats.Int16: "h", formats.Int32: "i", formats.Float: "f",
                    formats.UInt8: "B"}.get(chosen.sampleFormat())
            source = media.QAudioSource(device, chosen, self)
        else:
            device = media.QAudioDeviceInfo.defaultInputDevice()
            wanted.setSampleSize(16)
            wanted.setCodec("audio/pcm")
            wanted.setByteOrder(media.QAudioFormat.Endian.LittleEndian)
            wanted.setSampleType(media.QAudioFormat.SampleType.SignedInt)
            if device.isNull():
                self.failed.emit("no_microphone")
                return
            chosen = wanted if device.isFormatSupported(wanted) else device.nearestFormat(wanted)
            types = media.QAudioFormat.SampleType
            kind = {(16, types.SignedInt): "h", (32, types.SignedInt): "i", (32, types.Float): "f",
                    (8, types.UnSignedInt): "B"}.get((chosen.sampleSize(), chosen.sampleType()))
            source = media.QAudioInput(device, chosen, self)
        if kind is None:
            self.failed.emit("unsupported")
            return
        self._convert = Converter(chosen.sampleRate(), chosen.channelCount(), kind)
        self._source = source
        source.stateChanged.connect(self._on_state)
        self._device = source.start()
        if self._device is None or not _no_error(source.error()):
            self._close()
            self.failed.emit("mic_error")
            return
        self._device.readyRead.connect(self._read)
        self.started.emit()

    def _read(self) -> None:
        if self._device is None or self._convert is None:
            return
        data = self._convert.feed(bytes(self._device.readAll()))
        if data:
            self.chunk.emit(data)

    def _on_state(self, _state) -> None:
        source = self._source
        if source is not None and not _no_error(source.error()):
            self._close()
            self.failed.emit("mic_error")

    def _close(self) -> None:
        source, self._source, self._device = self._source, None, None
        if source is not None:
            source.stateChanged.disconnect(self._on_state)
            source.stop()
            source.deleteLater()

    def stop(self) -> None:
        self._read()
        self._close()
        self.finished.emit()

    def abort(self) -> None:
        self._close()


class FileRecorder(Recorder):


    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self._path = path
        self._pcm = b""
        self._timer = QTimer(self)
        self._timer.setInterval(_CHUNK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self, max_seconds: int) -> None:
        try:
            with wave.open(self._path, "rb") as clip:
                self._pcm = clip.readframes(min(clip.getnframes(), max_seconds * SAMPLE_RATE))
        except (OSError, wave.Error, EOFError):
            self.failed.emit("no_microphone")
            return
        self._timer.start()
        self.started.emit()

    def _tick(self) -> None:
        size = SAMPLE_RATE * 2 * _CHUNK_MS // 1000
        data, self._pcm = self._pcm[:size], self._pcm[size:]
        if data:
            self.chunk.emit(data)

    def stop(self) -> None:
        self._timer.stop()
        if self._pcm:
            self.chunk.emit(self._pcm)
        self._pcm = b""
        self.finished.emit()

    def abort(self) -> None:
        self._timer.stop()
        self._pcm = b""


def _recorder(parent) -> Recorder:
    path = dev_env("QGIS_AGENT_DICTATION_WAV")
    if path:
        return FileRecorder(path, parent)
    if sys.platform == "darwin":
        from .dictation_mac import HelperRecorder

        return HelperRecorder(parent)
    return QtRecorder(parent)


class Dictation(QObject):
    state_changed = pyqtSignal(str)
    level = pyqtSignal(float)
    text_ready = pyqtSignal(str)
    failed = pyqtSignal(str)

    cut_at_limit = pyqtSignal()
    availability_changed = pyqtSignal()

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self._session = session
        self._state = IDLE
        self._recorder: Recorder | None = None
        self._pcm = bytearray()
        self._started_at = 0.0
        self._request = ""
        self._retry_wav = b""
        self._sent_at = 0.0
        self.last_answer_ms = 0
        self._answer_timer = one_shot(self, self._on_no_answer, _ANSWER_TIMEOUT_MS)
        self._limit_timer = one_shot(self, self._on_limit)
        session.transcript.connect(self._on_transcript)
        session.state_changed.connect(self._on_session_state)

    @property
    def state(self) -> str:
        return self._state

    @property
    def available(self) -> bool:

        return self._session.dictation_terms is not None

    @property
    def max_seconds(self) -> int:
        terms = self._session.dictation_terms or {}
        try:
            return max(5, int(terms.get("max_seconds") or _DEFAULT_MAX_SECONDS))
        except (TypeError, ValueError):
            return _DEFAULT_MAX_SECONDS

    @property
    def can_retry(self) -> bool:
        return bool(self._retry_wav) and self._state == IDLE

    def elapsed(self) -> float:
        return time.monotonic() - self._started_at if self._state == RECORDING else 0.0

    def start(self) -> None:
        if self._state != IDLE:
            return
        if not self.available:
            self._report("offline")
            return
        self._retry_wav = b""
        self._pcm = bytearray()
        self._started_at = 0.0
        self._recorder = recorder = _recorder(self)
        recorder.asking.connect(self._on_asking)
        recorder.started.connect(self._on_started)
        recorder.chunk.connect(self._on_chunk)
        recorder.failed.connect(self._fail)
        recorder.finished.connect(self._on_recorded)
        self._set(STARTING)
        recorder.start(self.max_seconds)

    def confirm(self) -> None:

        if self._state == RECORDING and self._recorder is not None:
            self._limit_timer.stop()
            self._recorder.stop()
        elif self._state in (STARTING, ASKING):
            self.cancel()

    def cancel(self) -> None:

        if self._state == IDLE:
            return
        self._drop_recorder(abort=True)
        self._pcm = bytearray()
        self._request = ""
        self._answer_timer.stop()
        self._limit_timer.stop()
        self._set(IDLE)

    def retry(self) -> None:

        if self.can_retry:
            self._send(self._retry_wav)

    def _report(self, code: str) -> None:


        since = round(time.monotonic() - self._started_at, 1) if self._started_at else 0
        telemetry.track(ev.AGENT_DICTATION_FAILED, {"code": code, "seconds": since})
        self.failed.emit(code)

    def _set(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def _drop_recorder(self, abort: bool) -> None:
        recorder, self._recorder = self._recorder, None
        if recorder is not None:
            if abort:
                recorder.abort()
            recorder.deleteLater()

    def _fail(self, code: str) -> None:
        self._drop_recorder(abort=True)
        self._pcm = bytearray()
        self._request = ""
        self._answer_timer.stop()
        self._limit_timer.stop()
        self._set(IDLE)
        self._report(code)

    def _on_asking(self) -> None:
        self._set(ASKING)

    def _on_started(self) -> None:
        self._started_at = time.monotonic()
        self._limit_timer.start(self.max_seconds * 1000)
        self._set(RECORDING)

    def _on_chunk(self, data: bytes) -> None:
        if self._state != RECORDING:
            return
        room = self.max_seconds * SAMPLE_RATE * 2 - len(self._pcm)
        self._pcm += data[:max(0, room)]
        self.level.emit(loudness(data))

    def _on_limit(self) -> None:
        if self._state == RECORDING:
            self.cut_at_limit.emit()
            self.confirm()

    def _on_recorded(self) -> None:
        self._drop_recorder(abort=False)
        pcm, self._pcm = bytes(self._pcm), bytearray()
        loudest = peak(pcm)
        if loudest < _QUIET_PEAK or len(pcm) < SAMPLE_RATE:
            self._set(IDLE)

            self._report("mic_silent" if loudest == 0 and len(pcm) > SAMPLE_RATE * 2 else "nothing_heard")
            return
        self._send(wav_bytes(pcm))

    def _send(self, wav: bytes) -> None:
        self._retry_wav = wav
        self._request = uuid.uuid4().hex
        frame = protocol.transcribe(self._request, base64.b64encode(wav).decode("ascii"))
        if not self._session.send_frame(frame):
            self._request = ""
            self._set(IDLE)
            self._report("offline")
            return
        self._sent_at = time.monotonic()
        self._answer_timer.start()
        self._set(TRANSCRIBING)

    def _on_transcript(self, request_id: str, text: str, code: str) -> None:
        if not self._request or request_id != self._request:
            return
        self._request = ""
        self._answer_timer.stop()
        self.last_answer_ms = int((time.monotonic() - self._sent_at) * 1000)
        retryable = code in ("busy", "failed", "rate_limited")
        if not retryable:
            self._retry_wav = b""
        self._set(IDLE)
        if code:
            self._report(code)
        elif not text.strip():
            self._report("nothing_heard")
        else:
            self.text_ready.emit(text.strip())

    def _on_no_answer(self) -> None:
        if self._state == TRANSCRIBING:
            self._request = ""
            self._set(IDLE)
            self._report("failed")

    def _on_session_state(self, state: str, _detail: str) -> None:
        if state != "online" and self._state == TRANSCRIBING:

            self._request = ""
            self._answer_timer.stop()
            self._set(IDLE)
            self._report("offline")
        self.availability_changed.emit()

    def shutdown(self) -> None:
        self.cancel()
        self._retry_wav = b""

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The temporal controller's animation written out as PNG frames, as one polled task."""

























from __future__ import annotations

import os
import time
import uuid

from qgis.core import (
    QgsApplication,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsMapRendererTask,
    QgsMapSettings,
    QgsProject,
    QgsRectangle,
    QgsTemporalNavigationObject,
)
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QSize

from ..core import limits, output_paths, security
from ..core.host_platform import remove_quietly, retry_file_op
from ..core.logger import log_warning
from ..core.tool_registry import tool_error

TOOL = "export_animation_frames"
_DIGITS = 4
_DAY_MS = 86_400_000




def running_export() -> str | None:
    """The task id of the export running now, or None."""
    from .processing_tools import _PROCESSING_TASKS

    return next((task_id for task_id, entry in _PROCESSING_TASKS.items()
                 if isinstance(entry.get("sequence"), FrameExport) and entry.get("status") == "running"), None)


def _animation(controller) -> dict:
    """The temporal layers and the controller's frames, or the refusal naming set_layer_temporal."""
    from .temporal_tools import _fmt, _moment, _navigation_mode

    layers = [layer.name() for layer in QgsProject.instance().mapLayers().values()
              if layer.temporalProperties() is not None and layer.temporalProperties().isActive()]
    if not layers:
        return tool_error("No layer of the project is animated over time, so there are no frames to export.",
                          "INVALID_ARGS", "Call set_layer_temporal on the layer first (its date field, or begin "
                                          "and end for a raster), then export its frames.")
    extents = controller.temporalExtents()
    total = int(controller.totalFrameCount())
    if (controller.navigationMode() != _navigation_mode("Animated") or not extents.begin().isValid()
            or not extents.end().isValid() or total < 1):
        return tool_error(f"The temporal controller is not animating a range: {', '.join(layers[:5])} "
                          "have time settings, but no frames run.", "INVALID_ARGS",
                          "Call set_layer_temporal on the layer to put the controller in animation over its "
                          "dates, then export its frames.")
    begin, end = _moment(extents.begin()), _moment(extents.end())
    return {"layers": layers, "total": total,
            "range": {"begin": _fmt(begin, bool(begin % _DAY_MS)), "end": _fmt(end, bool(end % _DAY_MS))}}


def _frames(args: dict, total: int):
    """(first, last) frame numbers asked for, or the refusal naming the frames there are."""
    first = 0 if args.get("first_frame") is None else args["first_frame"]
    last = total - 1 if args.get("last_frame") is None else args["last_frame"]
    for key, value in (("first_frame", first), ("last_frame", last)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < total:
            return tool_error(f"{key} {value!r} is not a frame of this animation, which has {total} "
                              f"(0 to {total - 1}).", "INVALID_ARGS",
                              f"Pass frame numbers from 0 to {total - 1}, or leave both out for every frame.")
    if last < first:
        return tool_error(f"last_frame {last} is before first_frame {first}.", "INVALID_ARGS",
                          "Swap them, or leave both out for every frame.")
    ceiling = int(limits.current("ANIMATION_MAX_FRAMES"))
    count = last - first + 1
    if count > ceiling:
        factor = -(-count // ceiling)
        return limits.refusal(
            "The export", f"{count:,} frames", f"{ceiling:,} frames",
            f"Export frames {first} to {first + ceiling - 1} now (first_frame and last_frame) and the rest in a "
            f"second call, or make the step of set_layer_temporal {factor} times as long, which gives about "
            f"{-(-count // factor):,} frames.")
    return first, last


def _size(args: dict, canvas_size: QSize, extent: QgsRectangle):
    """(width, height) of each frame, or the refusal naming the largest size that fits."""
    width, height = args.get("width"), args.get("height")
    ratio = extent.height() / extent.width() if extent.width() > 0 and extent.height() > 0 else 1.0
    asked = width is not None or height is not None
    if not asked:
        width = canvas_size.width()
        height = canvas_size.height() if not args.get("extent") else max(16, round(width * ratio))
    elif width is None:
        width = max(16, round(height / ratio))
    elif height is None:
        height = max(16, round(width * ratio))
    max_w, max_h = int(limits.current("MAX_RENDER_WIDTH_PX")), int(limits.current("MAX_RENDER_HEIGHT_PX"))
    max_pixels = int(limits.current("MAX_RENDER_PIXELS"))
    scale = min(1.0, max_w / width, max_h / height, (max_pixels / (width * height)) ** 0.5)
    if scale >= 1.0:
        return int(width), int(height)
    fit_w, fit_h = max(16, int(width * scale)), max(16, int(height * scale))
    if not asked:

        return fit_w, fit_h
    return limits.refusal(
        "Each frame", f"{width:,} by {height:,} pixels", f"{max_w:,} by {max_h:,} and {max_pixels:,} pixels",
        f"Pass width {fit_w} and height {fit_h}, the largest frame of this shape that fits.")


def _name(prefix: str, frame: int) -> str:
    return f"{prefix}{frame:0{_DIGITS}d}.png"


def _folder(args: dict, layer: str, prefix: str, first: int, last: int):
    """(folder, created) for the frames, or a refusal. A frame already there is never replaced."""
    from .advanced_tools import _prepare_frame_folder

    asked = str(args.get("out_dir") or "").strip()
    if asked:
        folder = security.expand_path(asked)
    else:
        stem = output_paths.safe_file_name(layer, "animation").replace(" ", "_")[:80] + "_frames"
        base = output_paths.default_folder()
        folder = os.path.join(base, stem)
        index = 2
        while os.path.exists(folder) and index < 1000:
            folder = os.path.join(base, f"{stem}_{index}")
            index += 1
    error = security.validate_path(folder, write=True)
    if error:
        return tool_error(error, "PERMISSION_DENIED",
                          "Pick a folder under the project folder, your home folder or the temp folder.")
    existed = os.path.isdir(folder)



    problem = _prepare_frame_folder(folder, prefix + "0.part")
    if problem:
        return tool_error(problem, "INVALID_ARGS", "Pass another out_dir.")
    if existed:
        names = set(os.listdir(folder))
        clash = next((_name(prefix, n) for n in range(first, last + 1) if _name(prefix, n) in names), None)
        if clash:
            return tool_error(f"{os.path.join(folder, clash)} already exists.", "INVALID_ARGS",
                              "Pass another prefix or out_dir: an export never replaces frames already there.")
    return folder, not existed


def _navigator(controller) -> QgsTemporalNavigationObject:
    """A private copy of the controller's frames: moving its frame number never moves the canvas."""
    navigator = QgsTemporalNavigationObject()
    if hasattr(controller, "availableTemporalRanges"):
        navigator.setAvailableTemporalRanges(controller.availableTemporalRanges())
    navigator.setTemporalExtents(controller.temporalExtents())
    navigator.setFrameDuration(controller.frameDuration())
    navigator.setTemporalRangeCumulative(controller.temporalRangeCumulative())
    navigator.setFramesPerSecond(controller.framesPerSecond())
    return navigator


def _decorations() -> list:
    """The canvas decorations (title, scale bar, north arrow) QGIS's own export draws on each frame."""
    try:
        from qgis.utils import iface

        return list(iface.activeDecorations()) if iface is not None and hasattr(iface, "activeDecorations") else []
    except Exception as exc:  # noqa: BLE001 - frames without decorations are still the animation
        log_warning(f"{TOOL}: canvas decorations not read: {exc}")
        return []


def export_animation_frames(args: dict) -> dict:
    """Main thread: check the call, then start the first frame and answer running with the task id."""
    from .advanced_tools import _apply_quality_flags, _safe_frame_prefix
    from .temporal_tools import _canvas_and_controller

    running = running_export()
    if running:
        return tool_error(f"An animation export is already running (task {running}).", "INVALID_ARGS",
                          f"Wait for it with get_task_status task_id {running}, or stop it with cancel_task, "
                          "then export again.")
    canvas, controller = _canvas_and_controller()
    if canvas is None or controller is None:
        return tool_error("There is no map canvas with a temporal controller in this QGIS window.",
                          "EXECUTION_FAILED", "Export the animation from QGIS with its main window open.")
    animation = _animation(controller)
    if "_error" in animation:
        return animation
    frames = _frames(args, animation["total"])
    if isinstance(frames, dict):
        return frames
    first, last = frames

    base = QgsMapSettings(canvas.mapSettings())
    extent = args.get("extent")
    if extent:
        rectangle = QgsRectangle(extent["xmin"], extent["ymin"], extent["xmax"], extent["ymax"])
        if rectangle.width() <= 0 or rectangle.height() <= 0:
            return tool_error("extent is empty: xmax must be above xmin and ymax above ymin.", "INVALID_ARGS",
                              f"Pass an extent in the map's CRS, {base.destinationCrs().authid()}, or leave it "
                              "out for the current view.")
    else:
        rectangle = canvas.extent()
    size = _size(args, base.outputSize(), rectangle)
    if isinstance(size, dict):
        return size
    prefix = _safe_frame_prefix(args.get("prefix"), "frame_")
    folder = _folder(args, animation["layers"][0], prefix, first, last)
    if isinstance(folder, dict):
        return folder
    base.setExtent(rectangle)
    base.setOutputSize(QSize(*size))

    base.setDevicePixelRatio(1.0)
    _apply_quality_flags(base)
    base.setFrameRate(controller.framesPerSecond())
    export = FrameExport(base, _navigator(controller), folder, prefix, (first, last), animation, _decorations())
    return export.start()




class FrameExport:
    """One export: each frame starts from the end signal of the one before it."""

    def __init__(self, base, navigator, folder, prefix, frames, animation, decorations):
        self.base = base
        self.context = QgsExpressionContext(base.expressionContext())
        self.navigator = navigator
        self.folder, self.created = folder
        self.prefix = prefix
        self.first, self.last = frames
        self.pending = list(range(self.first, self.last + 1))
        self.count = len(self.pending)
        self.animation = animation
        self.decorations = decorations

        self.layer_names = {layer.id(): layer.name() for layer in base.layers()}
        self.written: list[tuple[str, int]] = []
        self.current: dict | None = None
        self.removed = 0
        self.error = ""
        self.longest_start_ms = 0.0
        self.started = time.monotonic()
        self.ended: float | None = None
        self._advancing = False
        self.task_id = "frames-" + uuid.uuid4().hex[:12]
        self.entry = {"status": "running", "progress": 0, "algorithm": TOOL,
                      "started_at": time.strftime("%H:%M:%S"), "sequence": self}

    def path(self, frame: int) -> str:
        return os.path.join(self.folder, _name(self.prefix, frame))

    def start(self) -> dict:
        from .processing_tools import _POLL_INTERVAL_S, _PROCESSING_TASKS, _sweep_consumed_tasks

        _sweep_consumed_tasks()
        _PROCESSING_TASKS[self.task_id] = self.entry
        self.advance()
        if self.entry["status"] != "running":
            _PROCESSING_TASKS.pop(self.task_id, None)
            return tool_error(self.error or "The first frame could not start.", "EXECUTION_FAILED",
                              "Check the folder has room, or export a smaller frame.")

        return {**self.report(), "task_id": self.task_id, "status": "running",
                "outputs": {"first_frame": {"path": self.path(self.first)},
                            "last_frame": {"path": self.path(self.last)}},
                "note": "The frames render in the background, one after another, and QGIS stays responsive. "
                        "Poll get_task_status(task_id).",
                "poll": {"tool": "get_task_status", "args": {"task_id": self.task_id},
                         "interval_s": _POLL_INTERVAL_S, "timeout_s": 600 + 2 * self.count,
                         "label": f"Exporting {self.count} animation frames"}}

    def report(self) -> dict:
        """What the export has done so far, in the shape get_task_status carries."""
        seconds = (self.ended or time.monotonic()) - self.started
        out = {"frames": self.count, "frames_written": len(self.written), "first_frame": self.first,
               "last_frame": self.last, "out_dir": self.folder, "prefix": self.prefix,
               "size_px": [self.base.outputSize().width(), self.base.outputSize().height()],
               "animation_range": self.animation["range"], "temporal_layers": self.animation["layers"][:10],
               "seconds": round(seconds, 1)}
        if self.written:
            out.update(first_path=self.written[0][0], last_path=self.written[-1][0],
                       total_bytes=sum(size for _path, size in self.written))
        status = self.entry["status"]
        if status == "complete":
            out["longest_main_thread_start_ms"] = round(self.longest_start_ms, 1)
        elif status == "canceled":
            out["frames_removed"] = self.removed
            out["note"] = (f"Stopped: the {self.removed} frames already written were removed"
                           + (f", and the folder {self.folder}" if self.created else "") + "; nothing is left.")
        elif status == "error":
            out["error"] = self.error
            out["note"] = f"The export failed; the {self.removed} frames it had written were removed."
        return out

    def advance(self) -> None:
        """Main thread: settle a frame task deleted without its signal, then start the next frame or finish."""
        if self._advancing:
            return
        self._advancing = True
        try:
            if self.current is not None:
                if not sip.isdeleted(self.current["task"]):
                    return
                current, self.current = self.current, None
                self.entry.pop("task", None)
                self._settled(current, os.path.isfile(current["part"]))
            if self.entry["status"] != "running":
                return
            if not self.pending:
                self._finish("complete")
                return
            present = {layer.id() for layer in self.base.layers()}
            gone = [name for layer_id, name in self.layer_names.items() if layer_id not in present]
            if gone:


                self.error = (f"{len(gone)} of the layers the frames draw left the project during the export "
                              f"({', '.join(gone[:5])}), so the frames still to come would miss them.")[:300]
                self._fail()
                return
            self._start_frame(self.pending.pop(0))
        except Exception as exc:  # noqa: BLE001 - reached from a Qt signal, where nothing may raise
            log_warning(f"{TOOL}: {exc}")
            self.error = f"{type(exc).__name__}: {exc}"[:300]
            self._fail()
        finally:
            self._advancing = False

    def _start_frame(self, frame: int) -> None:
        started = time.perf_counter()
        self.navigator.setCurrentFrameNumber(frame)
        settings = QgsMapSettings(self.base)
        settings.setIsTemporal(True)
        settings.setTemporalRange(self.navigator.dateTimeRangeForFrameNumber(frame))
        settings.setCurrentFrame(frame)
        context = QgsExpressionContext(self.context)
        context.appendScope(self.navigator.createExpressionContextScope())
        context.appendScope(QgsExpressionContextUtils.mapSettingsScope(settings))
        settings.setExpressionContext(context)
        final = self.path(frame)
        item = {"frame": frame, "part": final + ".part", "final": final}
        item["task"] = task = QgsMapRendererTask(settings, item["part"], "PNG")
        if self.decorations:
            task.addDecorations(self.decorations)
        task.renderingComplete.connect(lambda: self._ended(item, True))
        task.errorOccurred.connect(lambda _code: self._ended(item, False))
        self.current = item
        self.entry["task"] = task
        QgsApplication.taskManager().addTask(task)
        self.longest_start_ms = max(self.longest_start_ms, 1000 * (time.perf_counter() - started))

    def _ended(self, item: dict, ok: bool) -> None:
        """A frame task's end signal, on the main thread."""
        if self.current is item:
            self.current = None
            self.entry.pop("task", None)
        elif self.entry["status"] == "running":
            return
        try:
            self._settled(item, ok)
            self.advance()
        except Exception as exc:  # noqa: BLE001 - a Qt slot must not raise
            log_warning(f"{TOOL}: frame {item['frame']}: {exc}")
            self.error = f"Frame {item['frame']}: {exc}"[:300]
            self._fail()

    def _settled(self, item: dict, ok: bool) -> None:
        if self.entry["status"] != "running":

            self._remove(item["part"], item["final"])
            self._remove_folder()
            return
        if not ok or not os.path.isfile(item["part"]):
            self._remove(item["part"])
            self.error = (f"Frame {item['frame']} could not be rendered or written to {self.folder}: check the "
                          "folder has room, or export a smaller frame.")
            self._fail()
            return
        try:
            retry_file_op(os.replace, item["part"], item["final"])
        except OSError as exc:


            self._remove(item["part"])
            self.error = (f"Frame {item['frame']} was rendered but could not be moved to {item['final']}, "
                          f"which another program may hold open: {exc}")[:300]
            self._fail()
            return
        self.written.append((item["final"], os.path.getsize(item["final"])))
        self.entry["progress"] = int(100 * len(self.written) / self.count)

    def cancel(self) -> None:
        """Stop, through cancel_task, which marked the entry canceled first: the render stops, the frames go."""



        self.pending = []
        if self.current is not None and not sip.isdeleted(self.current["task"]):
            self.current["task"].cancel()
        self._clear_written()
        self._remove_folder()
        self.ended = time.monotonic()

    def _fail(self) -> None:
        self.pending = []
        self.entry["status"] = "error"
        if self.current is not None and not sip.isdeleted(self.current["task"]):
            self.current["task"].cancel()
        self._clear_written()
        self._remove_folder()
        self._finish("error")

    def _finish(self, status: str) -> None:
        self.entry.update(status=status, progress=100 if status == "complete" else self.entry["progress"])
        self.ended = time.monotonic()

    def _clear_written(self) -> None:
        for path, _size_bytes in self.written:
            self._remove(path)
        self.removed += len(self.written)
        self.written = []

    @staticmethod
    def _remove(*paths: str) -> None:
        for path in paths:
            if os.path.isfile(path) and not remove_quietly(path):
                log_warning(f"{TOOL}: {path} not removed: another program holds it.")

    def _remove_folder(self) -> None:
        """The folder this call made, once empty and no frame of it still renders."""
        if not self.created or self.current is not None:
            return
        try:
            if os.path.isdir(self.folder) and not os.listdir(self.folder):
                os.rmdir(self.folder)
        except OSError as exc:
            log_warning(f"{TOOL}: folder {self.folder} not removed: {exc}")


__all__ = ["TOOL", "FrameExport", "export_animation_frames", "running_export"]

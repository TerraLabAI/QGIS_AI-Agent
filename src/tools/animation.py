# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








































from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import uuid

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import background, limits, net, output_paths, security
from ..core.background import run_on_main_thread
from ..core.host_platform import IS_WINDOWS, remove_quietly, retry_file_op
from ..core.tool_registry import Tool, ToolRegistry, tool_error

TOOL = "make_animation"


_SIGNATURE = b"GIF89a"
_EXT_INTRODUCER = 0x21
_EXT_GRAPHIC_CONTROL = 0xF9
_EXT_APPLICATION = 0xFF
_IMAGE_SEPARATOR = 0x2C
_TRAILER = 0x3B
_PALETTE_COLOURS = 256
_LSD_PACKED_256 = 0xF7
_DISPOSAL_DO_NOT_DISPOSE = 1

_SAMPLE_FRAMES = 8
_MOSAIC_TILE_W, _MOSAIC_TILE_H = 160, 120
_STOP_POLL_EVERY = 1

_FRAME_NUMBER_RE = re.compile(r"(\d+)(?=\.[Pp][Nn][Gg]$)")




def register_animation_tools(registry: ToolRegistry):


    registry.register(Tool(
        name="make_animation",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Turn {frames_folder} into a GIF"),
        input_schema={
            "type": "object",
            "properties": {
                "frames_folder": {"type": "string"},
                "prefix": {"type": "string"},
                "fps": {"type": "number", "minimum": 0.1, "maximum": 60},
                "loop": {"type": "boolean"},
                "hold_last_s": {"type": "number", "minimum": 0, "maximum": 30},
                "width": {"type": "integer", "minimum": 16},
                "output_path": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["frames_folder"],
        },
        handler=_make_animation,




        background=True,
    ))


def _default_fps() -> float | None:







    from .temporal_tools import _canvas_and_controller, _navigation_mode

    canvas, controller = _canvas_and_controller()
    if canvas is None or controller is None or controller.navigationMode() != _navigation_mode("Animated"):
        return None
    rate = controller.framesPerSecond()
    return float(rate) if rate and rate > 0 else None


def _list_frames(folder: str, prefix: str | None) -> list[str]:





    prefix = prefix or ""
    found = []
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    for name in names:
        if prefix and not name.startswith(prefix):
            continue
        if not name.lower().endswith(".png"):
            continue
        match = _FRAME_NUMBER_RE.search(name)
        if not match:
            continue
        found.append((int(match.group(1)), name))
    found.sort(key=lambda pair: pair[0])
    return [os.path.join(folder, name) for _, name in found]


def _check_args(args: dict) -> dict | None:
    asked = str(args.get("frames_folder") or "").strip()
    if not asked:
        return tool_error("frames_folder is required: make_animation does not keep a session's last export.",
                          "INVALID_ARGS",
                          "export_animation_frames answers out_dir; QGIS's own "
                          "Export Animation also writes a folder.")
    folder = security.expand_path(asked)
    if not os.path.isdir(folder):
        return tool_error(f"{folder} is not a folder.", "INVALID_ARGS",
                          "export_animation_frames (out_dir) or Export Animation gives the frames folder.")
    error = security.validate_path(folder, write=False)
    if error:
        return tool_error(error, "PERMISSION_DENIED", "Frames folders sit under your home folder or the "
                                                       "project folder.")
    return None


def _scaled_size(width: int, height: int, target_width: int | None) -> tuple[int, int]:
    if not target_width or target_width >= width:
        return width, height
    new_height = max(1, round(height * target_width / width))
    return target_width, new_height




def _gdal_open(gdal, path: str):








    try:
        return gdal.Open(path)
    except RuntimeError:
        return None


def _frame_sizes(gdal, frames: list[str], cancelled) -> list[tuple[int, int]] | dict:

    sizes = []
    for index, path in enumerate(frames, 1):
        if net.is_cancelled(cancelled):
            return _stopped()
        dataset = _gdal_open(gdal, path)
        if dataset is None:
            return tool_error(f"{os.path.basename(path)} could not be opened as an image.", "EXECUTION_FAILED",
                              "A partial export leaves an unreadable PNG behind.")
        sizes.append((dataset.RasterXSize, dataset.RasterYSize))
        dataset = None
        background.breathe(index)
    return sizes


def _as_rgb_mem(gdal, path: str, width: int, height: int):

    dataset = _gdal_open(gdal, path)
    if dataset is None:
        return None
    needs_resize = (dataset.RasterXSize, dataset.RasterYSize) != (width, height)
    needs_expand = dataset.RasterCount < 3
    if not needs_resize and not needs_expand:
        return dataset
    kwargs = {"format": "MEM", "width": width, "height": height}
    if needs_expand:
        kwargs["rgbExpand"] = "rgb"
    options = gdal.TranslateOptions(**kwargs)
    try:
        resized = gdal.Translate("", dataset, options=options)
    except RuntimeError:
        return None
    dataset = None
    return resized


def _build_palette(gdal, frames: list[str], sizes: list[tuple[int, int]], cancelled):

    step = max(1, len(frames) // _SAMPLE_FRAMES)
    sample = list(range(0, len(frames), step))[:_SAMPLE_FRAMES]
    mosaic = gdal.GetDriverByName("MEM").Create("", _MOSAIC_TILE_W * len(sample), _MOSAIC_TILE_H, 3, gdal.GDT_Byte)
    for tile, frame_index in enumerate(sample):
        if net.is_cancelled(cancelled):
            return _stopped()
        width, height = sizes[frame_index]
        tile_ds = _as_rgb_mem(gdal, frames[frame_index], min(width, _MOSAIC_TILE_W), min(height, _MOSAIC_TILE_H))
        if tile_ds is None:
            return tool_error(f"{os.path.basename(frames[frame_index])} could not be read for the palette.",
                              "EXECUTION_FAILED", "The frame is not a readable PNG.")
        tile_w, tile_h = tile_ds.RasterXSize, tile_ds.RasterYSize
        for band in range(3):





            data = tile_ds.GetRasterBand(band + 1).ReadRaster(0, 0, tile_w, tile_h)
            if data is None:
                return tool_error(f"{os.path.basename(frames[frame_index])} could not be read for the palette: "
                                  "its pixel data looks truncated.", "EXECUTION_FAILED",
                                  "A partial export leaves an incomplete PNG behind.")
            mosaic.GetRasterBand(band + 1).WriteRaster(tile * _MOSAIC_TILE_W, 0, tile_w, tile_h, data)
        tile_ds = None
    colours = gdal.ColorTable()
    gdal.ComputeMedianCutPCT(mosaic.GetRasterBand(1), mosaic.GetRasterBand(2), mosaic.GetRasterBand(3),
                              _PALETTE_COLOURS, colours)
    mosaic = None
    return colours


def _dither_frame_to_gif_bytes(gdal, path: str, width: int, height: int, colours, tag: str, vsimem_paths: list):

    source = _as_rgb_mem(gdal, path, width, height)
    if source is None:
        return None
    try:
        target = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
        gdal.DitherRGB2PCT(source.GetRasterBand(1), source.GetRasterBand(2), source.GetRasterBand(3),
                           target.GetRasterBand(1), colours)
        target.GetRasterBand(1).SetRasterColorTable(colours)
        target.GetRasterBand(1).SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
        vsi_path = f"/vsimem/make_animation_{tag}.gif"
        vsimem_paths.append(vsi_path)
        gif = gdal.GetDriverByName("GIF").CreateCopy(vsi_path, target)
        del gif
    except RuntimeError:
        return None
    finally:
        target = None
        source = None
    handle = gdal.VSIFOpenL(vsi_path, "rb")
    if handle is None:
        return None
    try:
        gdal.VSIFSeekL(handle, 0, 2)
        size = gdal.VSIFTellL(handle)
        gdal.VSIFSeekL(handle, 0, 0)
        data = gdal.VSIFReadL(1, size, handle)
    finally:
        gdal.VSIFCloseL(handle)
    return bytes(data) if data is not None else None


def _read_frame_gif(data: bytes):






    if data[:6] not in (b"GIF87a", b"GIF89a"):
        raise ValueError("not a GIF")
    width, height = struct.unpack_from("<HH", data, 6)
    packed = data[10]
    table_size = 2 ** ((packed & 0x07) + 1) if packed & 0x80 else 0
    position = 13
    table = data[position:position + table_size * 3]
    position += table_size * 3
    while position < len(data) and data[position] == _EXT_INTRODUCER:
        position += 2
        while True:
            block_size = data[position]
            position += 1
            if block_size == 0:
                break
            position += block_size
    if position >= len(data) or data[position] != _IMAGE_SEPARATOR:
        raise ValueError("no image descriptor")
    image_width, image_height = struct.unpack_from("<HH", data, position + 5)
    image_packed = data[position + 9]
    position += 10
    if image_packed & 0x80:
        local_size = 2 ** ((image_packed & 0x07) + 1)
        position += local_size * 3
    start = position
    position += 1
    while True:
        block_size = data[position]
        position += 1
        if block_size == 0:
            break
        position += block_size
    return image_width, image_height, table, data[start:position]


def _write_gif(part_path: str, frame_blocks: list, table: bytes, canvas_w: int, canvas_h: int,
               delay_cs: int, loop: bool, hold_last_cs: int, cancelled):





    ceiling = int(limits.current("ANIMATION_MAX_OUTPUT_BYTES"))
    padded_table = (table + b"\x00" * (_PALETTE_COLOURS * 3))[:_PALETTE_COLOURS * 3]
    written = 0
    failure: dict | None = None
    with open(part_path, "wb") as handle:
        def emit(chunk: bytes):
            nonlocal written
            handle.write(chunk)
            written += len(chunk)

        emit(_SIGNATURE)
        emit(struct.pack("<HH", canvas_w, canvas_h))
        emit(bytes([_LSD_PACKED_256, 0x00, 0x00]))
        emit(padded_table)
        if loop:
            emit(bytes([_EXT_INTRODUCER, _EXT_APPLICATION, 0x0B]) + b"NETSCAPE2.0"
                 + bytes([0x03, 0x01, 0x00, 0x00, 0x00]))
        total = len(frame_blocks)
        for index, (width, height, block) in enumerate(frame_blocks):
            if index % _STOP_POLL_EVERY == 0 and net.is_cancelled(cancelled):
                failure = _stopped()
                break
            delay = delay_cs + (hold_last_cs if index == total - 1 else 0)
            control_packed = _DISPOSAL_DO_NOT_DISPOSE << 2
            emit(bytes([_EXT_INTRODUCER, _EXT_GRAPHIC_CONTROL, 0x04, control_packed])
                 + struct.pack("<H", min(delay, 0xFFFF)) + bytes([0x00, 0x00]))
            emit(bytes([_IMAGE_SEPARATOR]) + struct.pack("<HHHH", 0, 0, width, height) + bytes([0x00]))
            emit(block)
            if written > ceiling:
                per_frame = max(1, written // max(1, index + 1))
                fits = max(1, ceiling // per_frame)
                failure = limits.refusal(
                    "The GIF", f"over {written:,} bytes at {index + 1} of {total} frames",
                    f"{ceiling:,} bytes",
                    f"Make about {fits:,} frames at this size and width, or pass a smaller width.")
                break
        else:
            emit(bytes([_TRAILER]))
    if failure is not None:
        remove_quietly(part_path)
        return failure
    return written




_WINDOWS_FFMPEG_CANDIDATES = (
    r"%ProgramFiles%\ffmpeg\bin\ffmpeg.exe",
    r"%ProgramFiles(x86)%\ffmpeg\bin\ffmpeg.exe",
    r"%ChocolateyInstall%\bin\ffmpeg.exe",
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe",
    r"%USERPROFILE%\scoop\shims\ffmpeg.exe",
    r"C:\ffmpeg\bin\ffmpeg.exe",
)


def _find_ffmpeg() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    if not IS_WINDOWS:
        return None
    for candidate in _WINDOWS_FFMPEG_CANDIDATES:
        expanded = os.path.expandvars(candidate)
        if "%" not in expanded and os.path.isfile(expanded):
            return expanded
    return None


def _concat_line(path: str) -> str:






    safe = path.replace("'", "'\\''")
    return f"file '{safe}'"


def _write_concat_list(frames: list[str], fps: float, hold_last_s: float) -> str:
    handle, list_path = tempfile.mkstemp(prefix="make_animation_", suffix=".txt")
    duration = 1.0 / fps
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
        for index, path in enumerate(frames):
            file.write(_concat_line(path) + "\n")
            this_duration = duration + (hold_last_s if index == len(frames) - 1 else 0.0)
            file.write(f"duration {this_duration:.6f}\n")

        file.write(_concat_line(frames[-1]) + "\n")
    return list_path


def _encode_mp4(ffmpeg: str, frames: list[str], fps: float, width: int | None, hold_last_s: float,
                 mp4_target: str) -> dict:
    list_path = _write_concat_list(frames, fps, hold_last_s)
    part_path = mp4_target + ".part"



    even_width = width - (width % 2) if width else None
    scale = f"scale={even_width}:-2" if even_width else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    args = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", list_path,
            "-vf", scale, "-r", f"{fps:.3f}", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-f", "mp4", part_path]
    kwargs: dict = {}
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        kwargs["creationflags"] = flags
    timeout_s = float(limits.current("ANIMATION_MP4_TIMEOUT_S"))
    try:
        completed = subprocess.run(  # nosec B603
            args, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout_s, **kwargs)
    except subprocess.TimeoutExpired:
        remove_quietly(part_path)
        return tool_error(f"ffmpeg did not finish the MP4 within {timeout_s:.0f} s.", "EXECUTION_FAILED",
                          "Fewer frames or a smaller width fit the timeout; the GIF this call made already stands.")
    except OSError as exc:
        remove_quietly(part_path)
        return tool_error(f"ffmpeg could not be started: {exc}", "EXECUTION_FAILED",
                          "The GIF this call made is unaffected.")
    finally:
        remove_quietly(list_path)
    if completed.returncode != 0 or not os.path.isfile(part_path):
        remove_quietly(part_path)
        tail = (completed.stderr or "")[-800:]
        return tool_error(f"ffmpeg exited {completed.returncode}: {tail}", "EXECUTION_FAILED",
                          "The GIF this call made is unaffected.")
    try:
        retry_file_op(os.replace, part_path, mp4_target)
    except OSError as exc:
        remove_quietly(part_path)
        return tool_error(f"The MP4 was encoded but could not be moved to {mp4_target}: {exc}", "EXECUTION_FAILED",
                          "Another program may hold that name open.")
    return {"path": mp4_target, "size_bytes": os.path.getsize(mp4_target)}




def _targets(args: dict, frames_folder: str):








    asked = str(args.get("output_path") or "").strip()
    if asked:
        expanded = security.expand_path(asked)
        extension = os.path.splitext(expanded)[1].lower()
        if not extension:
            expanded += ".gif"
        elif extension != ".gif":
            return None, None, tool_error(f"{os.path.basename(expanded)} is not a GIF name.", "INVALID_ARGS",
                                          "output_path must end in .gif: this tool always writes a GIF.")
        error = security.validate_path(expanded, write=True)
        if error:
            return None, None, tool_error(error, "PERMISSION_DENIED",
                                          "Paths sit under the project folder, your home folder or the temp "
                                          "folder.")
        mp4_candidate = os.path.splitext(expanded)[0] + ".mp4"
        if args.get("overwrite") is not True:
            taken = expanded if os.path.exists(expanded) else mp4_candidate if os.path.exists(mp4_candidate) else None
            if taken:
                return None, None, tool_error(f"{taken} already exists.", "INVALID_ARGS",
                                              "The file exists; overwrite true replaces it, or a new "
                                              "file name keeps it, the user's call.")
        gif_path = expanded
        mp4_path = mp4_candidate
    else:
        stem = output_paths.safe_file_name(os.path.basename(os.path.normpath(frames_folder)), "animation")
        if stem.endswith("_frames"):
            stem = stem[: -len("_frames")] or "animation"
        stem = re.sub(r"\s+", "_", stem)[:120]
        folder = output_paths.default_folder()
        gif_path = mp4_path = None
        for index in range(1, 1000):
            gif_candidate = os.path.join(folder, f"{stem}_{index}.gif" if index > 1 else f"{stem}.gif")
            mp4_candidate = os.path.splitext(gif_candidate)[0] + ".mp4"
            if not os.path.exists(gif_candidate) and not os.path.exists(mp4_candidate):
                error = security.validate_path(gif_candidate, write=True)
                if error:
                    return None, None, tool_error(error, "PERMISSION_DENIED",
                                                  "output_path sits under your home folder.")
                gif_path, mp4_path = gif_candidate, mp4_candidate
                break
        if gif_path is None:
            return None, None, tool_error(f"{folder} already holds 999 animations named {stem}.", "INVALID_ARGS",
                                          "output_path needs a new name.")
    return gif_path, mp4_path, None




def _stopped() -> dict:
    return tool_error("Stopped before the animation was written.", "CANCELLED", "No file was written.")




def _make_animation(args: dict) -> dict:
    from osgeo import gdal

    refused = _check_args(args)
    if refused:
        return refused
    started = time.monotonic()
    cancelled = net.current_cancel_check()
    folder = security.expand_path(str(args["frames_folder"]).strip())
    frames = _list_frames(folder, args.get("prefix"))
    if not frames:
        return tool_error(f"No numbered PNG frames were found in {folder}"
                          + (f" starting with {args['prefix']!r}" if args.get("prefix") else "") + ".",
                          "INVALID_ARGS",
                          "export_animation_frames (out_dir) or Export Animation names the folder; prefix "
                          "narrows it when several exports share one.")

    fps = args.get("fps")
    if fps is None:
        fps = run_on_main_thread(_default_fps, timeout=10) or 2.0
    fps = max(0.1, float(fps))
    loop = args.get("loop", True) is not False
    hold_last_s = float(args.get("hold_last_s") or 0.0)
    width_arg = args.get("width")







    gdal.UseExceptions()
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        sizes = _frame_sizes(gdal, frames, cancelled)
        if isinstance(sizes, dict):
            return sizes
        target_sizes = [_scaled_size(w, h, width_arg) for w, h in sizes]
        total_pixels = sum(w * h for w, h in target_sizes)
        ceiling = int(limits.current("ANIMATION_ENCODE_MAX_PIXELS"))
        if total_pixels > ceiling:
            biggest = max(w * h for w, h in target_sizes) or 1
            frames_that_fit = max(1, ceiling // biggest)
            narrower = max(16, int((ceiling / len(frames)) ** 0.5)) if frames else 16
            return limits.refusal(
                "The animation", f"{len(frames):,} frames of up to {max(w for w, h in target_sizes):,} by "
                f"{max(h for w, h in target_sizes):,} pixels ({total_pixels:,} pixels in all)",
                f"{ceiling:,} pixels in all",
                f"Export about {frames_that_fit:,} frames at this size, or pass width {narrower:,} to keep every "
                f"frame.")

        gif_path, mp4_path, refusal = _targets(args, folder)
        if refusal:
            return refusal

        vsimem_paths: list[str] = []
        try:
            colours = _build_palette(gdal, frames, sizes, cancelled)
            if isinstance(colours, dict):
                return colours
            frame_blocks = []
            canvas_w = max(w for w, h in target_sizes)
            canvas_h = max(h for w, h in target_sizes)
            for index, path in enumerate(frames):
                if net.is_cancelled(cancelled):
                    return _stopped()
                width, height = target_sizes[index]
                data = _dither_frame_to_gif_bytes(gdal, path, width, height, colours, f"{uuid.uuid4().hex[:8]}_"
                                                  f"{index}", vsimem_paths)
                if data is None:
                    return tool_error(f"{os.path.basename(path)} could not be dithered or written.",
                                      "EXECUTION_FAILED", "The frame may be unreadable, or disk lacks room "
                                                          "for the temporary work.")
                try:
                    parsed_w, parsed_h, table, block = _read_frame_gif(data)
                except (ValueError, IndexError):
                    return tool_error(f"{os.path.basename(path)} produced a GIF this tool could not parse back.",
                                      "EXECUTION_FAILED",
                                      "A partial export leaves a truncated PNG behind.")
                if index == 0:
                    global_table = table
                frame_blocks.append((parsed_w, parsed_h, block))
                background.breathe(index + 1)
        finally:
            for vsi_path in vsimem_paths:
                gdal.Unlink(vsi_path)
    finally:
        gdal.PopErrorHandler()

    part_path = gif_path + ".part"
    if not security.fits_path(part_path):
        part_path = os.path.join(os.path.dirname(gif_path), f".make_animation_{uuid.uuid4().hex[:12]}.gif.part")
    outcome = _write_gif(part_path, frame_blocks, global_table, canvas_w, canvas_h, round(100 / fps), loop,
                        round(hold_last_s * 100), cancelled)
    if isinstance(outcome, dict):
        return outcome
    gif_bytes = outcome
    try:
        retry_file_op(os.replace, part_path, gif_path)
    except OSError as exc:
        remove_quietly(part_path)
        return tool_error(f"The GIF was written but could not be moved to {gif_path}: {exc}", "EXECUTION_FAILED",
                          "Another program may hold that name open.")

    result = {
        "path": gif_path, "size_bytes": gif_bytes,
        "gif": {"path": gif_path, "size_bytes": gif_bytes},
        "frame_count": len(frames), "fps": round(fps, 3), "loop": loop,
        "duration_s": round(len(frames) / fps + hold_last_s, 2),
        "size_px": [canvas_w, canvas_h],
        "palette": f"one 256-colour palette by median cut over {min(len(frames), _SAMPLE_FRAMES)} sampled frames, "
                   "dithered per frame",
        "seconds": round(time.monotonic() - started, 1),
    }
    ffmpeg = _find_ffmpeg()
    if ffmpeg is None:
        result["mp4"] = None
        result["note"] = "No ffmpeg executable was found on this machine, so only the GIF was made. Installing " \
                          "ffmpeg (ffmpeg.org) and calling make_animation again would also make an MP4."
        return result
    mp4_outcome = _encode_mp4(ffmpeg, frames, fps, width_arg, hold_last_s, mp4_path)
    if "_error" in mp4_outcome:
        result["mp4"] = None
        result["note"] = f"The GIF was made; the MP4 was not: {mp4_outcome['_error']}"
    else:
        result["mp4"] = mp4_outcome
    return result


__all__ = ["TOOL", "register_animation_tools"]

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
import base64
import difflib
import hashlib
import importlib
import os
import re
import time
import traceback

import qgis
from qgis.core import (
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsLayoutExporter,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSize
from qgis.utils import iface

from ..core import code_guard, limits
from ..core.background import run_on_main_thread
from ..core.qt_compat import enum_member, field_type
from ..core.security import safe_read_text, validate_path
from ..core.serialization import cut_string, size_budget
from ..core.tool_registry import Tool, ToolRegistry
from .core_tools import _find_layer, _layer_not_found_error


def register_advanced_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="execute_code",
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                },
            },
            "required": ["code"],
        },
        handler=_execute_code,
        destructive=True,
    ))

    registry.register(Tool(
        name="render_map",
        input_schema={
            "type": "object",
            "properties": {
                "width": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 3840,
                },
                "height": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2160,
                },
                "extent": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "layer_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "crs": {"type": "string"},
                "background": {
                    "type": "string",
                },
                "warmup": {
                    "type": "boolean",
                },
                "save_path": {
                    "type": "string",
                },
                "overwrite": {"type": "boolean"},
            },
            "required": [],
        },
        handler=_render_map,
        background=True,
    ))

    registry.register(Tool(
        name="render_detection_reveal",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                },
                "out_dir": {"type": "string"},
                "steps": {"type": "integer", "minimum": 1, "maximum": 120},
                "order": {
                    "type": "string",
                    "enum": ["random", "area_desc", "area_asc", "top_down", "left_right"],
                },
                "extent": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "base_layer_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "width": {"type": "integer", "minimum": 1, "maximum": 3840},
                "height": {"type": "integer", "minimum": 1, "maximum": 2160},
                "prefix": {"type": "string"},
                "random_colors": {
                    "type": "boolean",
                },
                "fill_color": {
                    "type": "string",
                },
                "background": {
                    "type": "string",
                },
                "warmup": {
                    "type": "boolean",
                },
            },
            "required": ["layer_name", "out_dir"],
        },
        handler=_render_detection_reveal,
        background=True,
    ))

    registry.register(Tool(
        name="render_camera_move",
        input_schema={
            "type": "object",
            "properties": {
                "out_dir": {"type": "string"},
                "from_extent": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "to_extent": {
                    "type": "object",
                    "properties": {
                        "xmin": {"type": "number"},
                        "ymin": {"type": "number"},
                        "xmax": {"type": "number"},
                        "ymax": {"type": "number"},
                    },



                    "required": ["xmin", "ymin", "xmax", "ymax"],
                },
                "to_layer": {
                    "type": "string",
                },
                "to_zoom": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 1000000,
                },
                "steps": {"type": "integer", "minimum": 2, "maximum": 120},
                "layer_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 200,
                },
                "width": {"type": "integer", "minimum": 1, "maximum": 3840},
                "height": {"type": "integer", "minimum": 1, "maximum": 2160},
                "prefix": {"type": "string"},
                "background": {"type": "string"},
                "warmup": {
                    "type": "boolean",
                },
            },
            "required": ["out_dir"],
        },
        handler=_render_camera_move,
        background=True,
    ))

    registry.register(Tool(
        name="list_layouts",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_list_layouts,
    ))

    registry.register(Tool(
        name="export_layout",
        input_schema={
            "type": "object",
            "properties": {
                "layout_name": {"type": "string"},
                "output_path": {"type": "string"},
                "format": {
                    "type": "string",
                    "enum": ["pdf", "png", "jpg", "svg"],
                },
                "dpi": {"type": "integer", "minimum": 10, "maximum": 600},
                "overwrite": {
                    "type": "boolean",
                },
                "georeference": {
                    "type": "boolean",
                },
                "force_vector": {
                    "type": "boolean",
                },
            },
            "required": ["layout_name", "output_path"],
        },
        handler=_export_layout,
    ))

    registry.register(Tool(
        name="get_message_log",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 5000},
                "tag": {
                    "type": "string",
                },
                "level": {
                    "type": "string",
                    "enum": ["info", "warning", "critical"],
                },
                "search": {"type": "string"},
                "max_message_chars": {
                    "type": "integer",
                    "minimum": 40,
                    "maximum": 12000,
                },
            },
            "required": [],
        },
        handler=_get_message_log,
    ))

    registry.register(Tool(
        name="get_debug_info",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_debug_info,
    ))

    _connect_message_log()


_QT_CORE_NAMES = ("QVariant", "Qt", "QSize", "QSizeF", "QPointF", "QPoint", "QRectF", "QRect",
                  "QDate", "QTime", "QDateTime", "QUrl")
_QT_GUI_NAMES = ("QColor", "QFont", "QImage", "QPainter", "QBrush", "QPen", "QTransform")


def _qt_names() -> dict:
    """The Qt classes execute_code offers by name, none of them in code_guard.DENIED_NAMES."""
    out: dict = {}
    for module, wanted in (("QtCore", _QT_CORE_NAMES), ("QtGui", _QT_GUI_NAMES)):
        try:
            mod = __import__(f"qgis.PyQt.{module}", fromlist=["*"])
        except ImportError:
            continue
        for name in wanted:
            if name in code_guard.DENIED_NAMES:
                continue
            value = getattr(mod, name, None)
            if value is not None:
                out[name] = value
    return out


def _execute_code(args: dict) -> dict:
    """Run code on the QGIS main thread inside the code guard (core/code_guard.py)."""





    code = args.get("code", "")
    if not code.strip():
        return {"_error": "No code provided"}



    refused = code_guard.refusal_for(code)
    if refused:
        return {"_error": refused["error"], "_code": refused["code"], "suggestion": refused["suggestion"]}

    stdout_capture = code_guard.CappedOutput()

    def _print(*args, **kwargs):
        kwargs.setdefault("file", stdout_capture)
        print(*args, **kwargs)






    namespace = {k: v for k, v in vars(qgis.core).items()
                 if (k.startswith("Qgs") or k == "NULL") and k not in code_guard.DENIED_NAMES}







    namespace.update(_qt_names())
    try:
        import processing as _processing
        namespace["processing"] = _processing
    except ImportError:
        pass
    namespace.update({
        "__builtins__": code_guard.build_safe_builtins(),
        "iface": iface,
        "QgsProject": QgsProject,
        "QgsApplication": QgsApplication,
        "QgsVectorLayer": QgsVectorLayer,
        "QgsRasterLayer": QgsRasterLayer,
        "QgsCoordinateReferenceSystem": QgsCoordinateReferenceSystem,
        "QgsMapSettings": QgsMapSettings,
        "qgis": qgis,
        "project": QgsProject.instance(),
        "print": _print,
        "read_text": safe_read_text,
        "validate_path": validate_path,
    })

    try:
        code_guard.run_with_timeout(
            lambda: exec(code, namespace),  # nosec B102 - execute_code requires plugin permission approval
            code_guard.DEFAULT_TIMEOUT_S,
        )


        out = {"executed": True}
        _put_capped(out, "stdout", stdout_capture.getvalue())
        if "result" in namespace:
            _put_capped(out, "result", str(namespace["result"]))
        else:
            out["result_set"] = False
        return out
    except code_guard.CodeTimeout:
        return {
            "executed": False,
            "_error": f"execute_code stopped after {code_guard.DEFAULT_TIMEOUT_S} s.",
            "_code": "EXEC_TIMEOUT",
            "stdout": _cap(stdout_capture.getvalue()),
            "suggestion": "Narrow the work (fewer features, one layer) or use run_processing with async=true.",
        }
    except Exception as e:



        out = {
            "executed": False,
            "_error": f"execute_code raised {type(e).__name__}: {e}",
            "_code": "EXEC_RUNTIME_ERROR",
            "stdout": _cap(stdout_capture.getvalue()),
            "traceback": _cap(traceback.format_exc()),
        }
        help_text = _api_help(e)
        if help_text:
            out["api"] = help_text
        return out


















_ATTR_RE = re.compile(r"'([A-Za-z_][\w]*)'(?: object)? has no attribute '([A-Za-z_][\w]*)'")



_NAME_RE = re.compile(r"name '([A-Za-z_][\w]*)' is not defined")



_CALL_RE = re.compile(r"([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\(\)")
_ENUM_RE = re.compile(r"member of enum '([A-Za-z_][\w]*)' is expected not '([A-Za-z_][\w]*)'")
_API_MODULES = ("qgis.core", "qgis.gui", "qgis.PyQt.QtCore", "qgis.PyQt.QtGui", "qgis.PyQt.QtWidgets")
_API_HELP_CHARS = 600


def _api_class(name: str):
    for module_name in _API_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        found = getattr(module, name, None)
        if isinstance(found, type):
            return found
    return None


def _api_enum(name: str):
    """An enum by name, whether it sits at module level or under ``Qgis``."""
    found = _api_class(name)
    if found is not None:
        return found
    for module_name in _API_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for holder in ("Qgis", "QgsPalLayerSettings", "QgsWkbTypes"):
            parent = getattr(module, holder, None)
            nested = getattr(parent, name, None) if parent is not None else None
            if nested is not None and hasattr(nested, "__members__"):
                return nested
    return None


def _api_module_of(name: str) -> str:
    """The module a bare name lives in, or ''."""
    for module_name in _API_MODULES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if getattr(module, name, None) is not None:
            return module_name
    return ""


_FAMILY_SHOWN = 8


def _api_family(cls_name: str, wanted: str, public: list) -> str:
    """The members that share the verb *wanted* starts with, or ''."""
    verb = ""
    for ch in wanted:
        if ch.islower() or ch == "_":
            verb += ch
        else:
            break
    if len(verb) < 2 or verb == wanted:
        return ""
    kin = sorted(a for a in public if a.startswith(verb) and a != wanted)
    if not kin:
        return ""
    shown = ", ".join(kin[:_FAMILY_SHOWN])
    more = "" if len(kin) <= _FAMILY_SHOWN else f", and {len(kin) - _FAMILY_SHOWN} more"
    return f"{cls_name} has no {wanted}. Its {verb} methods are: {shown}{more}."


def _api_help(exc: Exception) -> str:
    """The real signature, or the names that do exist, for a call that was wrong."""
    text = str(exc)
    try:
        if isinstance(exc, AttributeError):
            match = _ATTR_RE.search(text)
            if not match:
                return ""
            cls = _api_class(match.group(1))
            if cls is None:
                return ""
            public = [a for a in dir(cls) if not a.startswith("_")]
            near = difflib.get_close_matches(match.group(2), public, n=5, cutoff=0.6)
            if not near:




                return _api_family(match.group(1), match.group(2), public)
            return f"{match.group(1)} has no {match.group(2)}. Nearest: " + ", ".join(near) + "."
        if isinstance(exc, NameError):
            match = _NAME_RE.search(text)
            if not match:
                return ""
            wanted = match.group(1)
            module_name = _api_module_of(wanted)
            if not module_name:
                return ""
            return (f"{wanted} is real but execute_code does not bind it. It is in {module_name}: "
                    f"write from {module_name} import {wanted}, or {module_name}.{wanted} where "
                    "qgis is already bound.")
        if isinstance(exc, TypeError):
            enum = _ENUM_RE.search(text)
            if enum:
                family = _api_enum(enum.group(1))
                members = list(getattr(family, "__members__", {}) or {})
                if members:
                    return (f"{enum.group(2)} belongs to another family. {enum.group(1)} is: "
                            + ", ".join(members[:12]) + ("." if len(members) <= 12 else ", ..."))
                return ""
            match = _CALL_RE.search(text)
            if not match:
                return ""
            cls = _api_class(match.group(1))
            member = getattr(cls, match.group(2), None) if cls is not None else None
            doc = (getattr(member, "__doc__", "") or "").strip()
            if not doc:
                return ""
            lines = [line.strip() for line in doc.splitlines() if line.strip()][:6]






            return cut_string("\n".join(lines), _API_HELP_CHARS)
    except Exception:  # noqa: BLE001 - a hint that cannot be built is a hint the model does without
        return ""
    return ""




_MAX_OUTPUT = 12_000


def _cap(text: str) -> str:
    return cut_string(text, _MAX_OUTPUT)


def _put_capped(out: dict, key: str, text: str) -> None:
    """Store the text cut to head and tail, with its true length when it was cut."""
    out[key] = _cap(text)
    if len(text) > _MAX_OUTPUT:
        out[f"{key}_chars"] = len(text)


def _encode_jpeg(image, quality=70) -> bytes:
    from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(enum_member(QIODevice, "OpenModeFlag", "WriteOnly"))
    image.save(buf, "JPEG", quality)
    buf.close()
    return bytes(ba)





_MAX_IMAGE_BYTES = 1_500_000


def _jpeg_under_cap(image, quality=70):
    """``(jpeg bytes, image)`` with the image downscaled until the JPEG fits the cap."""
    from qgis.PyQt.QtCore import Qt
    raw = _encode_jpeg(image, quality)
    smooth = enum_member(Qt, "TransformationMode", "SmoothTransformation")
    while len(raw) > _MAX_IMAGE_BYTES and image.width() > 320:


        scale = max(0.5, (_MAX_IMAGE_BYTES / len(raw)) ** 0.5 * 0.9)
        image = image.scaledToWidth(int(image.width() * scale), smooth)
        raw = _encode_jpeg(image, quality)
    return raw, image


def _apply_quality_flags(settings: "QgsMapSettings") -> None:
    """Turn on the flags that make a still look publication-clean: antialiased geometry, advanced effects (layer opacity/blend), and smooth raster."""


    for flag_name in ("Antialiasing", "UseAdvancedEffects", "HighQualityImageTransforms", "DrawLabels"):
        flag = getattr(QgsMapSettings, flag_name, None)
        if flag is not None:
            try:
                settings.setFlag(flag, True)
            except Exception:  # nosec B110 - render enhancement is optional
                pass


def _is_tiled_raster(layer) -> bool:
    """A layer whose content arrives over the network after the render starts."""








    try:
        if isinstance(layer, QgsRasterLayer):
            return layer.providerType() in ("wms", "wmts", "xyz")
        return isinstance(layer, _vector_tile_class())
    except Exception:  # nosec B110 - render enhancement is optional
        return False


def _vector_tile_class():
    """``QgsVectorTileLayer``, or a class nothing is an instance of."""





    global _VECTOR_TILE_CLASS
    if _VECTOR_TILE_CLASS is None:
        try:
            from qgis.core import QgsVectorTileLayer

            _VECTOR_TILE_CLASS = QgsVectorTileLayer
        except ImportError:
            _VECTOR_TILE_CLASS = _NoLayer
    return _VECTOR_TILE_CLASS


class _NoLayer:
    """Stands in for a layer class this QGIS does not have."""


_VECTOR_TILE_CLASS = None


def _warm_tiles(settings, passes: int = 1) -> None:
    """XYZ/WMS/WMTS tiles load asynchronously, so a cold offscreen render can come back blank."""


    try:
        if not any(_is_tiled_raster(layer) for layer in settings.layers()):
            return
        for _ in range(max(1, passes)):
            warm = QgsMapRendererParallelJob(settings)
            warm.start()
            warm.waitForFinished()
    except Exception:  # nosec B110 - tile warmup is optional
        pass


def _apply_background(settings, background) -> None:
    """background='transparent' renders with an alpha channel (save as .png) so the result composites over other footage."""

    if background == "transparent":
        try:
            from qgis.PyQt.QtGui import QColor
            settings.setBackgroundColor(QColor(0, 0, 0, 0))
        except Exception:  # nosec B110 - render enhancement is optional
            pass







_MAX_RENDER_WIDTH = limits.MAX_RENDER_WIDTH_PX
_MAX_RENDER_HEIGHT = limits.MAX_RENDER_HEIGHT_PX


def _hash_image(image) -> str:
    """md5 of the raw pixel bytes, so two renders can be compared for equality without a pixel-by-pixel loop in Python."""

    bits = image.bits()
    bits.setsize(image.sizeInBytes())
    return hashlib.sha256(bytes(bits)).hexdigest()








MAX_FRAME_STEPS = 120

_UNSAFE_IN_A_FRAME_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _empty_memory_provider(provider) -> None:
    """Empty a provider we are about to re-fill, without using ``truncate()``."""














    try:
        ids = list(provider.allFeatureIds())
    except (AttributeError, RuntimeError):
        ids = []
    if ids:
        provider.deleteFeatures(ids)
    else:
        provider.truncate()


def _safe_frame_prefix(value, default: str) -> str:
    """A frame prefix that can only name a file inside the chosen folder."""







    text = _UNSAFE_IN_A_FRAME_NAME.sub("_", str(value if value is not None else default))
    while ".." in text:
        text = text.replace("..", ".")
    return text.strip(" .") or default


def _prepare_frame_folder(path: str, prefix: str = ""):
    """Create a frame folder, or return a useful error for file/race failures."""







    if os.path.exists(path) and not os.path.isdir(path):
        return f"Frame out_dir is not a folder: {path}"
    from ..core.security import _MAX_PATH, _long_paths_ok

    frame_len = len(path) + 1 + len(prefix) + len("000.png")
    if frame_len >= _MAX_PATH and not _long_paths_ok():
        return (f"Each frame here would be a path of {frame_len} characters and Windows stops "
                f"this process at {_MAX_PATH}; choose a shorter out_dir or prefix.")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        return f"Could not create frame out_dir {path}: {exc}"
    return None


def _render_stable(settings, max_passes: int = 3):
    """Render until two consecutive passes hash identical, or max_passes is used up."""










    last_hash = None
    image = None
    errors = []
    passes = 0
    for _ in range(max(1, max_passes)):
        job = QgsMapRendererParallelJob(settings)
        job.start()
        job.waitForFinished()
        image = job.renderedImage()
        errors = [{"layer_id": e.layerID, "message": e.message} for e in job.errors()]
        passes += 1
        digest = _hash_image(image)
        if digest == last_hash:
            return image, passes, True, errors
        last_hash = digest
    return image, passes, False, errors




_RENDER_PASS_SECONDS = 120.0





_RENDER_TOTAL_SECONDS = 165.0

_RENDER_PASS_FLOOR_S = 8.0


def _render_pass_async(settings, budget: float = _RENDER_PASS_SECONDS):
    """One render whose wait happens on the worker, not the main thread."""









    import threading

    done = threading.Event()
    holder = {}

    def start():
        job = QgsMapRendererParallelJob(settings)
        holder["job"] = job
        job.finished.connect(done.set)
        job.start()

    run_on_main_thread(start, timeout=30)
    budget = max(_RENDER_PASS_FLOOR_S, min(float(budget), _RENDER_PASS_SECONDS))
    if not done.wait(budget):
        def cancel():
            try:
                holder["job"].cancelWithoutBlocking()
            except Exception:  # nosec B110 - a job past its budget is dropped either way
                pass
        run_on_main_thread(cancel, timeout=10)
        return None, [{"layer_id": "", "message": f"render not finished after {budget:.0f} s"}]

    def collect():
        job = holder["job"]
        return job.renderedImage(), [{"layer_id": e.layerID, "message": e.message} for e in job.errors()]

    return run_on_main_thread(collect, timeout=30)


def _image_is_uniform(image) -> bool:
    """True when every pixel of the render is the same colour."""







    try:
        width, height = image.width(), image.height()
        if width < 2 or height < 2:
            return True
        first = image.pixel(0, 0)
        steps = 16
        for row in range(steps):
            y = row * (height - 1) // (steps - 1)
            for column in range(steps):
                x = column * (width - 1) // (steps - 1)
                if image.pixel(x, y) != first:
                    return False
    except Exception:  # noqa: BLE001 - an image we cannot sample is not called blank
        return False
    return True


def _render_stable_async(settings, max_passes: int = 3, warm: bool = False):
    """``_render_stable`` with every wait on the worker."""







    deadline = time.monotonic() + _RENDER_TOTAL_SECONDS
    if warm:



        _render_pass_async(settings, _RENDER_TOTAL_SECONDS / 3.0)
    last_hash = None
    image = None
    errors = []
    passes = 0
    for _ in range(max(1, max_passes)):
        left = deadline - time.monotonic()
        if left < _RENDER_PASS_FLOOR_S and image is not None:
            break
        image, errors = _render_pass_async(settings, left)
        passes += 1
        if image is None:
            return None, passes, False, errors
        digest = _hash_image(image)
        if digest == last_hash:




            if not (warm and _image_is_uniform(image)):
                return image, passes, True, errors
        last_hash = digest
    return image, passes, False, errors


def _render_map(args: dict) -> dict:
    """Worker thread (``background=True``): the settings are built on the main thread, every render waits here, and the JPEG is encoded here."""

    planned = run_on_main_thread(_plan_render, args, timeout=30)
    if "_error" in planned:
        return planned
    settings, layers_rendered, width, height, warm = (planned["settings"], planned["layers_rendered"],
                                                      planned["width"], planned["height"], planned["warm"])
    extent_note = planned.get("extent_note") or ""
    image, passes, stable, render_errors = _render_stable_async(settings, warm=warm)
    if image is None:
        return {"_error": "The map did not finish rendering in time.",
                "suggestion": "Render fewer layers (layer_names), a smaller extent, or a smaller image.",
                "render_errors": render_errors}



    blank = {"blank": True,
             "note": ("Every pixel is the same colour. Over a tile layer this is usually tiles that have "
                      "not arrived yet: render again before concluding a layer is wrong. If the second "
                      "render is blank too, the layers and the numbers you computed still stand: report "
                      "them and say the preview did not draw. Do not stop the work for a blank preview.")
             } if _image_is_uniform(image) else {}
    save_path = args.get("save_path")
    if save_path:
        path_error = validate_path(save_path, write=True)
        if path_error:
            return {"_error": path_error}
        save_dir = os.path.dirname(save_path)
        if save_dir:
            os.makedirs(save_dir, exist_ok=True)
        fmt = "PNG" if os.path.splitext(save_path)[1].lower() == ".png" else "JPEG"
        if not image.save(save_path, fmt):
            return {"_error": f"Could not write image to {save_path}"}

        result = {
            "saved_path": save_path, "width": width, "height": height, "format": fmt.lower(),
            "layers_rendered": layers_rendered, "render_stable": stable, "passes": passes,
        }
        result.update(blank)
        if extent_note:
            result["extent_note"] = extent_note
        if render_errors:
            result["render_errors"] = render_errors
        return result

    raw, sent = _jpeg_under_cap(image)
    result = {
        "image_base64": base64.b64encode(raw).decode("ascii"), "width": sent.width(), "height": sent.height(),
        "format": "jpeg", "image_bytes": len(raw),
        "layers_rendered": layers_rendered, "render_stable": stable, "passes": passes,
    }
    result.update(blank)
    if extent_note:
        result["extent_note"] = extent_note
    if (sent.width(), sent.height()) != (width, height):
        result["downscaled_from"] = {"width": width, "height": height}
    if render_errors:
        result["render_errors"] = render_errors
    return result


def _plan_render(args: dict) -> dict:
    """Main thread: the settings of a render_map call, or the error that stops it."""
    width = min(args.get("width", 800), _MAX_RENDER_WIDTH)
    height = min(args.get("height", 600), _MAX_RENDER_HEIGHT)

    settings = QgsMapSettings()
    settings.setOutputSize(QSize(width, height))
    _apply_quality_flags(settings)








    canvas_crs = None
    try:
        canvas_crs = iface.mapCanvas().mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001 - no canvas outside QGIS
        canvas_crs = None
    crs_id = args.get("crs")
    if crs_id:
        crs = QgsCoordinateReferenceSystem(crs_id)
        if not crs.isValid():
            return {"_error": f"Invalid CRS: {crs_id}"}
        settings.setDestinationCrs(crs)
    elif canvas_crs is not None and canvas_crs.isValid():
        settings.setDestinationCrs(canvas_crs)
    else:
        settings.setDestinationCrs(QgsProject.instance().crs())

    extent = args.get("extent")
    degrees_note = ""
    if extent:
        rectangle = QgsRectangle(
            extent["xmin"], extent["ymin"], extent["xmax"], extent["ymax"]
        )







        destination = settings.destinationCrs()
        if (destination.isValid() and not destination.isGeographic()
                and -180.0 <= rectangle.xMinimum() < rectangle.xMaximum() <= 180.0
                and -90.0 <= rectangle.yMinimum() < rectangle.yMaximum() <= 90.0):
            try:
                to_canvas = QgsCoordinateTransform(
                    QgsCoordinateReferenceSystem("EPSG:4326"), destination, QgsProject.instance())
                rectangle = to_canvas.transformBoundingBox(rectangle)
                degrees_note = (f"The extent was read as degrees (EPSG:4326) and transformed to "
                                f"{destination.authid()}, the CRS this render draws in.")
            except Exception:  # noqa: BLE001 - an extent that cannot be transformed is used as given
                degrees_note = ""
        settings.setExtent(rectangle)
    else:
        settings.setExtent(iface.mapCanvas().extent())

    layer_names = args.get("layer_names")
    if layer_names:
        layers = []
        missing = []
        for name in layer_names:
            layer = _find_layer(name)
            if layer is not None:
                layers.append(layer)
            else:
                missing.append(name)
        if missing:



            error = _layer_not_found_error(missing[0])
            error["_missing_layers"] = missing
            return error
    else:
        layers = list(iface.mapCanvas().layers())
    settings.setLayers(layers)

    _apply_background(settings, args.get("background"))
    warm = bool(args.get("warmup", True)) and any(_is_tiled_raster(layer) for layer in layers)
    return {"settings": settings, "layers_rendered": [layer.name() for layer in layers],
            "width": width, "height": height, "warm": warm, "extent_note": degrees_note}


def _plan_detection_reveal(args: dict) -> dict:
    """Main thread: the memory layer of the reveal, and one settings per frame."""
    import math
    import random as _random

    from qgis.core import (
        QgsCoordinateTransform,
        QgsFeature,
        QgsField,
        QgsFillSymbol,
        QgsProperty,
        QgsSingleSymbolRenderer,
        QgsSymbolLayer,
        QgsWkbTypes,
    )

    layer_name = args["layer_name"]
    out_dir = args["out_dir"]
    steps = min(MAX_FRAME_STEPS, max(1, int(args.get("steps", 24))))
    order = args.get("order", "random")
    width = min(args.get("width", 1920), _MAX_RENDER_WIDTH)
    height = min(args.get("height", 1080), _MAX_RENDER_HEIGHT)
    prefix = _safe_frame_prefix(args.get("prefix"), "reveal_")
    random_colors = args.get("random_colors", True)
    fill_color = args.get("fill_color", "#3FB984")
    background = args.get("background")

    src = _find_layer(layer_name)
    if src is None:
        return _layer_not_found_error(layer_name)
    if not isinstance(src, QgsVectorLayer):
        return {"_error": f"'{layer_name}' is not a vector layer"}

    path_error = validate_path(out_dir, write=True)
    if path_error:
        return {"_error": path_error}
    folder_error = _prepare_frame_folder(out_dir, prefix)
    if folder_error:
        return {"_error": folder_error, "code": "INVALID_ARGS"}

    feats = list(src.getFeatures())
    if not feats:
        return {"_error": f"Layer '{layer_name}' has no features to reveal"}

    if order == "area_desc":
        feats.sort(key=lambda f: f.geometry().area(), reverse=True)
    elif order == "area_asc":
        feats.sort(key=lambda f: f.geometry().area())
    elif order == "top_down":
        feats.sort(key=lambda f: -f.geometry().centroid().asPoint().y())
    elif order == "left_right":
        feats.sort(key=lambda f: f.geometry().centroid().asPoint().x())
    else:
        _random.Random(len(feats)).shuffle(feats)  # nosec B311 - deterministic display order is not security relevant

    dest_crs = QgsProject.instance().crs()

    extent = args.get("extent")
    if extent:
        rect = QgsRectangle(extent["xmin"], extent["ymin"], extent["xmax"], extent["ymax"])
    else:
        rect = QgsRectangle(src.extent())
        if src.crs() != dest_crs:
            xform = QgsCoordinateTransform(src.crs(), dest_crs, QgsProject.instance())
            rect = xform.transformBoundingBox(rect)
        pad_x = rect.width() * 0.05
        pad_y = rect.height() * 0.05
        rect = QgsRectangle(rect.xMinimum() - pad_x, rect.yMinimum() - pad_y,
                            rect.xMaximum() + pad_x, rect.yMaximum() + pad_y)

    if args.get("base_layer_names"):
        base_layers = []
        missing = []
        for name in args["base_layer_names"]:
            layer = _find_layer(name)
            if layer is not None:
                base_layers.append(layer)
            else:
                missing.append(name)
        if missing:
            error = _layer_not_found_error(missing[0])
            error["_missing_layers"] = missing
            return error
    else:
        base_layers = [lyr for lyr in iface.mapCanvas().layers() if isinstance(lyr, QgsRasterLayer)]

    geom = QgsWkbTypes.displayString(QgsWkbTypes.flatType(src.wkbType()))
    mem = QgsVectorLayer(f"{geom}?crs={src.crs().authid()}", "reveal_tmp", "memory")
    mem.dataProvider().addAttributes([QgsField("seq", field_type("Int"))])
    mem.updateFields()

    sym = QgsFillSymbol.createSimple({
        "color": fill_color, "outline_color": "#ffffff", "outline_width": "0.35",
    })
    if random_colors:
        fl = sym.symbolLayer(0)
        fl.setDataDefinedProperty(
            enum_member(QgsSymbolLayer, "Property", "PropertyFillColor"),
            QgsProperty.fromExpression('color_hsla(("seq" * 47) % 360, 70, 55, 190)'),
        )
    mem.setRenderer(QgsSingleSymbolRenderer(sym))

    total = len(feats)

    def frame_settings(index):
        """Frame *index*: the features revealed so far, then the settings."""



        k = max(1, math.ceil((index + 1) / steps * total))
        _empty_memory_provider(mem.dataProvider())
        batch = []
        for seq, feature in enumerate(feats[:k]):
            new_feature = QgsFeature(mem.fields())
            new_feature.setGeometry(feature.geometry())
            new_feature.setAttribute("seq", seq)
            batch.append(new_feature)
        mem.dataProvider().addFeatures(batch)
        mem.updateExtents()
        settings = QgsMapSettings()
        settings.setOutputSize(QSize(width, height))
        _apply_quality_flags(settings)
        _apply_background(settings, background)
        settings.setDestinationCrs(dest_crs)
        settings.setExtent(rect)
        settings.setLayers([mem] + base_layers if background != "transparent" else [mem])
        return settings

    return {"out_dir": out_dir, "prefix": prefix, "steps": steps, "width": width,
            "height": height, "frame_settings": frame_settings, "order": order,
            "feature_count": total, "layer": src.name(),
            "warm": bool(args.get("warmup", True)) and bool(base_layers),
            "layers_rendered": [src.name()] + [lyr.name() for lyr in base_layers]}


def _render_detection_reveal(args: dict) -> dict:
    """Worker thread (``background=True``): the memory layer and each frame's settings are built on the main thread, every render waits here."""






    planned = run_on_main_thread(_plan_detection_reveal, args, timeout=60)
    if "_error" in planned:
        return planned
    out_dir, prefix = planned["out_dir"], planned["prefix"]
    frames: list = []
    pass_counts: list = []
    stable_flags: list = []
    all_errors: list = []
    for index in range(planned["steps"]):
        settings = run_on_main_thread(planned["frame_settings"], index, timeout=60)
        image, passes, stable, errors = _render_stable_async(settings, warm=planned["warm"])
        if image is None:
            return {"_error": f"Frame {index} did not finish rendering in time.",
                    "suggestion": "Fewer steps, fewer base layers, or a smaller image.",
                    "render_errors": errors}
        pass_counts.append(passes)
        stable_flags.append(stable)
        for err in errors:
            if err not in all_errors:
                all_errors.append(err)
        frame_path = os.path.join(out_dir, f"{prefix}{index:03d}.png")
        if not image.save(frame_path, "PNG"):
            return {"_error": f"Could not write frame {frame_path}"}
        frames.append(frame_path)

    result = {
        "frames": frames,
        "count": len(frames),
        "steps": planned["steps"],
        "feature_count": planned["feature_count"],
        "order": planned["order"],
        "width": planned["width"],
        "height": planned["height"],
        "layer": planned["layer"],
        "layers_rendered": planned["layers_rendered"],
        "render_stable": all(stable_flags),
        "passes": max(pass_counts) if pass_counts else 0,
    }
    if all_errors:
        result["render_errors"] = all_errors
    return result


def _plan_camera_move(args: dict) -> dict:
    """Main thread: every frame of a camera move, as a settings object each."""
    from qgis.core import QgsCoordinateTransform

    out_dir = args["out_dir"]
    steps = min(MAX_FRAME_STEPS, max(2, int(args.get("steps", 60))))
    width = min(args.get("width", 1920), _MAX_RENDER_WIDTH)
    height = min(args.get("height", 1080), _MAX_RENDER_HEIGHT)
    prefix = _safe_frame_prefix(args.get("prefix"), "camera_")
    background = args.get("background")
    dest_crs = QgsProject.instance().crs()

    path_error = validate_path(out_dir, write=True)
    if path_error:
        return {"_error": path_error}
    folder_error = _prepare_frame_folder(out_dir, prefix)
    if folder_error:
        return {"_error": folder_error, "code": "INVALID_ARGS"}

    def _rect(key):
        e = args.get(key)
        return QgsRectangle(e["xmin"], e["ymin"], e["xmax"], e["ymax"]) if e else None

    start = _rect("from_extent") or QgsRectangle(iface.mapCanvas().extent())
    end = _rect("to_extent")
    if end is None and args.get("to_layer"):
        lyr = _find_layer(args["to_layer"])
        if lyr is None:
            return _layer_not_found_error(args["to_layer"])
        end = QgsRectangle(lyr.extent())
        if lyr.crs() != dest_crs:
            end = QgsCoordinateTransform(lyr.crs(), dest_crs, QgsProject.instance()).transformBoundingBox(end)
    if end is None:
        zf = float(args.get("to_zoom", 2.0)) or 2.0
        cx, cy = start.center().x(), start.center().y()
        hw, hh = start.width() / (2 * zf), start.height() / (2 * zf)
        end = QgsRectangle(cx - hw, cy - hh, cx + hw, cy + hh)

    layer_names = args.get("layer_names")
    if layer_names:
        layers = []
        missing = []
        for name in layer_names:
            layer = _find_layer(name)
            if layer is not None:
                layers.append(layer)
            else:
                missing.append(name)
        if missing:
            error = _layer_not_found_error(missing[0])
            error["_missing_layers"] = missing
            return error
    else:
        layers = list(iface.mapCanvas().layers())

    def _ease(t):
        return 4 * t * t * t if t < 0.5 else 1 - ((-2 * t + 2) ** 3) / 2

    def _lerp(a, b, t):
        return a + (b - a) * t



    frame_rects = []
    for i in range(steps):
        t = _ease(i / (steps - 1))
        frame_rects.append(QgsRectangle(
            _lerp(start.xMinimum(), end.xMinimum(), t), _lerp(start.yMinimum(), end.yMinimum(), t),
            _lerp(start.xMaximum(), end.xMaximum(), t), _lerp(start.yMaximum(), end.yMaximum(), t),
        ))

    def frame_settings(index):
        """One frame, built here because QgsMapSettings holds live layers."""
        settings = QgsMapSettings()
        settings.setOutputSize(QSize(width, height))
        _apply_quality_flags(settings)
        _apply_background(settings, background)
        settings.setDestinationCrs(dest_crs)
        settings.setExtent(frame_rects[index])
        settings.setLayers(layers)
        return settings

    return {"out_dir": out_dir, "prefix": prefix, "steps": len(frame_rects),
            "width": width, "height": height, "frame_settings": frame_settings,
            "warm": bool(args.get("warmup", True)),
            "layers_rendered": [layer.name() for layer in layers]}


def _render_camera_move(args: dict) -> dict:
    """Worker thread (``background=True``): the plan and each frame's settings are built on the main thread, every render waits here."""









    planned = run_on_main_thread(_plan_camera_move, args, timeout=60)
    if "_error" in planned:
        return planned
    out_dir, prefix = planned["out_dir"], planned["prefix"]
    steps, warm = planned["steps"], planned["warm"]
    frames: list = []
    pass_counts: list = []
    stable_flags: list = []
    all_errors: list = []
    for index in range(steps):
        settings = run_on_main_thread(planned["frame_settings"], index, timeout=30)
        image, passes, stable, errors = _render_stable_async(settings, warm=warm)
        if image is None:
            return {"_error": f"Frame {index} did not finish rendering in time.",
                    "suggestion": "Fewer steps, fewer layers, or a smaller image.",
                    "render_errors": errors}
        pass_counts.append(passes)
        stable_flags.append(stable)
        for err in errors:
            if err not in all_errors:
                all_errors.append(err)
        frame_path = os.path.join(out_dir, f"{prefix}{index:03d}.png")
        if not image.save(frame_path, "PNG"):
            return {"_error": f"Could not write frame {frame_path}"}
        frames.append(frame_path)

    result = {
        "frames": frames,
        "count": len(frames),
        "steps": steps,
        "width": planned["width"],
        "height": planned["height"],
        "layers_rendered": planned["layers_rendered"],
        "render_stable": all(stable_flags),
        "passes": max(pass_counts) if pass_counts else 0,
    }
    if all_errors:
        result["render_errors"] = all_errors
    return result


def _list_layouts(args: dict) -> dict:
    manager = QgsProject.instance().layoutManager()
    layouts = []
    for layout in manager.layouts():
        layouts.append({
            "name": layout.name(),
            "page_count": layout.pageCollection().pageCount(),
        })
    return {"layouts": layouts, "count": len(layouts)}



_EXPORT_EXTS = {".pdf": "pdf", ".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".svg": "svg"}


def _export_format(args: dict, output_path: str) -> tuple[str, str]:
    """The format to export in, and why not when the call contradicts itself."""








    from_name = _EXPORT_EXTS.get(os.path.splitext(str(output_path))[1].lower(), "")
    asked = str(args.get("format") or "").strip().lower()
    if not asked:
        return from_name or "pdf", ""
    if asked == "jpeg":
        asked = "jpg"
    if from_name and from_name != asked:
        return "", (f"format is {asked} but {os.path.basename(str(output_path))} names a "
                    f"{from_name} file, so the file would not hold what its name promises.")
    return asked, ""


def _export_layout(args: dict) -> dict:
    layout_name = args["layout_name"]
    output_path = args["output_path"]
    fmt, fmt_error = _export_format(args, output_path)
    if fmt_error:
        return {"_error": fmt_error, "_code": "INVALID_ARGS",
                "_suggestion": "Pass format that matches the file name, or drop format and let the "
                               "extension decide."}


    dpi = max(10, min(int(args.get("dpi", 300) or 300), limits.MAX_RENDER_DPI))





    overwrite = args.get("overwrite", False)

    path_error = validate_path(output_path, write=True)
    if path_error:
        return {"_error": path_error}

    if not overwrite and os.path.exists(output_path):
        return {"_error": f"File already exists: {output_path}. Use overwrite:true to replace it."}

    manager = QgsProject.instance().layoutManager()
    layout = manager.layoutByName(layout_name)
    if not layout:
        return {"_error": f"Layout not found: {layout_name}"}

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    exporter = QgsLayoutExporter(layout)

    if fmt == "pdf":
        settings = QgsLayoutExporter.PdfExportSettings()
        settings.dpi = dpi

        if args.get("georeference"):
            try:
                settings.appendGeoreference = True
            except AttributeError:
                pass
        if args.get("force_vector"):
            try:
                settings.forceVectorOutput = True
            except AttributeError:
                pass
        result = exporter.exportToPdf(output_path, settings)
    elif fmt in ("png", "jpg"):
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = dpi
        result = exporter.exportToImage(output_path, settings)
    elif fmt == "svg":
        settings = QgsLayoutExporter.SvgExportSettings()
        settings.dpi = dpi
        result = exporter.exportToSvg(output_path, settings)
    else:
        return {"_error": f"Unsupported format: {fmt}"}

    if result != enum_member(QgsLayoutExporter, "ExportResult", "Success"):
        return {"_error": f"Export failed with code: {result}"}



    actual_path = output_path
    if not os.path.exists(actual_path):
        try:
            generated = exporter.generateFileName(output_path) if hasattr(exporter, "generateFileName") else None
        except Exception:
            generated = None
        if generated and os.path.exists(generated):
            actual_path = generated
        else:
            return {"_error": f"Export reported success but no output file was found at {output_path}"}

    return {
        "exported": actual_path,
        "format": fmt,
        "dpi": dpi,
        "file_size": os.path.getsize(actual_path),
    }


_LOG_BUFFER_SIZE = 5000
_MSG_BUF = "_aiagent_msglog_buf"
_MSG_BUF_CACHE = None
_MSG_CONNECTED = "_aiagent_msglog_connected"


def _message_buffer():
    """The captured-message ring, parked on the QgsApplication singleton."""





    global _MSG_BUF_CACHE
    if _MSG_BUF_CACHE is not None:
        return _MSG_BUF_CACHE
    from collections import deque

    app = QgsApplication.instance()
    if app is None:
        return deque(maxlen=_LOG_BUFFER_SIZE)
    buf = app.property(_MSG_BUF)
    if buf is None:
        buf = deque(maxlen=_LOG_BUFFER_SIZE)
        app.setProperty(_MSG_BUF, buf)


    _MSG_BUF_CACHE = buf
    return buf


def _connect_message_log():
    """Connect to QgsApplication.messageLog() to capture messages."""






    app = QgsApplication.instance()
    if app is not None and app.property(_MSG_CONNECTED):
        return
    try:
        msg_log = QgsApplication.messageLog()
        if hasattr(msg_log, "messageReceived"):
            msg_log.messageReceived.connect(_on_message_received)
            if app is not None:
                app.setProperty(_MSG_CONNECTED, True)
    except Exception:  # nosec B110 - render enhancement is optional
        pass


def _on_message_received(message, tag, level):
    """Capture log messages into our buffer with timestamp."""
    import time
    _LEVEL_MAP = {0: "info", 1: "warning", 2: "critical", 3: "success"}




    try:
        ordinal = int(level)
    except (TypeError, ValueError):
        ordinal = level
    _message_buffer().append({
        "tag": tag,
        "message": message,
        "level": _LEVEL_MAP.get(ordinal, str(level)),
        "timestamp": time.strftime("%H:%M:%S"),
    })


_LOG_RESULT_BUDGET = 4_000


def _clip_message(text: str, max_chars: int) -> str:
    """Cut a long log line, saying how much was dropped so nothing looks complete."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}… [+{len(text) - max_chars} chars]"


def _get_message_log(args: dict) -> dict:
    _connect_message_log()
    limit = args.get("limit", 25)
    tag = args.get("tag")
    min_level = args.get("level")
    search = (args.get("search") or "").lower()
    try:
        max_message_chars = int(args.get("max_message_chars", 500))
    except (TypeError, ValueError):
        max_message_chars = 500

    _LEVEL_SEVERITY = {"info": 0, "success": 0, "warning": 1, "critical": 2}

    captured = list(_message_buffer())
    messages = list(captured)
    messages.reverse()

    if tag:
        messages = [m for m in messages if m["tag"] == tag]
    if min_level:
        threshold = _LEVEL_SEVERITY.get(min_level, 0)
        messages = [m for m in messages if _LEVEL_SEVERITY.get(m["level"], 0) >= threshold]
    if search:
        messages = [m for m in messages if search in m["message"].lower() or search in m["tag"].lower()]

    matched = len(messages)
    messages = [
        dict(m, message=_clip_message(m["message"], max_message_chars))
        for m in messages[:limit]
    ]


    messages, _dropped = size_budget(messages, _LOG_RESULT_BUDGET)

    out = {
        "messages": messages,
        "count": len(messages),
        "buffer_size": len(captured),
        "buffer_capacity": _LOG_BUFFER_SIZE,
    }
    if matched > len(messages):
        out["messages_omitted"] = matched - len(messages)
    if not tag:

        tags = sorted({m["tag"] for m in captured})
        out["available_tags"] = tags[:40]
        if len(tags) > 40:
            out["tags_omitted"] = len(tags) - 40
    return out




_ENV_HIGHLIGHTS = (
    "PROJ_LIB", "PROJ_DATA", "GDAL_DATA", "GDAL_DRIVER_PATH", "GDAL_PAM_ENABLED",
    "QT_SCALE_FACTOR", "QT_AUTO_SCREEN_SCALE_FACTOR", "QT_QPA_PLATFORM",
    "PYTHONPATH", "PYTHONHOME", "QGIS_PREFIX_PATH", "QGIS_PLUGINPATH", "PATH",
)


def _python_executable() -> str:
    """The interpreter a user could actually type, not the program we run in."""








    import os
    import sys

    current = sys.executable or ""
    if os.name != "nt" or os.path.basename(current).lower().startswith("python"):
        return current
    sibling = os.path.join(os.path.dirname(current), "python.exe")
    return sibling if os.path.exists(sibling) else current


def _get_debug_info(args: dict) -> dict:
    import platform
    import sys

    from qgis.core import Qgis, QgsProviderRegistry

    result = {
        "qgis_version": Qgis.version(),
        "python_version": platform.python_version(),
        "python_executable": _python_executable(),
        "host_executable": sys.executable,
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "profile_path": QgsApplication.qgisSettingsDirPath(),
        "prefix_path": QgsApplication.prefixPath(),
        "locale": QgsApplication.locale(),
    }






    try:
        from ..core import machine, net

        result["machine"] = machine.report()
        result["connection"] = net.link_report()
    except Exception as err:
        result["machine_error"] = f"{err.__class__.__name__}: {err}"

    try:
        result["data_providers"] = sorted(QgsProviderRegistry.instance().providerList())
    except Exception as err:
        result["data_providers_error"] = f"{err.__class__.__name__}: {err}"

    try:
        proc_reg = QgsApplication.processingRegistry()
        result["processing_providers"] = [
            {"id": p.id(), "name": p.name(), "algorithms": len(p.algorithms()), "active": p.isActive()}
            for p in proc_reg.providers()
        ]
    except Exception as err:
        result["processing_providers_error"] = f"{err.__class__.__name__}: {err}"

    try:
        import qgis.utils
        result["active_plugins"] = {
            name: qgis.utils.pluginMetadata(name, "version") or "?"
            for name in sorted(qgis.utils.plugins)
        }
    except Exception as err:
        result["active_plugins_error"] = f"{err.__class__.__name__}: {err}"

    try:
        srs_db = QgsApplication.srsDatabaseFilePath()
        crs_db = {"path": srs_db, "exists": os.path.exists(srs_db)}
        if crs_db["exists"]:
            crs_db["size_bytes"] = os.path.getsize(srs_db)
        aux = QgsApplication.qgisUserDatabaseFilePath()
        crs_db["user_database"] = {"path": aux, "exists": os.path.exists(aux)}
        result["crs_database"] = crs_db
    except Exception as err:
        result["crs_database_error"] = f"{err.__class__.__name__}: {err}"

    try:
        svg = list(QgsApplication.svgPaths())
        result["svg_paths"] = [{"path": p, "exists": os.path.isdir(p)} for p in svg]
    except Exception as err:
        result["svg_paths_error"] = f"{err.__class__.__name__}: {err}"

    try:
        env = dict(os.environ)

        result["environment_highlights"] = {k: env[k] for k in _ENV_HIGHLIGHTS if k in env}
    except Exception as err:
        result["environment_error"] = f"{err.__class__.__name__}: {err}"

    from ..core.host_platform import peak_memory_mb

    peak = peak_memory_mb()
    if peak is None:
        result["memory_peak_mb_error"] = "peak memory unavailable on this platform"
    else:
        result["memory_peak_mb"] = peak

    _connect_message_log()



    seen = set()
    recent_errors = []
    for m in reversed(list(_message_buffer())):
        if m["level"] not in ("warning", "critical"):
            continue
        key = hashlib.sha256(f"{m['tag']}|{m['message']}".encode("utf-8", "replace")).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        recent_errors.append(dict(m, message=_clip_message(m["message"], 500)))
        if len(recent_errors) >= 10:
            break
    if recent_errors:
        result["recent_errors"] = recent_errors

    return result

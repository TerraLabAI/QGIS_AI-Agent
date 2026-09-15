# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""render_map, render_detection_reveal and render_camera_move: the offscreen renders and their frame sequences."""
from __future__ import annotations

import base64
import hashlib
import os
import re
import time

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QSize
from qgis.utils import iface

from ..core import limits
from ..core.background import run_on_main_thread
from ..core.qt_compat import enum_member, field_type
from ..core.security import validate_path
from .core_tools import _find_layer, _layer_not_found_error


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


def _apply_quality_flags(settings: QgsMapSettings) -> None:
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
    import weakref

    from qgis.PyQt.QtCore import QTimer

    done = threading.Event()
    holder = {}

    def start():




        job = QgsMapRendererParallelJob(settings)
        key = id(job)
        _JOBS_ALIVE[key] = job
        holder["key"] = key
        ref = weakref.ref(job)

        def finished():
            try:
                ended = ref()
                if ended is not None:
                    holder["image"] = ended.renderedImage()
                    holder["errors"] = [{"layer_id": e.layerID, "message": e.message} for e in ended.errors()]
            finally:
                done.set()
                QTimer.singleShot(0, lambda: _JOBS_ALIVE.pop(key, None))

        job.finished.connect(finished)
        job.start()

    run_on_main_thread(start, timeout=30)
    budget = max(_RENDER_PASS_FLOOR_S, min(float(budget), _RENDER_PASS_SECONDS))
    if not done.wait(budget):
        def cancel():
            try:
                job = _JOBS_ALIVE.get(holder.get("key"))
                if job is not None:
                    job.cancelWithoutBlocking()
            except Exception:  # nosec B110 - a job past its budget is dropped either way
                pass
        run_on_main_thread(cancel, timeout=10)
        return None, [{"layer_id": "", "message": f"render not finished after {budget:.0f} s"}]
    if "image" not in holder:
        return None, [{"layer_id": "", "message": "the render job ended without an image"}]
    return holder["image"], holder.get("errors", [])



_JOBS_ALIVE: dict = {}


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
        got, got_errors = _render_pass_async(settings, left)
        passes += 1
        if got is None:


            if image is None:
                return None, passes, False, got_errors
            return image, passes, False, errors
        image, errors = got, got_errors
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
    width = min(args.get("width", 800), limits.current("MAX_RENDER_WIDTH_PX"))
    height = min(args.get("height", 600), limits.current("MAX_RENDER_HEIGHT_PX"))

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
    width = min(args.get("width", 1920), limits.current("MAX_RENDER_WIDTH_PX"))
    height = min(args.get("height", 1080), limits.current("MAX_RENDER_HEIGHT_PX"))
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
    width = min(args.get("width", 1920), limits.current("MAX_RENDER_WIDTH_PX"))
    height = min(args.get("height", 1080), limits.current("MAX_RENDER_HEIGHT_PX"))
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


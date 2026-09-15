# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





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
from qgis.PyQt.QtCore import QSize, Qt
from qgis.utils import iface

from ..core import limits
from ..core.background import run_on_main_thread
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from .layer_lookup import _find_layer, _layer_not_found_error


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

    from qgis.PyQt.QtCore import Qt
    raw = _encode_jpeg(image, quality)
    smooth = enum_member(Qt, "TransformationMode", "SmoothTransformation")
    while len(raw) > _MAX_IMAGE_BYTES and image.width() > 320:


        scale = max(0.5, (_MAX_IMAGE_BYTES / len(raw)) ** 0.5 * 0.9)
        image = image.scaledToWidth(int(image.width() * scale), smooth)
        raw = _encode_jpeg(image, quality)
    return raw, image


def _apply_quality_flags(settings: QgsMapSettings) -> None:



    for flag_name in ("Antialiasing", "UseAdvancedEffects", "HighQualityImageTransforms", "DrawLabels"):
        flag = getattr(QgsMapSettings, flag_name, None)
        if flag is not None:
            try:
                settings.setFlag(flag, True)
            except Exception:  # nosec B110
                pass


def _is_tiled_raster(layer) -> bool:






    try:
        if isinstance(layer, QgsRasterLayer):
            return layer.providerType() in ("wms", "wmts", "xyz")
        return isinstance(layer, _vector_tile_class())
    except Exception:  # nosec B110
        return False


def _vector_tile_class():






    global _VECTOR_TILE_CLASS
    if _VECTOR_TILE_CLASS is None:
        try:
            from qgis.core import QgsVectorTileLayer

            _VECTOR_TILE_CLASS = QgsVectorTileLayer
        except ImportError:
            _VECTOR_TILE_CLASS = _NoLayer
    return _VECTOR_TILE_CLASS


class _NoLayer:
    pass


_VECTOR_TILE_CLASS = None


def _apply_background(settings, background) -> None:


    if background == "transparent":
        try:
            from qgis.PyQt.QtGui import QColor
            settings.setBackgroundColor(QColor(0, 0, 0, 0))
        except Exception:  # nosec B110
            pass


_UNSAFE_IN_A_FRAME_NAME = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_frame_prefix(value, default: str) -> str:








    text = _UNSAFE_IN_A_FRAME_NAME.sub("_", str(value if value is not None else default))
    while ".." in text:
        text = text.replace("..", ".")
    return text.strip(" .") or default


def _prepare_frame_folder(path: str, prefix: str = ""):








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




_RENDER_PASS_SECONDS = 120.0








_RENDER_TOTAL_SECONDS = 130.0


_FALLBACK_SECONDS = 30.0
_FALLBACK_MAX_SIDE = 640

_RENDER_PASS_FLOOR_S = 8.0


def _render_pass_async(settings, budget: float = _RENDER_PASS_SECONDS):











    import threading
    import weakref

    from qgis.PyQt.QtCore import QTimer

    done = threading.Event()
    holder = {}
    began = time.monotonic()

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
                    holder["labels"] = ended.takeLabelingResults()
            finally:
                done.set()
                QTimer.singleShot(0, lambda: _JOBS_ALIVE.pop(key, None))

        def layers_drawn():

            holder.setdefault("layers_s", time.monotonic() - began)

        job.finished.connect(finished)
        job.renderingLayersFinished.connect(layers_drawn)
        job.start()

    run_on_main_thread(start, timeout=30)
    budget = max(_RENDER_PASS_FLOOR_S, min(float(budget), _RENDER_PASS_SECONDS))
    stopped = _wait_or_stop(done, budget)
    if stopped or not done.is_set():
        def cancel():
            try:
                job = _JOBS_ALIVE.get(holder.get("key"))
                if job is not None:
                    job.cancelWithoutBlocking()
            except Exception:  # nosec B110
                pass
        run_on_main_thread(cancel, timeout=10)
        if stopped:
            return None, [{"layer_id": "", "message": _STOPPED}], None


        layers_s = holder.get("layers_s")
        where = (f"the layers were drawn after {layers_s:.0f} s and the labels were still being placed"
                 if layers_s is not None else "the layers were still drawing")
        return None, [{"layer_id": "", "message": f"render not finished after {budget:.0f} s: {where}"}], None
    if "image" not in holder:
        return None, [{"layer_id": "", "message": "the render job ended without an image"}], None
    return holder["image"], holder.get("errors", []), holder.get("labels")



_JOBS_ALIVE: dict = {}
_STOPPED = "render stopped by the user"

_STOP_POLL_S = 0.25


def _stop_requested() -> bool:

    from ..core.net_state import current_cancel_check

    check = current_cancel_check()
    try:
        return bool(check()) if check is not None else False
    except Exception:  # noqa: BLE001
        return False


def _wait_or_stop(done, budget: float) -> bool:






    deadline = time.monotonic() + budget
    while not done.is_set():
        if _stop_requested():
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        done.wait(min(_STOP_POLL_S, left))
    return False


def _image_is_uniform(image) -> bool:








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
    except Exception:  # noqa: BLE001
        return False
    return True


def _dominant_share(image) -> float:









    try:
        width, height = image.width(), image.height()
        if width < 2 or height < 2:
            return 1.0
        counts: dict = {}
        steps = 24
        for row in range(steps):
            y = row * (height - 1) // (steps - 1)
            for column in range(steps):
                x = column * (width - 1) // (steps - 1)
                pixel = image.pixel(x, y)
                counts[pixel] = counts.get(pixel, 0) + 1
        return max(counts.values()) / float(steps * steps)
    except Exception:  # noqa: BLE001
        return 0.0


def _drawn_share(image) -> float:







    try:
        width, height = image.width(), image.height()
        if width < 2 or height < 2:
            return 0.0
        scale = min(1.0, 256.0 / max(width, height))
        small = image.scaled(max(1, int(width * scale)), max(1, int(height * scale)),
                             enum_member(Qt, "AspectRatioMode", "IgnoreAspectRatio"),
                             enum_member(Qt, "TransformationMode", "SmoothTransformation"))
        counts: dict = {}
        for y in range(small.height()):
            for x in range(small.width()):
                pixel = small.pixel(x, y)
                counts[pixel] = counts.get(pixel, 0) + 1
        total = float(small.width() * small.height())
        return (total - max(counts.values())) / total if total else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _out_of_scale(layers, settings) -> list:




    try:
        scale = float(settings.scale())
    except Exception:  # noqa: BLE001
        return []
    names = []
    for layer in layers:
        try:
            if layer.hasScaleBasedVisibility() and not layer.isInScaleRange(scale):
                names.append(layer.name())
        except Exception:  # nosec B112
            continue
    return names





_REMOTE_PROVIDERS = frozenset({"wms", "wmts", "xyz", "wfs", "arcgismapserver",
                               "arcgisfeatureserver", "afs", "ams", "vectortile", "oapif"})


def _in_project_order(layers) -> tuple:








    try:
        order = [layer.id() for layer in QgsProject.instance().layerTreeRoot().layerOrder()]
    except (AttributeError, RuntimeError):
        return layers, ""
    rank = {layer_id: index for index, layer_id in enumerate(order)}
    stacked = sorted(layers, key=lambda layer: rank.get(layer.id(), len(order)))
    if [layer.id() for layer in stacked] == [layer.id() for layer in layers]:
        return layers, ""
    lifted = []
    for index, layer in enumerate(layers):
        under = [other.name() for other in layers[index + 1:]
                 if rank.get(other.id(), len(order)) < rank.get(layer.id(), len(order))]
        if under:
            lifted.append(f"'{layer.name()}' is under {', '.join(repr(name) for name in under[:3])}")
    return stacked, (
        "Drawn in the project's layer order, which a layout map prints, not in the order given: "
        + "; ".join(lifted[:4])
        + ". To print it on top, set_layer_order layer_name that layer, position top, then render again.")


def _slow_layer_names(layers) -> list:





    remote, pixels = [], []
    for layer in layers or ():
        try:
            try:
                provider = str(layer.providerType() or "").lower()
                source = str(layer.source() or "").lower()
            except (AttributeError, RuntimeError):
                provider, source = "", ""
            if provider in _REMOTE_PROVIDERS or "://" in source.split("|")[0] or "/vsicurl" in source:
                remote.append(layer.name())
            elif isinstance(layer, (QgsRasterLayer, _vector_tile_class())):
                pixels.append(layer.name())
        except (AttributeError, RuntimeError):
            continue
    return remote or pixels


def _extent_in(layer, destination) -> QgsRectangle | None:





    try:
        if _is_tiled_raster(layer):
            return None
        rect = QgsRectangle(layer.extent())
        if rect.isNull() or rect.isEmpty():
            return None
        source = layer.crs()
        if source.isValid() and destination.isValid() and source != destination:
            rect = QgsCoordinateTransform(source, destination, QgsProject.instance()).transformBoundingBox(rect)
        return rect if not (rect.isNull() or rect.isEmpty()) else None
    except Exception:  # noqa: BLE001
        return None


def _framing(layers, destination, extent) -> tuple:





    data: QgsRectangle | None = None
    outside, inside = [], []
    for layer in layers or ():
        rect = _extent_in(layer, destination)
        if rect is None:
            continue
        data = QgsRectangle(rect) if data is None else data
        data.combineExtentWith(rect)
        try:
            (inside if rect.intersects(extent) else outside).append(layer.name())
        except (AttributeError, RuntimeError):
            continue
    return data, outside, inside


def _left_out(chosen, destination, extent) -> list:










    from ..core import layer_order

    try:
        canvas_layers = list(iface.mapCanvas().layers())
        order = [layer.id() for layer in QgsProject.instance().layerTreeRoot().layerOrder()]
    except (AttributeError, RuntimeError):
        return []
    rank = {layer_id: index for index, layer_id in enumerate(order)}
    chosen_ids = {layer.id() for layer in chosen}
    view_area = float(extent.width()) * float(extent.height())
    out = []
    for layer in canvas_layers:
        try:
            if layer.id() in chosen_ids:
                continue
            entry = {"name": layer.name()}
            if layer_order.is_backdrop(layer):
                entry["covers"] = "all"
            else:
                rect = layer_order.drawn_extent(layer, destination)
                if rect is None or view_area <= 0:
                    entry["covers"] = "unknown"
                else:
                    share = rect.intersect(extent)
                    part = 0.0 if share.isEmpty() else float(share.width()) * float(share.height())
                    entry["covers"] = f"{min(100.0, 100.0 * part / view_area):.0f}%"
                    if part <= 0:
                        continue
            here = rank.get(layer.id(), len(order))
            over = [other.name() for other in chosen if rank.get(other.id(), len(order)) > here]
            if over:
                entry["draws_over"] = over[:5]
            out.append(entry)
        except (AttributeError, RuntimeError):
            continue
    out.sort(key=lambda entry: 0 if entry.get("draws_over") else 1)
    return out[:8]


def _bbox_of(rect) -> dict:
    return {"xmin": rect.xMinimum(), "ymin": rect.yMinimum(),
            "xmax": rect.xMaximum(), "ymax": rect.yMaximum()}


def _drawn_extent(settings) -> dict:








    try:
        rect = settings.visibleExtent()
        crs = settings.destinationCrs()
        digits = 6 if crs.isValid() and crs.isGeographic() else 2
        out = {key: round(value, digits) for key, value in _bbox_of(rect).items()}
        out["crs"] = crs.authid() if crs.isValid() else ""
        return out
    except Exception:  # noqa: BLE001
        return {}


def _layer_stamp(layer) -> str:






    parts = []
    try:
        parts.append(str(layer.id()))
        parts.append(f"{float(layer.opacity()):.4f}")


        blend = layer.blendMode()
        parts.append(str(int(getattr(blend, "value", blend))))
        parts.append(layer.extent().toString(9))
    except Exception:  # noqa: BLE001
        return ""
    try:
        from qgis.core import QgsMapLayerStyle

        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        xml = style.xmlData()
        if not xml:
            return ""
        parts.append(hashlib.sha256(xml.encode("utf-8", "replace")).hexdigest())
    except Exception:  # noqa: BLE001
        return ""
    if isinstance(layer, QgsVectorLayer):
        try:
            if layer.isEditable():
                return ""
            if str(layer.providerType() or "").lower() in _REMOTE_PROVIDERS:



                return ""
            parts.append(str(layer.featureCount()))
            parts.append(str(layer.subsetString() or ""))
            parts.append(str(layer.undoStack().index()))
        except Exception:  # noqa: BLE001
            return ""
    return "|".join(parts)







_RENDER_CACHE: dict = {}
_RENDER_CACHE_MAX = 2


_RENDER_CACHE_TTL_S = 240.0

_RENDER_CACHE_MAX_LAYERS = 24


def _render_fingerprint(settings, layers, args) -> str:

    if not layers or len(layers) > _RENDER_CACHE_MAX_LAYERS:
        return ""
    stamps = [_layer_stamp(layer) for layer in layers]
    if any(not stamp for stamp in stamps):
        return ""
    try:
        size = settings.outputSize()
        parts = [
            f"{size.width()}x{size.height()}",
            settings.destinationCrs().authid() or settings.destinationCrs().toWkt(),
            settings.extent().toString(9),
            str(args.get("background") or ""),


            str(QgsProject.instance().fileName() or ""),
            str(QgsProject.instance().count()),
        ]
    except Exception:  # noqa: BLE001
        return ""
    parts.extend(stamps)
    return hashlib.sha256("|".join(parts).encode("utf-8", "replace")).hexdigest()


def _current_run_token():
    try:
        from ..core import layer_order

        return layer_order.current_run()
    except Exception:  # noqa: BLE001
        return None


def _cached_render(fingerprint: str):

    if not fingerprint:
        return None
    entry = _RENDER_CACHE.get(fingerprint)
    if entry is None:
        return None
    if time.monotonic() - entry["at"] > _RENDER_CACHE_TTL_S or entry["run"] != _current_run_token():
        _RENDER_CACHE.pop(fingerprint, None)
        return None
    return entry


def _keep_render(fingerprint: str, image, plan: dict, passes: int) -> None:
    if not fingerprint or image is None:
        return
    while len(_RENDER_CACHE) >= _RENDER_CACHE_MAX:
        _RENDER_CACHE.pop(next(iter(_RENDER_CACHE)), None)
    _RENDER_CACHE[fingerprint] = {
        "at": time.monotonic(), "run": _current_run_token(), "image": image, "passes": passes,
        "layers_rendered": plan["layers_rendered"], "width": plan["width"], "height": plan["height"],
        "labels": plan.get("labels"),
    }


def _shrink_for_fallback(settings, width, height) -> tuple:

    longest = max(1, max(int(width), int(height)))
    scale = min(1.0, _FALLBACK_MAX_SIDE / float(longest))
    small_w = max(64, int(round(width * scale)))
    small_h = max(64, int(round(height * scale)))
    settings.setOutputSize(QSize(small_w, small_h))
    return small_w, small_h


def _render_once_async(settings, tiles: bool):



















    deadline = time.monotonic() + _RENDER_TOTAL_SECONDS
    image, errors, labels = _render_pass_async(settings, _RENDER_TOTAL_SECONDS)
    if image is None:
        return None, 1, False, errors, None
    if not (errors or (tiles and _image_is_uniform(image))):
        return image, 1, True, errors, labels
    left = deadline - time.monotonic()
    if left < _RENDER_PASS_FLOOR_S:
        return image, 1, False, errors, labels
    again, again_errors, again_labels = _render_pass_async(settings, left)
    if again is None:
        if any(e.get("message") == _STOPPED for e in again_errors):
            return None, 2, False, again_errors, None
        return image, 2, False, errors, labels
    return again, 2, not (tiles and _image_is_uniform(again)), again_errors, again_labels


def _label_facts(results, settings, layer_ids: list) -> list:




    from .harvest_canvas import _labels_in_view, _labels_placed

    facts = []
    project = QgsProject.instance()
    for layer_id in layer_ids:
        layer = project.mapLayer(layer_id)
        placed = _labels_placed(layer_id, results) if layer is not None else None
        if placed is None:
            continue
        entry = {"layer": layer.name(), "drawn": placed[0]}
        entry.update(_labels_in_view(layer, None, placed[1],
                                     view=(settings.visibleExtent(), settings.destinationCrs())))
        if placed[2]:
            entry["overlapping"] = placed[2]
        facts.append(entry)
    return facts


def _blank_note(image, planned: dict) -> dict:







    uniform = _image_is_uniform(image)
    if not uniform and _dominant_share(image) < 0.995:
        return {}
    outside, inside = planned.get("off_extent") or [], planned.get("in_extent") or []
    data = planned.get("data_extent")
    hidden = planned.get("out_of_scale") or []
    drawn = _drawn_share(image) if inside else 0.0
    if drawn > 0:
        return {"sparse": True, "drawn_pct": round(100.0 * drawn, 2),
                "note": (f"Little of the picture is drawn ({100.0 * drawn:.2g}% of it): the layers inside the "
                         f"extent ({', '.join(inside[:8])}) are thin or small at this scale, not missing.")}
    note = {"blank": True}
    if hidden:
        note["out_of_scale"] = hidden[:8]
        note["note"] = (f"Nothing was drawn of {', '.join(hidden[:8])}: each is set to draw only at other "
                        "scales than this render's (its scale range, often a service's own), so it is not "
                        "broken. get_layer_info shows the range; a smaller extent may fit.")
        return note
    if outside and not inside and data:
        note["note"] = (
            "Nothing was drawn: none of these layers has data inside the extent that was rendered. "
            f"Outside it: {', '.join(outside[:8])}. data_extent as the render extent, or "
            "zoom_to_layer, would draw it; this is a framing mistake, not a broken layer.")
        note["data_extent"] = data
        note["off_extent"] = outside
        return note
    if not planned.get("tiles", True):


        painted = ", ".join(inside[:8]) or "any of the layers"
        note["note"] = (f"Every pixel is the same colour: nothing was painted of {painted}"
                        + (", whose data lies inside this extent" if inside else "")
                        + ". Any error QGIS raised for a layer is in render_errors.")
        return note
    note["note"] = ("Every pixel is the same colour. Over a tile layer this is usually tiles that have "
                    "not arrived yet, so a second render often differs. When a second render is blank "
                    "too, the layers and the numbers already computed still stand; the preview alone "
                    "did not draw, and the work continues.")
    if data:
        note["data_extent"] = data
        note["note"] += (" The layers' own data sits inside data_extent; that extent covers them when "
                         "the view does not.")
    return note


def _deliver_image(args: dict, image, common: dict) -> dict:





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

        return {"saved_path": save_path, "width": image.width(), "height": image.height(),
                "format": fmt.lower(), **common}
    raw, sent = _jpeg_under_cap(image)
    result = {"image_base64": base64.b64encode(raw).decode("ascii"),
              "width": sent.width(), "height": sent.height(),
              "format": "jpeg", "image_bytes": len(raw), **common}
    if (sent.width(), sent.height()) != (image.width(), image.height()):
        result["downscaled_from"] = {"width": image.width(), "height": image.height()}
    return result


def _render_answer(args: dict, image, planned: dict, passes: int, stable: bool, render_errors: list) -> dict:

    common = {"layers_rendered": planned["layers_rendered"], "render_stable": stable, "passes": passes}
    if planned.get("extent"):
        common["extent"] = planned["extent"]
    result = _deliver_image(args, image, common)
    if "_error" in result:
        return result
    result.update(_blank_note(image, planned))
    keys = ["extent_note", "size_note", "order_note", "layout_note", "left_out", "canvas_unchanged", "labels"]
    if planned.get("off_extent"):


        keys += ["off_extent", "data_extent"]
    for key in keys:
        value = planned.get(key)
        if value and key not in result:
            result[key] = value
    if render_errors:
        result["render_errors"] = render_errors
    return result


_TARGETS = frozenset({"canvas", "layout", "file"})


def _layout_page(args: dict) -> dict:







    from . import layout_ready, render_look

    started = run_on_main_thread(render_look.layout_page_start, args, timeout=30)
    if "_error" in started:
        return started
    ready = started["ready"]
    try:
        layout_ready.wait(ready, _RENDER_TOTAL_SECONDS)
        if ready["stopped"]:
            return {"_error": "The render was stopped.", "code": "CANCELLED"}
        if ready["unfinished"]:
            late = ready["unfinished"]
            return {"_error": (f"The maps of page {started['page']} of '{started['layout_name']}' did not finish "
                               f"drawing in {_RENDER_TOTAL_SECONDS:.0f} s."),
                    "slow_layers": late,
                    "suggestion": (f"Still drawing: {', '.join(late[:6])}. set_layers_visibility hides a heavy "
                                   "layer; a smaller page or map frame draws less.")}
        return run_on_main_thread(render_look.layout_page_draw, started, timeout=60)
    finally:
        layout_ready.discard(ready)


def _render_target(args: dict) -> dict:






    from . import render_look

    target = str(args.get("target") or "canvas").strip().lower()
    box = render_look.crop_box(args)
    if isinstance(box, dict):
        return box
    if target == "layout":
        drawn = _layout_page(args)
    else:
        drawn = render_look.file_image(args)
    if "_error" in drawn:
        return drawn
    facts = dict(drawn.get("facts") or {})
    facts["target"] = target


    image, crop_facts = render_look.cropped(drawn["image"], args)
    facts.update(crop_facts)
    return _deliver_image(args, image, facts)


def _render_map(args: dict) -> dict:






    target = str(args.get("target") or "canvas").strip().lower()
    if target not in _TARGETS:
        return {"_error": f"target must be canvas, layout or file, not {target!r}.",
                "_code": "INVALID_ARGS",
                "_suggestion": "canvas draws the map view, layout draws a page of a print layout, "
                               "file draws a PNG, JPG, SVG or PDF already written."}
    if target != "canvas":
        return _render_target(args)
    planned = run_on_main_thread(_plan_render, args, timeout=30)
    if "_error" in planned:
        return planned
    settings, width, height = planned["settings"], planned["width"], planned["height"]




    fingerprint = planned.get("fingerprint") or ""
    cached = _cached_render(fingerprint)
    if cached is not None:
        kept = dict(planned)
        kept.update(width=cached["width"], height=cached["height"],
                    layers_rendered=cached["layers_rendered"], labels=cached.get("labels"))
        result = _render_answer(args, cached["image"], kept, cached["passes"], True, [])
        if "_error" not in result:
            result["unchanged"] = True
            result["note"] = ("Nothing on the map changed since the last render_map of this run, so this is "
                              "that same picture, returned without rendering again. render_map gives the "
                              "same image again until the map, the extent or the size changes.")
        return result

    began = time.monotonic()
    image, passes, stable, render_errors, labels = _render_once_async(settings, planned["tiles"])
    full_errors = list(render_errors)
    if image is None and any(e.get("message") == _STOPPED for e in render_errors):
        return {"_error": "The render was stopped.", "code": "CANCELLED"}
    reduced = None
    if image is None and not _stop_requested():



        small = run_on_main_thread(_shrink_for_fallback, settings, width, height, timeout=10)
        image, render_errors, labels = _render_pass_async(settings, _FALLBACK_SECONDS)
        passes += 1
        stable = False
        reduced = small
    if image is None and any(e.get("message") == _STOPPED for e in render_errors):
        return {"_error": "The render was stopped.", "code": "CANCELLED"}
    if image is None:
        slow = planned.get("slow_layers") or []
        elapsed = time.monotonic() - began
        small = f"{reduced[0]} by {reduced[1]}" if reduced else "640 pixels"
        error = {"_error": (f"The map did not finish rendering in {elapsed:.0f} s, at {width} by {height} "
                            f"or at {small}."),
                 "elapsed_s": round(elapsed),
                 "suggestion": ("set_layers_visibility hides a heavy layer; a smaller extent draws less. "
                                + (f"These draw over the network or from pixels: {', '.join(slow[:6])}."
                                   if slow else "")).strip(),
                 "render_errors": full_errors + [e for e in render_errors if e not in full_errors]}
        if slow:
            error["slow_layers"] = slow
        return error
    if reduced is not None:
        planned = dict(planned)
        planned["width"], planned["height"] = reduced
    if labels is not None and planned.get("labelled"):
        planned = dict(planned)
        planned["labels"] = run_on_main_thread(_label_facts, labels, settings, planned["labelled"], timeout=10)
    result = _render_answer(args, image, planned, passes, stable, render_errors)
    if "_error" in result:
        return result
    if reduced is not None:
        result["reduced_from"] = {"width": width, "height": height}
        result["reduced_note"] = (f"The map was too slow to draw at {width} by {height}, so this is "
                                  f"{reduced[0]} by {reduced[1]}. Hide a heavy layer or render a smaller "
                                  "extent before asking for the full size again.")
    elif stable and not result.get("blank") and not render_errors:
        _keep_render(fingerprint, image, planned, passes)
    return result


def _view_size(args: dict) -> tuple:





    width, height = args.get("width"), args.get("height")
    if width and height and args.get("extent"):
        return int(width), int(height)
    aspect = 0.0
    if not args.get("extent"):
        try:
            canvas = iface.mapCanvas()
            if canvas.width() > 0 and canvas.height() > 0:
                aspect = canvas.width() / canvas.height()
        except Exception:  # noqa: BLE001
            aspect = 0.0
    if not 0.2 <= aspect <= 5.0:
        return int(width or 800), int(height or 600)
    if width and height:
        if abs(int(width) / int(height) - aspect) <= 0.03 * aspect:
            return int(width), int(height)
        area = int(width) * int(height)
        return max(64, round((area * aspect) ** 0.5)), max(64, round((area / aspect) ** 0.5))
    if width:
        return int(width), max(64, round(int(width) / aspect))
    if height:
        return max(64, round(int(height) * aspect)), int(height)
    area = 800 * 600
    return max(64, round((area * aspect) ** 0.5)), max(64, round((area / aspect) ** 0.5))


def _clamp_render_size(asked_width, asked_height) -> tuple:













    asked_width, asked_height = max(1, int(asked_width)), max(1, int(asked_height))
    max_w = limits.current("MAX_RENDER_WIDTH_PX")
    max_h = limits.current("MAX_RENDER_HEIGHT_PX")
    width, height = min(asked_width, max_w), min(asked_height, max_h)
    if (width, height) == (asked_width, asked_height):
        return width, height, ""
    shape = min(width / float(asked_width), height / float(asked_height))
    width = max(64, min(width, int(round(asked_width * shape))))
    height = max(64, min(height, int(round(asked_height * shape))))
    note = (f"Asked for {asked_width} by {asked_height}, rendered at {width} by {height}: "
            f"this machine renders at most {max_w} by {max_h} pixels. Nothing downstream reads "
            "more than this size.")
    return width, height, note


def _plan_render(args: dict) -> dict:

    asked_width, asked_height = _view_size(args)
    width, height, size_note = _clamp_render_size(asked_width, asked_height)

    settings = QgsMapSettings()
    settings.setOutputSize(QSize(width, height))




    settings.setTransformContext(QgsProject.instance().transformContext())
    _apply_quality_flags(settings)





    canvas_crs = None
    try:
        canvas_crs = iface.mapCanvas().mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001
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




    layer_names = args.get("layer_names")
    order_note = ""
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
        layers, order_note = _in_project_order(layers)
    else:
        layers = list(iface.mapCanvas().layers())

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
            except Exception:  # noqa: BLE001
                degrees_note = ""
        settings.setExtent(rectangle)
    else:
        settings.setExtent(iface.mapCanvas().extent())

    settings.setLayers(layers)




    destination = settings.destinationCrs()
    data_rect, outside, inside = _framing(layers, destination, settings.extent())
    frame_note = ""
    if data_rect is not None and not inside and outside and not extent and layer_names:





        padded = QgsRectangle(data_rect)
        padded.grow(max(data_rect.width(), data_rect.height()) * 0.05 or 1.0)
        settings.setExtent(padded)
        frame_note = ("The view held none of these layers, so the render was framed on their own extent "
                      f"({', '.join(outside[:8])}) instead of the canvas. zoom_to_layer moves the canvas "
                      "there too.")
        data_rect, outside, inside = _framing(layers, destination, settings.extent())

    _apply_background(settings, args.get("background"))
    tiles = any(_is_tiled_raster(layer) for layer in layers)
    notes = [note for note in (degrees_note, frame_note) if note]
    plan = {"settings": settings, "layers_rendered": [layer.name() for layer in layers],
            "labelled": [layer.id() for layer in layers if isinstance(layer, QgsVectorLayer) and layer.labelsEnabled()],
            "width": width, "height": height, "tiles": tiles, "extent_note": " ".join(notes),
            "slow_layers": _slow_layer_names(layers), "off_extent": outside, "in_extent": inside,
            "out_of_scale": _out_of_scale(layers, settings)}
    if size_note:
        plan["size_note"] = size_note
    if order_note:
        plan["order_note"] = order_note
    if layer_names:







        plan["canvas_unchanged"] = True
        left_out = _left_out(layers, destination, settings.visibleExtent())
        if left_out:
            plan["left_out"] = left_out
            covering = [entry["name"] for entry in left_out if entry.get("draws_over")]
            plan["layout_note"] = (
                "Only the named layers were drawn as a preview; the live canvas and each layer's visibility "
                "were left unchanged. left_out lists the visible layers this picture does not show, the share "
                "of the view each covers and the drawn layers it sits above"
                + (f" ({', '.join(covering[:4])} draw over what you rendered, so the user's map does not look "
                   "like this picture: render without layer_names to see it)" if covering else "")
                + ". A layout map draws those too: pass this same list as layers to add_layout_map, "
                "or hide them, before exporting.")
        else:
            plan["layout_note"] = ("Only the named layers were drawn as a preview; the live canvas and each "
                                   "layer's visibility were left unchanged.")
    if data_rect is not None:
        plan["data_extent"] = _bbox_of(data_rect)
    plan["extent"] = _drawn_extent(settings)
    plan["fingerprint"] = _render_fingerprint(settings, layers, args)
    return plan










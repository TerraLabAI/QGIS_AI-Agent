# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

import os
import threading
import time

from qgis.core import (
    QgsFeatureRequest,
    QgsFeedback,
    QgsProject,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)

from ..core import limits, net
from ..core.crs_ref import crs_ref
from ..core.host_platform import remove_tree
from ..core.policy import create_managed_temp_dir
from ..core.quiet_credentials import no_login_prompt
from .data_common import _avoid_reserved_name, _run_on_main_thread



_CLOCK_SHARE = 0.75

_COMMIT_EVERY = 20_000

_POLL_S = 0.1


_CLOSE_WAIT_S = 30.0

_OPEN_TIMEOUT = 60
_ADD_TIMEOUT = 30


_BOOL, _INT, _UINT, _LONG, _ULONG, _DOUBLE = 1, 2, 3, 4, 5, 6
_DATE, _TIME, _DATETIME = 14, 15, 16


def _type_number(field) -> int:
    kind = field.type()
    try:
        return int(getattr(kind, "value", kind))
    except (TypeError, ValueError):
        return 10


def _open(uri: str, name: str, state: dict) -> dict | None:

    with no_login_prompt():
        layer = QgsVectorLayer(uri, name, "WFS")
    if not layer.isValid():
        try:
            said = layer.error().summary()
        except Exception:  # noqa: BLE001
            said = ""
        return {"_invalid": True, "_qgis_message": said}
    request = QgsFeatureRequest()
    feedback = QgsFeedback()
    if hasattr(request, "setFeedback"):
        request.setFeedback(feedback)
    crs = layer.crs()
    state.update(
        source=QgsVectorLayerFeatureSource(layer), request=request, feedback=feedback,
        fields=[(field.name(), _type_number(field)) for field in layer.fields()],
        wkb=int(getattr(layer.wkbType(), "value", layer.wkbType())),
        authid=crs.authid(), wkt=crs.toWkt(),
    )
    return None


def _ogr_geometry_type(ogr, wkb: int) -> int:

    if wkb & 0x80000000:
        flat, has_z, has_m = wkb & 0xFF, True, False
    else:
        flat, modifier = wkb % 1000, wkb // 1000
        has_z, has_m = modifier in (1, 3), modifier in (2, 3)
    if flat == 100:
        return ogr.wkbNone
    if not 1 <= flat <= 17:
        return ogr.wkbUnknown
    kind = flat
    if has_z:
        kind = ogr.GT_SetZ(kind)
    if has_m:
        kind = ogr.GT_SetM(kind)
    return kind


def _plain(value, number: int):

    if value is None:
        return None
    is_null = getattr(value, "isNull", None)
    if callable(is_null) and not isinstance(value, (str, bytes)):
        try:
            if is_null():
                return None
        except TypeError:
            pass
    if number in (_DATE, _TIME, _DATETIME) and hasattr(value, "toString"):
        pattern = {_DATE: "yyyy-MM-dd", _TIME: "HH:mm:ss", _DATETIME: "yyyy-MM-ddTHH:mm:ss"}[number]
        return value.toString(pattern) or None
    if number == _BOOL:
        return int(bool(value))
    if isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _create_layer(ogr, osr, path: str, name: str, state: dict):
    driver = ogr.GetDriverByName("GPKG")
    dataset = driver.CreateDataSource(path)
    if dataset is None:
        raise OSError(f"OGR could not create {path}")
    srs = osr.SpatialReference()
    authid = str(state.get("authid") or "")
    if authid.upper().startswith("EPSG:") and authid[5:].isdigit():
        srs.ImportFromEPSG(int(authid[5:]))
    elif state.get("wkt"):
        srs.ImportFromWkt(state["wkt"])
    else:
        srs = None
    if srs is not None and hasattr(osr, "OAMS_TRADITIONAL_GIS_ORDER"):
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    geometry_type = _ogr_geometry_type(ogr, state["wkb"])
    layer = dataset.CreateLayer(name, srs, geometry_type, ["GEOMETRY_NAME=geom"])
    if layer is None:
        raise OSError(f"OGR could not create the layer {name} in {path}")
    kinds = {_BOOL: ogr.OFTInteger, _INT: ogr.OFTInteger, _UINT: ogr.OFTInteger64, _LONG: ogr.OFTInteger64,
             _ULONG: ogr.OFTInteger64, _DOUBLE: ogr.OFTReal, _DATE: ogr.OFTDate, _TIME: ogr.OFTTime,
             _DATETIME: ogr.OFTDateTime}
    for field_name, number in state["fields"]:
        definition = ogr.FieldDefn(field_name, kinds.get(number, ogr.OFTString))
        if number == _BOOL:
            definition.SetSubType(ogr.OFSTBoolean)
        layer.CreateField(definition)
    return dataset, layer, geometry_type


def _write(state: dict, path: str, name: str, halted: threading.Event) -> None:

    from osgeo import ogr, osr

    dataset, layer, geometry_type = _create_layer(ogr, osr, path, name, state)
    definition = layer.GetLayerDefn()
    numbers = [number for _name, number in state["fields"]]
    flat_multi = geometry_type not in (ogr.wkbNone, ogr.wkbUnknown) and ogr.GT_IsSubClassOf(
        ogr.GT_Flatten(geometry_type), ogr.wkbGeometryCollection)
    cap = int(limits.current("MAX_STREAM_BYTES"))
    written = 0
    features = state["source"].getFeatures(state["request"])
    layer.StartTransaction()
    try:
        for feature in features:
            if halted.is_set():
                break
            out = ogr.Feature(definition)
            for index, value in enumerate(feature.attributes()[:len(numbers)]):
                plain = _plain(value, numbers[index])
                if plain is not None:
                    out.SetField(index, plain)
            geometry = feature.geometry()
            if geometry is not None and not geometry.isNull():
                shape = ogr.CreateGeometryFromWkb(bytes(geometry.asWkb()))
                if shape is not None:
                    if flat_multi and shape.GetGeometryType() != geometry_type and hasattr(ogr, "ForceTo"):
                        shape = ogr.ForceTo(shape, geometry_type)
                    out.SetGeometry(shape)
            layer.CreateFeature(out)
            written += 1
            if written % _COMMIT_EVERY == 0:
                layer.CommitTransaction()
                state["written"] = written
                size = os.path.getsize(path)
                free = net.free_disk_bytes(os.path.dirname(path))
                if size >= cap or (free is not None and free < net.STREAM_KEEP_FREE_BYTES):
                    state["ended_by"] = "disk"
                    layer.StartTransaction()
                    break
                layer.StartTransaction()
    finally:
        features.close()
        layer.CommitTransaction()
        state["written"] = written
        del layer
        dataset = None  # noqa: F841


def extract(url: str, typename: str, name: str, uri: str, hits: int | None = None) -> dict:







    try:
        from osgeo import ogr  # noqa: F401
    except ImportError:
        return {"_error": "GDAL's Python bindings are not available, so the WFS cannot be written to a file.",
                "code": "EXECUTION_FAILED",
                "suggestion": "Load it with a bbox or max_features instead, which keeps it a live WFS layer."}
    started = time.monotonic()
    deadline = started + limits.current("CALL_MAX_SECONDS_BACKGROUND") * _CLOCK_SHARE
    state: dict = {"written": 0, "ended_by": ""}
    opened = _run_on_main_thread(_open, uri, name, state, timeout=_OPEN_TIMEOUT)
    if isinstance(opened, dict):
        return opened
    directory = create_managed_temp_dir("wfs")
    stem = _avoid_reserved_name("".join(c if c.isalnum() or c in "-_." else "_" for c in typename)[:60] or "wfs")
    path = os.path.join(directory, f"{stem}.gpkg")
    table = stem.replace(".", "_")
    halted, over, failure = threading.Event(), threading.Event(), []
    keep = {"file": True}

    def reader(source, request, feedback):
        try:
            _write(state, path, table, halted)
        except BaseException as exc:  # noqa: BLE001
            failure.append(exc)
        finally:
            over.set()
            if not keep["file"]:
                remove_tree(directory)

    thread = threading.Thread(target=reader, name="add_wfs_layer write",
                              args=(state["source"], state["request"], state["feedback"]), daemon=True)
    thread.start()
    cancelled = net.current_cancel_check()
    while not over.wait(_POLL_S):
        if cancelled is not None and cancelled():
            keep["file"] = False
            halted.set()
            state["feedback"].cancel()
            return {"_error": "Stopped while the WFS was being written to disk; nothing was added.",
                    "code": "CANCELLED", "suggestion": "Stop here and wait for the next user message."}
        if time.monotonic() > deadline and not halted.is_set():
            state["ended_by"] = "clock"
            halted.set()
            state["feedback"].cancel()
            if not over.wait(_CLOSE_WAIT_S):
                keep["file"] = False
                return {"_error": (f"The WFS was still sending {typename} when the clock ran out, and the file "
                                   "could not be closed in time; nothing was added."),
                        "code": "TIMEOUT",
                        "suggestion": "Ask for a smaller area with bbox, or a filter on the type."}
    if failure:
        remove_tree(directory)
        exc = failure[0]
        return {"_error": f"Writing {typename} to a GeoPackage failed: {type(exc).__name__}: {exc}",
                "code": "EXECUTION_FAILED",
                "suggestion": "Load it with a bbox or max_features instead, which keeps it a live WFS layer."}
    written = int(state.get("written") or 0)

    def _add():
        layer = QgsVectorLayer(f"{path}|layername={table}", name, "ogr")
        if not layer.isValid():
            return {"_error": f"QGIS could not read the GeoPackage written to {path}.",
                    "code": "EXECUTION_FAILED"}
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "feature_count": layer.featureCount(),
                "crs": crs_ref(layer.crs())}

    out = _run_on_main_thread(_add, timeout=_ADD_TIMEOUT)
    if out.get("_error"):
        remove_tree(directory)
        return out
    wall = round(time.monotonic() - started, 1)
    out.update({"url": url, "typename": typename, "path": path, "provider": "WFS written to a GeoPackage",
                "size_bytes": os.path.getsize(path) if os.path.exists(path) else None, "wall_s": wall})
    ended_by = state.get("ended_by")
    if ended_by:
        out["coverage"] = "partial"
        why = ("the clock ran out" if ended_by == "clock"
               else "the disk cap or the free space was reached")
        out["warning"] = (f"Only {out.get('feature_count', written):,} features were written before {why}, "
                          "the first ones in the service's own order, which can all lie in one part of the area.")
        out["suggestion"] = ("Say the layer is partial. A smaller bbox per call, with the same full_extent, reads "
                             "the rest.")
    elif isinstance(hits, int) and hits > 0 and isinstance(out.get("feature_count"), int) \
            and out["feature_count"] < hits:
        out["features_available"] = hits
        out["coverage"] = "partial"
        out["warning"] = (f"{out['feature_count']:,} of the {hits:,} features the service counts came back: "
                          "it stops a request there and does not page past it.")
        out["suggestion"] = "Say the layer is partial, or read the type in smaller boxes with bbox."
    return out

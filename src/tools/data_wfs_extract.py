# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

























from __future__ import annotations

import os
import threading
import time

from qgis.core import QgsDataSourceUri, QgsFeatureRequest, QgsProject, QgsVectorLayer
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QEventLoop, Qt

from ..core import background, limits, net, quiet_credentials
from ..core.background import run_on_main_thread
from ..core.crs_ref import crs_ref
from ..core.host_platform import remove_tree
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.tool_registry import coded_fact, tool_error
from .data_common import _avoid_reserved_name, built_here, worker_options



_CLOCK_SHARE = 0.75

_COMMIT_EVERY = 20_000

_POLL_S = 0.1


_CLOSE_WAIT_S = 30.0
_ADD_TIMEOUT = 30


_BOOL, _INT, _UINT, _LONG, _ULONG, _DOUBLE = 1, 2, 3, 4, 5, 6
_DATE, _TIME, _DATETIME = 14, 15, 16
_ISO_WITH_MS = enum_member(Qt, "DateFormat", "ISODateWithMs")


def _type_number(field) -> int:
    kind = field.type()
    try:
        return int(getattr(kind, "value", kind))
    except (TypeError, ValueError):
        return 10


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
    if number == _DATETIME and hasattr(value, "toString"):


        return value.toString(_ISO_WITH_MS) or None
    if number in (_DATE, _TIME) and hasattr(value, "toString"):
        return value.toString("yyyy-MM-dd" if number == _DATE else "HH:mm:ss") or None
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


    taken = {field_name.lower() for field_name, _number in state["fields"]}
    fid = next(candidate for candidate in ("fid", "gpkg_fid", "wfs_copy_fid") if candidate not in taken)
    layer = dataset.CreateLayer(name, srs, geometry_type, ["GEOMETRY_NAME=geom", f"FID={fid}"])
    if layer is None:
        raise OSError(f"OGR could not create the layer {name} in {path}")
    kinds = {_BOOL: ogr.OFTInteger, _INT: ogr.OFTInteger, _UINT: ogr.OFTInteger64, _LONG: ogr.OFTInteger64,
             _ULONG: ogr.OFTInteger64, _DOUBLE: ogr.OFTReal, _DATE: ogr.OFTDate, _TIME: ogr.OFTTime,
             _DATETIME: ogr.OFTDateTime}
    columns = []
    for field_name, number in state["fields"]:
        definition = ogr.FieldDefn(field_name, kinds.get(number, ogr.OFTString))
        if number == _BOOL:
            definition.SetSubType(ogr.OFSTBoolean)
        try:
            made = layer.CreateField(definition) == 0
        except RuntimeError:
            made = False
        index = layer.GetLayerDefn().GetFieldIndex(field_name) if made else -1
        columns.append(index if index >= 0 else None)
    return dataset, layer, geometry_type, columns


def _write(state: dict, layer, path: str, name: str, halted: threading.Event) -> None:

    from osgeo import ogr, osr

    dataset, table, geometry_type, columns = _create_layer(ogr, osr, path, name, state)
    definition = table.GetLayerDefn()
    numbers = [number for _name, number in state["fields"]]
    flat_multi = geometry_type not in (ogr.wkbNone, ogr.wkbUnknown) and ogr.GT_IsSubClassOf(
        ogr.GT_Flatten(geometry_type), ogr.wkbGeometryCollection)
    cap = int(limits.current("MAX_STREAM_BYTES"))
    written = 0
    features = layer.getFeatures(QgsFeatureRequest())
    table.StartTransaction()
    try:
        for feature in features:
            if halted.is_set():
                break
            out = ogr.Feature(definition)
            for index, value in enumerate(feature.attributes()[:len(numbers)]):
                plain = _plain(value, numbers[index])
                if plain is not None and columns[index] is not None:
                    out.SetField(columns[index], plain)
            geometry = feature.geometry()
            if geometry is not None and not geometry.isNull():
                shape = ogr.CreateGeometryFromWkb(bytes(geometry.asWkb()))
                if shape is not None:
                    if flat_multi and shape.GetGeometryType() != geometry_type and hasattr(ogr, "ForceTo"):
                        shape = ogr.ForceTo(shape, geometry_type)
                    out.SetGeometry(shape)
            table.CreateFeature(out)
            written += 1


            background.breathe(written)
            if written % _COMMIT_EVERY == 0:
                table.CommitTransaction()
                state["written"] = written
                size = os.path.getsize(path)
                free = net.free_disk_bytes(os.path.dirname(path))
                if size >= cap or (free is not None and free < net.STREAM_KEEP_FREE_BYTES):
                    state["ended_by"] = "disk"
                    table.StartTransaction()
                    break
                table.StartTransaction()
    finally:
        features.close()

        del features
        table.CommitTransaction()
        state["written"] = written
        del table
        dataset = None  # noqa: F841


def _fell_short(written: int, uri: str, layer) -> bool:

    try:
        asked = int(QgsDataSourceUri(uri).param("maxNumFeatures") or 0)
        counted = int(layer.featureCount())
    except (TypeError, ValueError):
        return False
    wanted = [number for number in (asked, counted) if number > 0]
    return bool(wanted) and written < min(wanted)


def _read(uri: str, name: str, path: str, table: str, state: dict, halted: threading.Event) -> None:





    QEventLoop()
    layer = QgsVectorLayer(uri, name, "WFS", worker_options())



    reported: list[str] = []
    layer.raiseError.connect(reported.append)
    try:
        if not layer.isValid():
            try:
                state["invalid"] = str(layer.error().summary() or "")
            except Exception:  # noqa: BLE001
                state["invalid"] = ""
            return
        crs = layer.crs()
        state.update(fields=[(field.name(), _type_number(field)) for field in layer.fields()],
                     wkb=int(getattr(layer.wkbType(), "value", layer.wkbType())),
                     authid=crs.authid(), wkt=crs.toWkt())
        if halted.is_set():
            return
        if state.get("view"):






            view_filter, most = state["view"]
            counted = layer.featureCount()
            state["where_count"] = counted
            if not 0 < counted <= most:
                if not layer.setSubsetString(f"({layer.subsetString()}) AND ({view_filter})"):
                    raise RuntimeError("QGIS did not take the map view's box on the where")
                state["in_view"] = True
        _write(state, layer, path, table, halted)
        if halted.is_set():
            return
        count_at = state.get("count_at")
        if count_at and state["written"] >= count_at:


            state["matched"] = layer.featureCount()


        QCoreApplication.processEvents()



        if reported and (not state["written"] or _fell_short(state["written"], uri, layer)):
            state["service_error"] = str(reported[-1])[:400]
    finally:
        sip.delete(layer)


        deferred = enum_member(QEvent, "Type", "DeferredDelete")
        QCoreApplication.sendPostedEvents(None, int(getattr(deferred, "value", deferred)))


def misses_the_view(layer) -> tuple[str, str]:








    try:
        from qgis.core import QgsCoordinateTransform
        from qgis.utils import iface as qgis_iface

        canvas = qgis_iface.mapCanvas() if qgis_iface is not None else None
        if canvas is None:
            return "", ""
        view = canvas.extent()
        if view.isEmpty():
            return "", ""
        extent = layer.extent()
        if extent.isEmpty():
            return "The layer reports an empty extent, so nothing will draw.", ""
        target = canvas.mapSettings().destinationCrs()
        if layer.crs().isValid() and target.isValid() and layer.crs() != target:
            extent = QgsCoordinateTransform(layer.crs(), target,
                                            QgsProject.instance()).transformBoundingBox(extent)
        if extent.intersects(view):
            return "", ""
        return ("The layer's own extent does not reach the current view, so the map will look empty.",
                "layer_off_view")
    except Exception:  # noqa: BLE001
        return "", ""


def extract(url: str, typename: str, name: str, uri: str, count_at: int | None = None,
            view: tuple[str, int] | None = None) -> dict:













    started = time.monotonic()
    deadline = started + limits.current("CALL_MAX_SECONDS_BACKGROUND") * _CLOCK_SHARE
    directory = create_managed_temp_dir("wfs")
    stem = _avoid_reserved_name("".join(c if c.isalnum() or c in "-_." else "_" for c in typename)[:60] or "wfs")
    path = os.path.join(directory, f"{stem}.gpkg")
    table = stem.replace(".", "_")
    state: dict = {"written": 0, "ended_by": "", "count_at": count_at, "view": view}
    halted, over, failure = threading.Event(), threading.Event(), []
    keep = {"file": True}
    try:
        held = quiet_credentials.hold_from_worker()
    except BaseException:
        remove_tree(directory)
        raise

    def reader():
        try:
            _read(uri, name, path, table, state, halted)
        except BaseException as exc:  # noqa: BLE001
            failure.append(f"{type(exc).__name__}: {exc}")
        finally:
            if held:
                quiet_credentials.release_from_worker()
            over.set()
            if not keep["file"]:
                remove_tree(directory)

    def drop() -> None:

        keep["file"] = False
        halted.set()
        if over.is_set():
            remove_tree(directory)

    background.start_kept_thread(reader, name="add_wfs_layer read")
    cancelled = net.current_cancel_check()
    while not over.wait(_POLL_S):
        background.heartbeat()
        if cancelled is not None and cancelled():
            drop()
            return {"_error": "Stopped while the WFS was being written to disk; nothing was added.",
                    "code": "CANCELLED", "suggestion": "The user stopped the run."}
        if time.monotonic() > deadline and not halted.is_set():
            state["ended_by"] = "clock"
            halted.set()
            if not over.wait(_CLOSE_WAIT_S):
                drop()
                return tool_error(f"The WFS was still sending {typename} when the clock ran out, and the file "
                                  "could not be closed in time; nothing was added.", "TIMEOUT",
                                  hint="wfs_clock_ran_out", typename=typename)
    if "invalid" in state:
        remove_tree(directory)
        return {"_invalid": True, "_qgis_message": state["invalid"]}
    if failure:
        remove_tree(directory)
        return {"_error": f"Writing {typename} to a GeoPackage failed: {failure[0]}", "code": "EXECUTION_FAILED"}
    written = int(state.get("written") or 0)
    if state.get("service_error") and not written:
        remove_tree(directory)
        return {"_error": f"The service answered an error instead of the features of {typename}. "
                          f"QGIS said: {state['service_error']}", "code": "EXECUTION_FAILED",
                "_fields": [field_name for field_name, _number in state.get("fields") or ()]}
    if not os.path.exists(path):
        remove_tree(directory)
        return tool_error(f"The clock ran out while the WFS was describing {typename}, before any feature "
                          "came back; nothing was added.", "TIMEOUT",
                          hint="wfs_clock_ran_out", typename=typename)
    source = f"{path}|layername={table}"
    facts: dict = {}

    def make():
        made = QgsVectorLayer(source, name, "ogr", worker_options())
        if made.isValid():
            facts["feature_count"] = made.featureCount()
        return made

    built = built_here(make)
    added = {"layer": False}

    def _add():
        if not background.still_awaited():

            if built is not None:
                built.release()
            return {"_error": "The call ended before the layer could be added.", "code": "CANCELLED"}
        layer = background.take(built)
        if layer is None:
            layer = QgsVectorLayer(source, name, "ogr")
        if not layer.isValid():

            background.delete_here(layer)
            return {"_error": f"QGIS could not read the GeoPackage written to {path}.",
                    "code": "EXECUTION_FAILED"}
        added["layer"] = True
        QgsProject.instance().addMapLayer(layer)
        out = {"layer_name": layer.name(), "layer_id": layer.id(),
               "feature_count": facts["feature_count"] if "feature_count" in facts else layer.featureCount(),
               "crs": crs_ref(layer.crs())}
        missed, missed_hint = misses_the_view(layer)
        if missed:
            out["_note"] = missed
            if missed_hint:
                out.update(coded_fact(missed_hint))
        return out

    try:
        out = run_on_main_thread(_add, timeout=_ADD_TIMEOUT)
    except BaseException:
        if built is not None:
            built.release()
        if not added["layer"]:
            remove_tree(directory)
        raise
    if out.get("_error"):
        remove_tree(directory)
        return out
    out.update({"path": path, "size_bytes": os.path.getsize(path) if os.path.exists(path) else None,
                "wall_s": round(time.monotonic() - started, 1)})
    for key in ("ended_by", "matched", "service_error", "where_count", "in_view"):
        if state.get(key) not in (None, ""):
            out[f"_{key}"] = state[key]
    return out

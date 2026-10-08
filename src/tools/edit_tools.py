# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import uuid

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsLineString,
    QgsPoint,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsSnappingConfig,
    QgsTolerance,
    QgsVectorLayer,
    QgsVectorLayerEditUtils,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

try:
    from qgis.utils import iface
except Exception:  # pragma: no cover
    iface = None

from ..core.layer_order import feature_count_of
from ..core.qt_compat import enum_member
from ..core.tool_registry import NAMED_LAYER, Tool, ToolRegistry, coded_fact, tool_error
from . import vector_write


_edit_sessions: dict[str, dict] = {}

_layer_tokens: dict[str, str] = {}






def _resolve_vector(name_or_id: str):





    proj = QgsProject.instance()
    layer = proj.mapLayer(name_or_id)
    if layer is None:
        matches = proj.mapLayersByName(name_or_id)
        if not matches:
            return None, {"_error": f"Layer not found: {name_or_id}"}
        if len(matches) > 1:
            ids = [m.id() for m in matches]
            return None, {
                "_error": (
                    f"Ambiguous layer name '{name_or_id}': {len(matches)} layers match. "
                    f"ids: {ids}"
                )
            }
        layer = matches[0]
    if not isinstance(layer, QgsVectorLayer):
        return None, {
            "_error": (
                f"Layer '{name_or_id}' is not a vector layer (type: {type(layer).__name__}). "
                "Digitizing tools require a vector layer."
            )
        }
    return layer, None


def _validity(layer: QgsVectorLayer, fid) -> dict:







    if fid is None:
        return {}
    try:
        geometry = layer.getFeature(fid).geometry()
        if geometry is None or geometry.isNull():
            return {}
        if geometry.isGeosValid():
            return {"geometry_valid": True}
    except Exception:  # noqa: BLE001
        return {}
    from .query_tools import _invalid_reasons

    out = {"geometry_valid": False}
    reasons = _invalid_reasons(geometry)
    if reasons:
        out["geometry_invalid_reason"] = "; ".join(reasons)
    return out


def _require_editable(layer: QgsVectorLayer):

    if not layer.isEditable():
        return tool_error(f"Layer '{layer.name()}' has no open edit session.",
                          hint="edit_session_required", layer=layer.name())
    return None


def _set_vertex_and_segment_snapping(config) -> None:










    try:
        flags = (
            enum_member(QgsSnappingConfig, "SnappingTypes", "VertexFlag")
            | enum_member(QgsSnappingConfig, "SnappingTypes", "SegmentFlag")
        )
        config.setTypeFlag(flags)
    except (TypeError, AttributeError):




        config.setType(vars(QgsSnappingConfig)["VertexAndSegment"])


def _snapping_enum(container, *names):

    for name in names:
        try:
            return enum_member(QgsSnappingConfig, container, name)
        except (AttributeError, KeyError):
            continue
    raise AttributeError(f"QGIS snapping enum {container}.{names[0]} is unavailable")


def _snapping_mode(value):
    names = {
        "active": ("ActiveLayer",),
        "all": ("AllLayers",),
        "advanced": ("AdvancedConfiguration", "Advanced"),
    }
    try:
        return _snapping_enum("SnappingMode", *names[value])
    except KeyError:
        raise ValueError("mode must be one of: active, all, advanced") from None


def _snapping_units(value):
    names = {
        "pixels": ("Pixels",),
        "map_units": ("MapUnits", "ProjectUnits"),
        "layer_units": ("LayerUnits",),
        "millimeters": ("Millimeters", "Millimeter"),
        "points": ("Points", "Point"),
        "inches": ("Inches", "Inch"),
    }
    try:
        return enum_member(QgsTolerance, "UnitType", *names[value])
    except KeyError:
        raise ValueError("units must be pixels, map_units, layer_units, millimeters, points, or inches") from None
    except AttributeError:
        raise ValueError(f"QGIS does not support snapping units '{value}'") from None


def _snapping_type(value):

    if value == "both":
        try:
            return (
                enum_member(QgsSnappingConfig, "SnappingTypes", "VertexFlag")
                | enum_member(QgsSnappingConfig, "SnappingTypes", "SegmentFlag")
            )
        except (AttributeError, TypeError):
            try:
                return (
                    enum_member(Qgis, "SnappingType", "Vertex")
                    | enum_member(Qgis, "SnappingType", "Segment")
                )
            except (AttributeError, TypeError):
                return vars(QgsSnappingConfig)["VertexAndSegment"]
    names = {"vertex": "VertexFlag", "segment": "SegmentFlag"}
    if value not in names:
        raise ValueError("types must be one of: vertex, segment, both")
    try:
        return enum_member(QgsSnappingConfig, "SnappingTypes", names[value])
    except AttributeError:
        return enum_member(Qgis, "SnappingType", value.title())


def _set_snapping_type(config, value) -> None:
    flag = _snapping_type(value)
    try:
        config.setTypeFlag(flag)
    except (AttributeError, TypeError):
        if value != "both":
            raise
        config.setType(vars(QgsSnappingConfig)["VertexAndSegment"])


def _individual_layer_settings(layer, args: dict):

    cls = getattr(QgsSnappingConfig, "IndividualLayerSettings", None)
    if cls is None:
        return None
    try:
        return cls(
            bool(args.get("enabled", True)),
            _snapping_type(args.get("types", "both")),
            float(args.get("tolerance", 12)),
            _snapping_units(args.get("units", "pixels")),
        )
    except (AttributeError, TypeError, ValueError):
        return None


def _set_snapping_config(args: dict) -> dict:

    proj = QgsProject.instance()
    enabled = bool(args.get("enabled", True))
    mode_name = args.get("mode", "all")
    types_name = args.get("types", "both")
    units_name = args.get("units", "pixels")
    tolerance = float(args.get("tolerance", 12))
    if tolerance < 0:
        return {"_error": "tolerance must be zero or greater."}
    try:
        mode = _snapping_mode(mode_name)
        units = _snapping_units(units_name)
    except ValueError as exc:
        return {"_error": str(exc)}
    cfg = proj.snappingConfig()
    try:
        cfg.setEnabled(enabled)
        cfg.setMode(mode)
        _set_snapping_type(cfg, types_name)
        cfg.setTolerance(tolerance)
        cfg.setUnits(units)
        intersection = bool(args.get("intersection_snapping", False))
        if hasattr(cfg, "setIntersectionSnapping"):
            cfg.setIntersectionSnapping(intersection)
        elif intersection:
            return tool_error(
                "This QGIS version cannot configure intersection snapping.",
                "UNSUPPORTED_QGIS_VERSION",
                "intersection_snapping needs a newer QGIS.",
            )
        proj.setSnappingConfig(cfg)
    except (AttributeError, TypeError, ValueError) as exc:
        return {"_error": f"QGIS rejected the snapping configuration: {exc}"}

    applied, unsupported = [], []
    for override in args.get("layer_overrides", []):
        layer, err = _resolve_vector(override["layer_name"])
        if err:
            return err
        settings = _individual_layer_settings(layer, override)
        if settings is None or not hasattr(cfg, "setIndividualLayerSettings"):
            unsupported.append(layer.name())
            continue
        try:
            cfg.setIndividualLayerSettings(layer, settings)
            applied.append(layer.name())
        except (AttributeError, TypeError, ValueError):
            unsupported.append(layer.name())
    if applied:
        proj.setSnappingConfig(cfg)
    result = {
        "configured": True,
        "enabled": enabled,
        "mode": mode_name,
        "types": types_name,
        "tolerance": tolerance,
        "units": units_name,
        "topological_editing": bool(args.get("topological_editing", False)),
        "intersection_snapping": bool(args.get("intersection_snapping", False)),
        "layer_overrides_applied": applied,
    }
    if unsupported:
        result["layer_overrides_unsupported"] = unsupported
    proj.setTopologicalEditing(result["topological_editing"])
    return result


def _snapshot_aids(proj: QgsProject) -> dict:

    return {
        "snap": proj.snappingConfig(),
        "topo": proj.topologicalEditing(),
        "avoid_mode": proj.avoidIntersectionsMode(),
        "avoid_layer_ids": [lyr.id() for lyr in proj.avoidIntersectionsLayers()],
    }


def _apply_aids_snapshot(proj: QgsProject, snap: dict):

    proj.setTopologicalEditing(snap["topo"])
    proj.setAvoidIntersectionsMode(snap["avoid_mode"])
    restored_layers = [
        lyr for lyr in (proj.mapLayer(lid) for lid in snap["avoid_layer_ids"]) if lyr is not None
    ]
    proj.setAvoidIntersectionsLayers(restored_layers)
    proj.setSnappingConfig(snap["snap"])


def _reveal_digitize_toolbars():

    if iface is None:
        return
    for getter in (
        "digitizeToolBar",
        "advancedDigitizeToolBar",
        "shapeDigitizeToolBar",
    ):
        try:
            tb = getattr(iface, getter)()
            if tb is not None:
                tb.setVisible(True)
        except Exception:  # nosec B110
            pass


def _active_tool_class() -> str | None:
    if iface is None:
        return None
    try:
        tool = iface.mapCanvas().mapTool()
        return tool.__class__.__name__ if tool is not None else None
    except Exception:
        return None


def _line_points_from_wkt(wkt: str):

    geom = QgsGeometry.fromWkt(wkt)
    if geom.isNull():
        return None, {"_error": f"Invalid WKT line: {wkt[:100]}"}
    pts = geom.asPolyline()
    if len(pts) < 2:
        return None, {
            "_error": (
                "Line needs at least 2 vertices, as LINESTRING WKT in the "
                "layer's CRS, e.g. 'LINESTRING(x1 y1, x2 y2)'."
            )
        }
    return pts, None


def _release_aids(layer) -> bool:






    try:
        token = _layer_tokens.pop(layer.id(), None)
    except Exception:  # noqa: BLE001
        return False
    if token is None:
        return False
    snap = _edit_sessions.pop(token, None)
    if snap is None:
        return False
    try:
        _apply_aids_snapshot(QgsProject.instance(), snap)
    except Exception:  # noqa: BLE001
        return False
    return True






def _edit_begin(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err

    proj = QgsProject.instance()







    existing_token = _layer_tokens.get(layer.id())
    reused_session = existing_token is not None and existing_token in _edit_sessions
    prior = _edit_sessions[existing_token] if reused_session else _snapshot_aids(proj)

    if not layer.isEditable():




        _started, cannot = vector_write.open_edit(layer, "edit")
        if cannot:
            return cannot

    topological = bool(args.get("topological", True))
    avoid_overlap = bool(args.get("avoid_overlap", True))
    tol = args.get("snap_tolerance_px", 12)

    cfg = proj.snappingConfig()
    cfg.setEnabled(True)
    cfg.setMode(QgsSnappingConfig.SnappingMode.AllLayers)
    _set_vertex_and_segment_snapping(cfg)
    cfg.setTolerance(float(tol))
    cfg.setUnits(QgsTolerance.UnitType.Pixels)
    proj.setSnappingConfig(cfg)

    proj.setTopologicalEditing(topological)
    if avoid_overlap:
        proj.setAvoidIntersectionsMode(
            Qgis.AvoidIntersectionsMode.AvoidIntersectionsLayers
        )
        proj.setAvoidIntersectionsLayers([layer])
    else:
        proj.setAvoidIntersectionsMode(Qgis.AvoidIntersectionsMode.AllowIntersections)

    if iface is not None:
        try:
            iface.setActiveLayer(layer)
        except Exception:  # nosec B110
            pass
    _reveal_digitize_toolbars()

    if reused_session:
        token = existing_token
    else:
        token = uuid.uuid4().hex[:12]
        prior["layer_id"] = layer.id()
        _edit_sessions[token] = prior
        _layer_tokens[layer.id()] = token

    return {
        "state_token": token,
        "layer": layer.name(),
        "layer_id": layer.id(),
        "editing": layer.isEditable(),
        "snapping": {
            "enabled": True,
            "mode": "AllLayers",
            "types": "Vertex|Segment",
            "tolerance_px": float(tol),
        },
        "topological": topological,
        "avoid_overlap": avoid_overlap,
        "note": coded_fact(hint="edit_aids_saved", state_token=token),
    }


def _edit_commit(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err


    buf = layer.editBuffer()
    pending = {}
    if buf is not None:
        pending = {
            "added": len(buf.addedFeatures()),
            "changed_geometries": len(buf.changedGeometries()),
            "changed_attributes": len(buf.changedAttributeValues()),
            "deleted": len(buf.deletedFeatureIds()),
        }

    if not layer.commitChanges():
        errors = [str(message) for message in layer.commitErrors()]



        remaining = layer.editBuffer()
        return {
            "_error": (f"QGIS could not finish saving the edits to {layer.name()!r}: "
                       + ("; ".join(errors) or "the provider gave no detail")
                       + ". Uncommitted edits remain in the open edit session. "
                         "Some provider stages may already have been saved."),
            "code": "EXECUTION_FAILED",
            "layer": layer.name(),
            "editing": layer.isEditable(),
            "rolled_back": False,
            "provider_errors": errors[:8],
            "remaining": {
                "added": len(remaining.addedFeatures()),
                "changed_geometries": len(remaining.changedGeometries()),
                "changed_attributes": len(remaining.changedAttributeValues()),
                "deleted": len(remaining.deletedFeatureIds()),
            } if remaining is not None else {},
            **coded_fact(hint="edit_commit_incomplete", layer=layer.name()),
        }

    return {
        "committed": True,
        "layer": layer.name(),
        "changes": pending,



        "feature_count": feature_count_of(layer),
        "editing": layer.isEditable(),
        "note": coded_fact(hint="edit_aids_still_applied", state_token=_layer_tokens.get(layer.id())),
    }


def _edit_rollback(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err

    left_edit_mode = vector_write.force_out_of_edit(layer)

    aids_restored = _release_aids(layer)
    if not left_edit_mode:


        return {
            "_error": f"rollBack failed on layer '{layer.name()}', which is still in edit mode.",
            "code": "EXECUTION_FAILED",
            "aids_restored": aids_restored,
            **coded_fact(hint="edit_rollback_failed", layer=layer.name()),
        }

    return {
        "rolled_back": True,
        "layer": layer.name(),
        "editing": layer.isEditable(),
        "aids_restored": aids_restored,
    }


def _edit_restore_aids(args: dict) -> dict:
    token = args.get("state_token")
    if not token:
        return {"_error": "state_token is required (returned by qgis_edit_begin)."}
    snap = _edit_sessions.pop(token, None)
    if snap is None:
        active = list(_edit_sessions.keys())
        return {
            "_error": (
                f"Unknown state_token '{token}'. "
                f"Active tokens: {active or 'none'}."
            )
        }
    _layer_tokens.pop(snap.get("layer_id", ""), None)
    _apply_aids_snapshot(QgsProject.instance(), snap)
    return {
        "restored": True,
        "state_token": token,
        "topological": QgsProject.instance().topologicalEditing(),
        "avoid_mode": int(QgsProject.instance().avoidIntersectionsMode()),
        "snapping_enabled": QgsProject.instance().snappingConfig().enabled(),
    }





_ARM_ACTIONS = {
    "vertex": ("actionVertexToolActiveLayer", "mActionVertexToolActiveLayer"),
    "reshape": (None, "mActionReshapeFeatures"),
    "split": ("actionSplitFeatures", "mActionSplitFeatures"),
    "delete_ring": ("actionDeleteRing", "mActionDeleteRing"),
    "delete_part": ("actionDeletePart", "mActionDeletePart"),
    "add_ring": ("actionAddRing", "mActionAddRing"),
}


def _find_mainwindow_action(object_name: str):

    if iface is None or not object_name:
        return None
    try:
        try:
            from qgis.PyQt.QtGui import QAction
        except ImportError:
            from qgis.PyQt.QtWidgets import QAction
        for act in iface.mainWindow().findChildren(QAction):
            if act.objectName() == object_name:
                return act
    except Exception:
        return None
    return None


def _arm_tool(args: dict) -> dict:
    name = args["name"]
    if name not in _ARM_ACTIONS:
        return {
            "_error": (
                f"Unknown tool '{name}'. Valid: {sorted(_ARM_ACTIONS.keys())}"
            )
        }
    if iface is None:
        return {"_error": "No QGIS GUI (iface unavailable); cannot arm a map tool."}

    getter, object_name = _ARM_ACTIONS[name]
    action = None
    source = None
    if getter is not None:
        try:
            action = getattr(iface, getter)()
            source = f"iface.{getter}()"
        except Exception:
            action = None
    if action is None:
        action = _find_mainwindow_action(object_name)
        source = f"mainWindow action '{object_name}'"
    if action is None:
        return {"_error": f"Could not resolve a QGIS action to arm '{name}'."}
    if not action.isEnabled():
        return tool_error(f"The '{name}' action is disabled right now.",
                          hint="edit_action_disabled", action=name)
    action.trigger()

    return {
        "armed": name,
        "action": source,
        "active_tool": _active_tool_class(),
    }


def _move_vertex(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err

    fid = args["fid"]
    vertex_index = args["vertex_index"]
    feat = layer.getFeature(fid)
    if not feat.isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}' (fid, not an attribute id)."}




    src_geom = feat.geometry()
    orig = None if src_geom.isNull() else src_geom.vertexAt(vertex_index)

    x, y = float(args["x"]), float(args["y"])
    proj = QgsProject.instance()
    ok = layer.moveVertex(x, y, fid, vertex_index)
    if not ok:
        return {
            "_error": (
                f"moveVertex failed for fid {fid}, vertex {vertex_index}: the index "
                "may be out of range for this geometry."
            )
        }




    co_moved = 0
    if orig is not None and proj.topologicalEditing():
        ox, oy = orig.x(), orig.y()




        request = QgsFeatureRequest().setFilterRect(QgsRectangle(ox - 1e-9, oy - 1e-9, ox + 1e-9, oy + 1e-9))
        for other in layer.getFeatures(request):
            if other.id() == fid:
                continue
            g = other.geometry()
            if g.isNull():
                continue
            for j, v in enumerate(g.vertices()):
                if abs(v.x() - ox) < 1e-9 and abs(v.y() - oy) < 1e-9:
                    if layer.moveVertex(x, y, other.id(), j):
                        co_moved += 1

    layer.triggerRepaint()
    return {
        "moved": True,
        "layer": layer.name(),
        "fid": fid,
        "vertex_index": vertex_index,
        "x": x,
        "y": y,
        "coincident_vertices_moved": co_moved,
        "committed": False,
        "note": coded_fact(hint="edit_uncommitted", change="vertex moved"),
        **_validity(layer, fid),
    }


def _trim_extend_line(args: dict) -> dict:






    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    feat = layer.getFeature(fid)
    if not feat.isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}
    geom = feat.geometry()
    if geom.isNull() or geom.isEmpty() or geom.isMultipart():
        return {"_error": "trim/extend requires a single-part line feature."}
    points = geom.asPolyline()
    if len(points) < 2:
        return {"_error": "trim/extend requires a line with at least two vertices."}

    x, y = float(args["x"]), float(args["y"])
    target_crs = args.get("target_crs")
    if target_crs:
        source = QgsCoordinateReferenceSystem(str(target_crs))
        if not source.isValid():
            return {"_error": f"Invalid target CRS: {target_crs}"}
        if source != layer.crs():
            try:
                point = QgsCoordinateTransform(source, layer.crs(), QgsProject.instance()).transform(
                    QgsPointXY(x, y)
                )
                x, y = point.x(), point.y()
            except Exception as exc:  # noqa: BLE001
                return {"_error": f"Could not transform target to layer CRS: {exc}"}

    first, last = points[0], points[-1]
    d_first = (first.x() - x) ** 2 + (first.y() - y) ** 2
    d_last = (last.x() - x) ** 2 + (last.y() - y) ** 2
    vertex_index = 0 if d_first <= d_last else len(points) - 1
    old = points[vertex_index]
    other = points[-1] if vertex_index == 0 else points[0]
    if abs(old.x() - x) < 1e-15 and abs(old.y() - y) < 1e-15:
        return {"trimmed_or_extended": False, "changed": False, "layer": layer.name(),
                "fid": fid, "vertex_index": vertex_index,
                "note": "The selected endpoint is already at the target."}
    if abs(other.x() - x) < 1e-15 and abs(other.y() - y) < 1e-15:
        return {"_error": "Target would collapse the line to zero length."}

    moved = layer.moveVertex(x, y, fid, vertex_index)
    if not moved:
        return {"_error": f"Could not move endpoint {vertex_index} of feature {fid}."}
    layer.triggerRepaint()
    return {
        "trimmed_or_extended": True,
        "changed": True,
        "operation": "trim_or_extend",
        "layer": layer.name(),
        "fid": fid,
        "vertex_index": vertex_index,
        "target": {"x": x, "y": y, "crs": layer.crs().authid() or layer.crs().toWkt()},
        "old_endpoint": {"x": old.x(), "y": old.y()},
        "topological_editing": QgsProject.instance().topologicalEditing(),
        "committed": False,
        "note": coded_fact(hint="edit_uncommitted", change="endpoint moved"),
        **_validity(layer, fid),
    }


def _split_feature(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err

    fid = args["fid"]
    feat = layer.getFeature(fid)
    if not feat.isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}

    pts, err = _line_points_from_wkt(args["cut_line_wkt"])
    if err:
        return err

    topo = QgsProject.instance().topologicalEditing()



    prev_sel = list(layer.selectedFeatureIds())
    layer.selectByIds([fid])

    buf = layer.editBuffer()
    added_before = len(buf.addedFeatures()) if buf is not None else 0

    curve = QgsLineString([QgsPoint(p.x(), p.y()) for p in pts])
    result = None
    try:
        try:
            result = layer.splitFeatures(curve, False, topo)
        except TypeError:
            try:
                result = layer.splitFeatures(curve, topo)
            except TypeError:
                result = layer.splitFeatures(pts, topo)
    finally:
        layer.selectByIds(prev_sel)

    buf = layer.editBuffer()
    added_after = len(buf.addedFeatures()) if buf is not None else 0
    new_pieces = added_after - added_before

    layer.triggerRepaint()


    if isinstance(result, tuple):
        result = result[0] if result else None
    code = int(result) if result is not None else -1
    success = code == 0
    out = {
        "split": success,
        "layer": layer.name(),
        "fid": fid,
        "result_code": code,
        "new_pieces": new_pieces,
        "committed": False,
        "note": coded_fact(hint="edit_uncommitted", change="split"),
    }
    if not success:
        out["_error"] = f"splitFeatures returned code {code} (0=Success)."

        out.update(coded_fact(hint="digitize_split", result_code=code, layer=layer.name(), fid=fid))
    else:
        out.update(_validity(layer, fid))
    return out


def _reshape_feature(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err

    fid = args["fid"]
    feat = layer.getFeature(fid)
    if not feat.isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}

    pts, err = _line_points_from_wkt(args["reshape_line_wkt"])
    if err:
        return err

    geom = QgsGeometry(feat.geometry())
    if geom.isNull():
        return {"_error": f"Feature {fid} has no geometry to reshape."}

    curve = QgsLineString([QgsPoint(p.x(), p.y()) for p in pts])
    result = geom.reshapeGeometry(curve)
    code = int(result)
    if code != 0:
        return {
            "reshaped": False,
            "layer": layer.name(),
            "fid": fid,
            "result_code": code,
            "_error": f"reshapeGeometry returned code {code} (0=Success).",

            **coded_fact(hint="digitize_reshape", result_code=code, layer=layer.name(), fid=fid),
        }

    if not layer.changeGeometry(fid, geom):
        return {"_error": f"changeGeometry failed for fid {fid}."}

    layer.triggerRepaint()
    return {
        "reshaped": True,
        "layer": layer.name(),
        "fid": fid,
        "result_code": code,
        "committed": False,
        "note": coded_fact(hint="edit_uncommitted", change="reshaped"),
        **_validity(layer, fid),
    }


def _edit_state(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err

    proj = QgsProject.instance()
    cfg = proj.snappingConfig()
    token = _layer_tokens.get(layer.id())
    return {
        "layer": layer.name(),
        "layer_id": layer.id(),
        "editable": layer.isEditable(),
        "dirty": layer.isModified(),


        "feature_count": feature_count_of(layer),
        "active_tool": _active_tool_class(),
        "snapping_enabled": cfg.enabled(),
        "topological": proj.topologicalEditing(),
        "avoid_mode": int(proj.avoidIntersectionsMode()),
        "tracked_state_token": token,
    }


def _ring_points_from_wkt(wkt: str):

    geom = QgsGeometry.fromWkt(wkt)
    if geom.isNull():
        return None, {"_error": f"Invalid WKT: {wkt[:100]}"}
    poly = geom.asPolygon()
    if poly:
        pts = poly[0]
    else:
        pts = geom.asPolyline()
    if len(pts) < 3:
        return None, {"_error": "A ring/part needs at least 3 distinct vertices."}
    if pts[0] != pts[-1]:
        pts = list(pts) + [pts[0]]
    return pts, None


def _add_vertex(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    if not layer.getFeature(fid).isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}
    before = args["before_vertex"]
    ok = QgsVectorLayerEditUtils(layer).insertVertex(
        float(args["x"]), float(args["y"]), fid, before
    )
    if not ok:
        return {"_error": f"insertVertex failed for fid {fid} before vertex {before}."}
    layer.triggerRepaint()
    return {"inserted": True, "layer": layer.name(), "fid": fid,
            "before_vertex": before, "committed": False, **_validity(layer, fid)}


def _delete_vertex(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    if not layer.getFeature(fid).isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}
    result = layer.deleteVertex(fid, args["vertex_index"])
    code = int(result)
    if code != 0:
        return {"deleted": False, "layer": layer.name(), "fid": fid, "result_code": code,
                "_error": f"deleteVertex returned code {code} (0=Success).",
                **coded_fact(hint="digitize_deletevertex", result_code=code, layer=layer.name(), fid=fid)}
    layer.triggerRepaint()
    return {"deleted": True, "layer": layer.name(), "fid": fid,
            "vertex_index": args["vertex_index"], "committed": False, **_validity(layer, fid)}


def _translate_feature(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    if not layer.getFeature(fid).isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}
    result = layer.translateFeature(fid, float(args["dx"]), float(args["dy"]))
    code = int(result)
    if code != 0:
        return {"translated": False, "layer": layer.name(), "fid": fid, "result_code": code,
                "_error": f"translateFeature returned code {code} (0=Success)."}
    layer.triggerRepaint()
    return {"translated": True, "layer": layer.name(), "fid": fid,
            "dx": float(args["dx"]), "dy": float(args["dy"]), "committed": False}


def _rotate_feature(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    feat = layer.getFeature(fid)
    if not feat.isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}
    geom = QgsGeometry(feat.geometry())
    if geom.isNull():
        return {"_error": f"Feature {fid} has no geometry to rotate."}
    cx, cy = args.get("center_x"), args.get("center_y")
    if cx is None or cy is None:
        c = geom.centroid().asPoint()
        center = QgsPointXY(c.x(), c.y())
    else:
        center = QgsPointXY(float(cx), float(cy))
    result = geom.rotate(float(args["angle_degrees"]), center)
    code = int(result)
    if code != 0:
        return {"rotated": False, "layer": layer.name(), "fid": fid, "result_code": code,
                "_error": f"rotate returned code {code} (0=Success).",
                **coded_fact(hint="digitize_rotate", result_code=code, layer=layer.name(), fid=fid)}
    if not layer.changeGeometry(fid, geom):
        return {"_error": f"changeGeometry failed for fid {fid}."}
    layer.triggerRepaint()
    return {"rotated": True, "layer": layer.name(), "fid": fid,
            "angle_degrees": float(args["angle_degrees"]),
            "center": [center.x(), center.y()], "committed": False, **_validity(layer, fid)}


def _simplify_feature(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    feat = layer.getFeature(fid)
    if not feat.isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}
    geom = feat.geometry()
    if geom.isNull():
        return {"_error": f"Feature {fid} has no geometry to simplify."}
    simplified = geom.simplify(float(args["tolerance"]))
    if simplified.isNull():
        return tool_error("simplify produced a null geometry.", hint="edit_simplify_null", fid=fid,
                          tolerance=float(args["tolerance"]))
    vbefore = sum(1 for _ in geom.vertices())
    vafter = sum(1 for _ in simplified.vertices())
    if not layer.changeGeometry(fid, simplified):
        return {"_error": f"changeGeometry failed for fid {fid}."}
    layer.triggerRepaint()
    return {"simplified": True, "layer": layer.name(), "fid": fid,
            "tolerance": float(args["tolerance"]),
            "vertices_before": vbefore, "vertices_after": vafter, "committed": False,
            **_validity(layer, fid)}


def _merge_features(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fids = args["fids"]
    if len(fids) < 2:
        return {"_error": "fids needs at least 2 to merge."}
    geoms = []
    for fid in fids:
        f = layer.getFeature(fid)
        if not f.isValid():
            return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}
        g = f.geometry()
        if g.isNull():
            return {"_error": f"Feature {fid} has no geometry."}
        geoms.append(g)
    merged = QgsGeometry.unaryUnion(geoms)
    if merged.isNull() or merged.isEmpty():
        return {"_error": "Union of the features produced an empty geometry."}
    keep = fids[0]
    drop = fids[1:]
    if not layer.changeGeometry(keep, merged):
        return {"_error": f"changeGeometry failed for the kept fid {keep}."}
    if not layer.deleteFeatures(drop):
        return {"_error": f"Failed to delete merged-away fids {drop}."}
    layer.triggerRepaint()
    return {"merged": True, "layer": layer.name(), "kept_fid": keep,
            "deleted_fids": drop, "committed": False,
            "note": "kept the first fid's attributes; others removed.", **_validity(layer, keep)}


def _add_ring(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    pts, err = _ring_points_from_wkt(args["ring_wkt"])
    if err:
        return err
    result = layer.addRing(pts)
    if isinstance(result, tuple):
        code = int(result[0])
        ring_fid = result[1] if len(result) > 1 else None
    else:
        code = int(result)
        ring_fid = None
    if code != 0:
        return {"ring_added": False, "layer": layer.name(), "result_code": code,
                "_error": f"addRing returned code {code} (0=Success).",
                **coded_fact(hint="digitize_addring", result_code=code, layer=layer.name())}
    layer.triggerRepaint()
    return {"ring_added": True, "layer": layer.name(), "feature_fid": ring_fid,
            "committed": False, **_validity(layer, ring_fid)}


def _add_part(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    if not layer.getFeature(fid).isValid():
        return {"_error": f"No feature with fid {fid} in layer '{layer.name()}'."}
    part = QgsGeometry.fromWkt(args["part_wkt"])
    if part.isNull():
        return {"_error": f"Invalid WKT: {args['part_wkt'][:100]}"}
    geometry = QgsGeometry(layer.getFeature(fid).geometry())
    result = geometry.addPartGeometry(part)
    code = int(result)
    if code != 0:
        return {"part_added": False, "layer": layer.name(), "fid": fid, "result_code": code,
                "_error": f"addPartGeometry returned code {code} (0=Success).",
                **coded_fact(hint="digitize_addpart", result_code=code, layer=layer.name(), fid=fid)}
    if not layer.changeGeometry(fid, geometry):
        return {"_error": f"changeGeometry failed for fid {fid}."}
    layer.triggerRepaint()
    return {"part_added": True, "layer": layer.name(), "fid": fid, "committed": False,
            **_validity(layer, fid)}


def _undo_saved(layer) -> dict:


    from .code_runtime import current

    context = current()
    if context.restore_previous is None or not context.chat:
        return {"undone": False, "layer": layer.name(),
                "_error": f"Layer '{layer.name()}' has no edit waiting to be undone.",
                **coded_fact(hint="edit_nothing_to_undo", layer=layer.name())}
    return context.restore_previous(context.chat)


def _undo(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    stack = layer.undoStack()
    if not layer.isEditable() or not stack.canUndo():
        return _undo_saved(layer)
    label = stack.undoText()
    stack.undo()
    layer.triggerRepaint()
    return {"undone": True, "layer": layer.name(), "command": label,
            "can_undo": stack.canUndo(), "can_redo": stack.canRedo()}


def _redo(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    stack = layer.undoStack()
    if not stack.canRedo():
        return {"redone": False, "layer": layer.name(),
                "_error": "Nothing to redo in this edit session."}
    label = stack.redoText()
    stack.redo()
    layer.triggerRepaint()
    return {"redone": True, "layer": layer.name(), "command": label,
            "can_undo": stack.canUndo(), "can_redo": stack.canRedo()}






def register_edit_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="qgis_set_snapping_config",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "tolerance": {"type": "number", "minimum": 0, "maximum": 1000000},
                "units": {"type": "string",
                          "enum": ["pixels", "map_units", "layer_units", "millimeters", "points", "inches"]},
                "mode": {"type": "string", "enum": ["active", "all", "advanced"]},
                "types": {"type": "string", "enum": ["vertex", "segment", "both"]},
                "topological_editing": {"type": "boolean"},
                "intersection_snapping": {"type": "boolean"},
                "layer_overrides": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "layer_name": {"type": "string"},
                            "enabled": {"type": "boolean"},
                            "tolerance": {"type": "number", "minimum": 0, "maximum": 1000000},
                            "units": {"type": "string", "enum": ["pixels", "map_units", "layer_units",
                                                                "millimeters", "points", "inches"]},
                            "types": {"type": "string", "enum": ["vertex", "segment", "both"]},
                        },
                        "required": ["layer_name"],
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
        handler=_set_snapping_config,
    ))

    registry.register(Tool(
        name="qgis_edit_begin",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "snap_tolerance_px": {"type": "number", "minimum": 0, "maximum": 1000},
                "topological": {"type": "boolean"},
                "avoid_overlap": {"type": "boolean"},
            },
            "required": ["layer_name"],
        },
        handler=_edit_begin,
    ))

    registry.register(Tool(
        name="qgis_edit_commit",
        danger="write",
        input_schema={
            "type": "object",
            "x-preserve-failed-commit": True,
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_edit_commit,
    ))

    registry.register(Tool(
        name="qgis_edit_rollback",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_edit_rollback,
    ))

    registry.register(Tool(
        name="qgis_edit_restore_aids",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "state_token": {"type": "string"},
            },
            "required": ["state_token"],
        },
        handler=_edit_restore_aids,
    ))

    registry.register(Tool(
        name="qgis_arm_tool",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "enum": ["vertex", "reshape", "split", "delete_ring", "delete_part", "add_ring"],
                },
            },
            "required": ["name"],
        },
        handler=_arm_tool,
    ))

    registry.register(Tool(
        name="qgis_move_vertex",
        danger="write",
        xy_crs=(NAMED_LAYER,),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "vertex_index": {"type": "integer", "minimum": 0},
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            "required": ["layer_name", "fid", "vertex_index", "x", "y"],
        },
        handler=_move_vertex,
    ))

    registry.register(Tool(
        name="qgis_trim_extend_line",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Trim or extend a line endpoint"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "x": {"type": "number"},
                "y": {"type": "number"},
                "target_crs": {"type": "string"},
            },
            "required": ["layer_name", "fid", "x", "y"],
            "additionalProperties": False,
        },
        handler=_trim_extend_line,
    ))

    registry.register(Tool(
        name="qgis_split_feature",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "cut_line_wkt": {
                    "type": "string",
                },
            },
            "required": ["layer_name", "fid", "cut_line_wkt"],
        },
        handler=_split_feature,
    ))

    registry.register(Tool(
        name="qgis_reshape_feature",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "reshape_line_wkt": {
                    "type": "string",
                },
            },
            "required": ["layer_name", "fid", "reshape_line_wkt"],
        },
        handler=_reshape_feature,
    ))

    registry.register(Tool(
        name="qgis_edit_state",
        danger="read",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_edit_state,
    ))

    registry.register(Tool(
        name="qgis_add_vertex",
        danger="write",
        xy_crs=(NAMED_LAYER,),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "before_vertex": {
                    "type": "integer",
                    "minimum": 0,
                },
                "x": {"type": "number"},
                "y": {"type": "number"},
            },
            "required": ["layer_name", "fid", "before_vertex", "x", "y"],
        },
        handler=_add_vertex,
    ))

    registry.register(Tool(
        name="qgis_delete_vertex",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "vertex_index": {"type": "integer", "minimum": 0},
            },
            "required": ["layer_name", "fid", "vertex_index"],
        },
        handler=_delete_vertex,
    ))

    registry.register(Tool(
        name="qgis_translate_feature",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "dx": {"type": "number"},
                "dy": {"type": "number"},
            },
            "required": ["layer_name", "fid", "dx", "dy"],
        },
        handler=_translate_feature,
    ))

    registry.register(Tool(
        name="qgis_rotate_feature",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "angle_degrees": {"type": "number"},
                "center_x": {"type": "number"},
                "center_y": {"type": "number"},
            },
            "required": ["layer_name", "fid", "angle_degrees"],
        },
        handler=_rotate_feature,
    ))

    registry.register(Tool(
        name="qgis_simplify_feature",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "tolerance": {"type": "number", "minimum": 0},
            },
            "required": ["layer_name", "fid", "tolerance"],
        },
        handler=_simplify_feature,
    ))

    registry.register(Tool(
        name="qgis_merge_features",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fids": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 5000},
            },
            "required": ["layer_name", "fids"],
        },
        handler=_merge_features,
    ))

    registry.register(Tool(
        name="qgis_add_ring",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "ring_wkt": {
                    "type": "string",
                },
            },
            "required": ["layer_name", "ring_wkt"],
        },
        handler=_add_ring,
    ))

    registry.register(Tool(
        name="qgis_add_part",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fid": {"type": "integer"},
                "part_wkt": {"type": "string"},
            },
            "required": ["layer_name", "fid", "part_wkt"],
        },
        handler=_add_part,
    ))

    registry.register(Tool(
        name="qgis_undo",
        danger="write",
        input_schema={
            "type": "object",


            "x-undo-saved": True,
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_undo,
    ))

    registry.register(Tool(
        name="qgis_redo",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_redo,
    ))

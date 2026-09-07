# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""QGIS digitizing / vectorization tools for the AI Agent MCP."""


















from __future__ import annotations

import uuid

from qgis.core import (
    Qgis,
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

try:
    from qgis.utils import iface
except Exception:  # pragma: no cover - headless safety
    iface = None

from ..core.policy import ToolPolicyGroup
from ..core.tool_registry import Tool, ToolRegistry


_edit_sessions: dict[str, dict] = {}

_layer_tokens: dict[str, str] = {}






def _resolve_vector(name_or_id: str):
    """Resolve a vector layer by id first, then by unique name."""




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
                    f"Pass one of these ids instead: {ids}"
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


def _require_editable(layer: QgsVectorLayer):
    """Return an error dict if the layer has no open edit session, else None."""
    if not layer.isEditable():
        return {
            "_error": (
                f"Layer '{layer.name()}' has no open edit session. "
                "Call qgis_edit_begin first."
            )
        }
    return None


def _snapshot_aids(proj: QgsProject) -> dict:
    """Snapshot the project's current editing aids so they can be restored exactly."""
    return {
        "snap": proj.snappingConfig(),
        "topo": proj.topologicalEditing(),
        "avoid_mode": proj.avoidIntersectionsMode(),
        "avoid_layer_ids": [lyr.id() for lyr in proj.avoidIntersectionsLayers()],
    }


def _apply_aids_snapshot(proj: QgsProject, snap: dict):
    """Restore a prior editing-aid snapshot onto the project."""
    proj.setTopologicalEditing(snap["topo"])
    proj.setAvoidIntersectionsMode(snap["avoid_mode"])
    restored_layers = [
        lyr for lyr in (proj.mapLayer(lid) for lid in snap["avoid_layer_ids"]) if lyr is not None
    ]
    proj.setAvoidIntersectionsLayers(restored_layers)
    proj.setSnappingConfig(snap["snap"])


def _reveal_digitize_toolbars():
    """Best-effort reveal of the digitizing toolbars (GUI only)."""
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
        except Exception:  # nosec B110 - interface update is optional
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
    """Parse a LineString WKT into a list of QgsPointXY (>=2), or (None, error)."""
    geom = QgsGeometry.fromWkt(wkt)
    if geom.isNull():
        return None, {"_error": f"Invalid WKT line: {wkt[:100]}"}
    pts = geom.asPolyline()
    if len(pts) < 2:
        return None, {
            "_error": (
                "Line needs at least 2 vertices. Provide a LINESTRING WKT in the "
                "layer's CRS, e.g. 'LINESTRING(x1 y1, x2 y2)'."
            )
        }
    return pts, None






def _edit_begin(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err

    proj = QgsProject.instance()







    existing_token = _layer_tokens.get(layer.id())
    reused_session = existing_token is not None and existing_token in _edit_sessions
    prior = _edit_sessions[existing_token] if reused_session else _snapshot_aids(proj)

    if not layer.isEditable():
        if not layer.startEditing():
            return {"_error": f"Cannot start editing on layer '{layer.name()}'"}

    topological = bool(args.get("topological", True))
    avoid_overlap = bool(args.get("avoid_overlap", True))
    tol = args.get("snap_tolerance_px", 12)

    cfg = proj.snappingConfig()
    cfg.setEnabled(True)
    cfg.setMode(QgsSnappingConfig.SnappingMode.AllLayers)
    cfg.setTypeFlag(
        QgsSnappingConfig.SnappingTypes.VertexFlag
        | QgsSnappingConfig.SnappingTypes.SegmentFlag
    )
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
        except Exception:  # nosec B110 - interface update is optional
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
        "note": "prior editing aids saved; pass state_token to qgis_edit_restore_aids to restore.",
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
        errors = layer.commitErrors()
        return {"_error": "; ".join(errors) if errors else "commitChanges failed"}

    return {
        "committed": True,
        "layer": layer.name(),
        "changes": pending,
        "feature_count": layer.featureCount(),
        "editing": layer.isEditable(),
        "note": (
            "editing aids still applied; call qgis_edit_restore_aids with the "
            "state_token to leave the project clean."
        ),
    }


def _edit_rollback(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err

    if not layer.rollBack():
        return {"_error": f"rollBack failed on layer '{layer.name()}'"}

    aids_restored = False
    token = _layer_tokens.pop(layer.id(), None)
    if token is not None:
        snap = _edit_sessions.pop(token, None)
        if snap is not None:
            _apply_aids_snapshot(QgsProject.instance(), snap)
            aids_restored = True

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
    """Find a QAction on the QGIS main window by objectName (real toolbar action)."""
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
                f"Unknown tool '{name}'. Choose one of: {sorted(_ARM_ACTIONS.keys())}"
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
        return {
            "_error": (
                f"The '{name}' action is disabled right now. Open an edit session "
                "(qgis_edit_begin) on an active editable vector layer first."
            )
        }
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
                f"moveVertex failed for fid {fid}, vertex {vertex_index}. "
                "Check the vertex index is in range for this geometry."
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
        "note": "moved in the open edit session (call qgis_edit_commit to persist).",
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
        "note": "split in the open edit session (call qgis_edit_commit to persist).",
    }
    if not success:
        out["_error"] = (
            f"splitFeatures returned code {code} (0=Success). "
            "The cut line may not cross the feature."
        )
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
            "_error": (
                f"reshapeGeometry returned code {code} (0=Success). "
                "The reshape line must start and end on the feature boundary."
            ),
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
        "note": "reshaped in the open edit session (call qgis_edit_commit to persist).",
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
        "feature_count": layer.featureCount(),
        "active_tool": _active_tool_class(),
        "snapping_enabled": cfg.enabled(),
        "topological": proj.topologicalEditing(),
        "avoid_mode": int(proj.avoidIntersectionsMode()),
        "tracked_state_token": token,
    }


def _ring_points_from_wkt(wkt: str):
    """Parse a POLYGON or LINESTRING WKT into a closed ring of QgsPointXY (>=4)."""
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
            "before_vertex": before, "committed": False}


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
                "_error": f"deleteVertex returned code {code} (0=Success)."}
    layer.triggerRepaint()
    return {"deleted": True, "layer": layer.name(), "fid": fid,
            "vertex_index": args["vertex_index"], "committed": False}


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
                "_error": f"rotate returned code {code} (0=Success)."}
    if not layer.changeGeometry(fid, geom):
        return {"_error": f"changeGeometry failed for fid {fid}."}
    layer.triggerRepaint()
    return {"rotated": True, "layer": layer.name(), "fid": fid,
            "angle_degrees": float(args["angle_degrees"]),
            "center": [center.x(), center.y()], "committed": False}


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
        return {"_error": "simplify produced a null geometry; lower the tolerance."}
    vbefore = sum(1 for _ in geom.vertices())
    vafter = sum(1 for _ in simplified.vertices())
    if not layer.changeGeometry(fid, simplified):
        return {"_error": f"changeGeometry failed for fid {fid}."}
    layer.triggerRepaint()
    return {"simplified": True, "layer": layer.name(), "fid": fid,
            "tolerance": float(args["tolerance"]),
            "vertices_before": vbefore, "vertices_after": vafter, "committed": False}


def _merge_features(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fids = args["fids"]
    if len(fids) < 2:
        return {"_error": "Provide at least 2 fids to merge."}
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
            "note": "kept the first fid's attributes; others removed."}


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
                "_error": (f"addRing returned code {code} (0=Success). The ring must lie "
                           "entirely inside exactly one existing feature.")}
    layer.triggerRepaint()
    return {"ring_added": True, "layer": layer.name(), "feature_fid": ring_fid,
            "committed": False}


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
    pts, err = _ring_points_from_wkt(args["part_wkt"])
    if err:
        return err

    prev_sel = list(layer.selectedFeatureIds())
    layer.selectByIds([fid])
    result = layer.addPart(pts)
    layer.selectByIds(prev_sel)
    if isinstance(result, tuple):
        result = result[0]
    code = int(result)
    if code != 0:
        return {"part_added": False, "layer": layer.name(), "fid": fid, "result_code": code,
                "_error": f"addPart returned code {code} (0=Success)."}
    layer.triggerRepaint()
    return {"part_added": True, "layer": layer.name(), "fid": fid, "committed": False}


def _undo(args: dict) -> dict:
    layer, err = _resolve_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    stack = layer.undoStack()
    if not stack.canUndo():
        return {"undone": False, "layer": layer.name(),
                "_error": "Nothing to undo in this edit session."}
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
        name="qgis_edit_begin",
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
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_edit_commit",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_edit_commit,
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_edit_rollback",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_edit_rollback,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_edit_restore_aids",
        input_schema={
            "type": "object",
            "properties": {
                "state_token": {"type": "string"},
            },
            "required": ["state_token"],
        },
        handler=_edit_restore_aids,
        policy_group=ToolPolicyGroup.PROJECT_WRITE,
    ))

    registry.register(Tool(
        name="qgis_arm_tool",
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
        policy_group=ToolPolicyGroup.PROJECT_WRITE,
    ))

    registry.register(Tool(
        name="qgis_move_vertex",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_split_feature",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_reshape_feature",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_edit_state",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_edit_state,
        policy_group=ToolPolicyGroup.READ,
    ))

    registry.register(Tool(
        name="qgis_add_vertex",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_delete_vertex",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_translate_feature",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_rotate_feature",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_simplify_feature",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_merge_features",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fids": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 5000},
            },
            "required": ["layer_name", "fids"],
        },
        handler=_merge_features,
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_add_ring",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_add_part",
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
        destructive=True,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_undo",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_undo,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

    registry.register(Tool(
        name="qgis_redo",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
            },
            "required": ["layer_name"],
        },
        handler=_redo,
        policy_group=ToolPolicyGroup.DATA_MUTATION,
    ))

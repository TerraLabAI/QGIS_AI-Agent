# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import uuid

from qgis.core import (
    Qgis,
    QgsGeometry,
    QgsProject,
    QgsSnappingConfig,
    QgsTolerance,
    QgsVectorLayer,
)

try:
    from qgis.utils import iface
except Exception:  # pragma: no cover
    iface = None

from ..core.layer_order import feature_count_of
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, coded_fact, tool_error
from . import vector_write
from .layer_lookup import require_vector


_edit_sessions: dict[str, dict] = {}

_layer_tokens: dict[str, str] = {}


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


def _require_editable(layer: QgsVectorLayer, writes: bool = True):





    kmz = vector_write.kmz_in_place_error(layer, "edit") if writes else None
    if kmz is not None:
        return kmz
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


def _individual_layer_settings(args: dict):

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
        return tool_error("tolerance must be zero or greater.", "INVALID_ARGS")
    try:
        mode = _snapping_mode(mode_name)
        units = _snapping_units(units_name)
    except ValueError as exc:
        return tool_error(str(exc), "INVALID_ARGS")
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
                hint="snapping_intersection_unsupported",
            )
        proj.setSnappingConfig(cfg)
    except (AttributeError, TypeError, ValueError) as exc:
        return tool_error(f"QGIS rejected the snapping configuration: {exc}", "EXECUTION_FAILED")

    applied, unsupported = [], []
    for override in args.get("layer_overrides", []):
        layer, err = require_vector(override["layer_name"])
        if err:
            return err
        settings = _individual_layer_settings(override)
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
    layer, err = require_vector(args["layer_name"])
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
    layer, err = require_vector(args["layer_name"])
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
        return tool_error(
            f"QGIS could not finish saving the edits to {layer.name()!r}: "
            + ("; ".join(errors) or "the provider gave no detail")
            + ". Uncommitted edits remain in the open edit session. "
              "Some provider stages may already have been saved.",
            "EXECUTION_FAILED",
            hint="edit_commit_incomplete",
            layer=layer.name(),
            editing=layer.isEditable(),
            rolled_back=False,
            provider_errors=errors[:8],
            remaining={
                "added": len(remaining.addedFeatures()),
                "changed_geometries": len(remaining.changedGeometries()),
                "changed_attributes": len(remaining.changedAttributeValues()),
                "deleted": len(remaining.deletedFeatureIds()),
            } if remaining is not None else {},
        )

    return {
        "committed": True,
        "layer": layer.name(),
        "changes": pending,



        "feature_count": feature_count_of(layer),
        "editing": layer.isEditable(),
        "note": coded_fact(hint="edit_aids_still_applied", state_token=_layer_tokens.get(layer.id())),
    }


def _edit_rollback(args: dict) -> dict:
    layer, err = require_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer, writes=False)
    if err:
        return err

    left_edit_mode = vector_write.force_out_of_edit(layer)

    aids_restored = _release_aids(layer)
    if not left_edit_mode:


        return tool_error(
            f"rollBack failed on layer '{layer.name()}', which is still in edit mode.",
            "EXECUTION_FAILED",
            hint="edit_rollback_failed",
            layer=layer.name(),
            aids_restored=aids_restored,
        )

    return {
        "rolled_back": True,
        "layer": layer.name(),
        "editing": layer.isEditable(),
        "aids_restored": aids_restored,
    }


def _edit_restore_aids(args: dict) -> dict:
    token = args.get("state_token")
    if not token:
        return tool_error("state_token is required (returned by qgis_edit_begin).", "INVALID_ARGS")
    snap = _edit_sessions.pop(token, None)
    if snap is None:
        active = list(_edit_sessions.keys())
        return tool_error(
            f"Unknown state_token '{token}'. Active tokens: {active or 'none'}.",
            "INVALID_ARGS",
        )
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
        return tool_error(f"Unknown tool '{name}'. Valid: {sorted(_ARM_ACTIONS.keys())}", "INVALID_ARGS")
    if iface is None:
        return tool_error("No QGIS GUI (iface unavailable); cannot arm a map tool.", "EXECUTION_FAILED")

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
        return tool_error(f"Could not resolve a QGIS action to arm '{name}'.", "EXECUTION_FAILED")
    if not action.isEnabled():
        return tool_error(f"The '{name}' action is disabled right now.",
                          hint="edit_action_disabled", action=name)
    action.trigger()

    return {
        "armed": name,
        "action": source,
        "active_tool": _active_tool_class(),
    }


def _edit_state(args: dict) -> dict:
    layer, err = require_vector(args["layer_name"])
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


def _translate_feature(args: dict) -> dict:
    layer, err = require_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    if not layer.getFeature(fid).isValid():
        return tool_error(f"No feature with fid {fid} in layer '{layer.name()}'.", "INVALID_ARGS")
    result = layer.translateFeature(fid, float(args["dx"]), float(args["dy"]))
    code = int(result)
    if code != 0:
        return {"translated": False, "layer": layer.name(), "fid": fid, "result_code": code,
                **tool_error(f"translateFeature returned code {code} (0=Success).", "EXECUTION_FAILED")}
    layer.triggerRepaint()
    return {"translated": True, "layer": layer.name(), "fid": fid,
            "dx": float(args["dx"]), "dy": float(args["dy"]), "committed": False}


def _merge_features(args: dict) -> dict:
    layer, err = require_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fids = args["fids"]
    if len(fids) < 2:
        return tool_error("fids needs at least 2 to merge.", "INVALID_ARGS")
    geoms = []
    for fid in fids:
        f = layer.getFeature(fid)
        if not f.isValid():
            return tool_error(f"No feature with fid {fid} in layer '{layer.name()}'.", "INVALID_ARGS")
        g = f.geometry()
        if g.isNull():
            return tool_error(f"Feature {fid} has no geometry.", "INVALID_ARGS")
        geoms.append(g)
    merged = QgsGeometry.unaryUnion(geoms)
    if merged.isNull() or merged.isEmpty():
        return tool_error("Union of the features produced an empty geometry.", "EXECUTION_FAILED")
    keep = fids[0]
    drop = fids[1:]
    if not layer.changeGeometry(keep, merged):
        return tool_error(f"changeGeometry failed for the kept fid {keep}.", "EXECUTION_FAILED")
    if not layer.deleteFeatures(drop):
        return tool_error(f"Failed to delete merged-away fids {drop}.", "EXECUTION_FAILED")
    layer.triggerRepaint()
    return {"merged": True, "layer": layer.name(), "kept_fid": keep,
            "deleted_fids": drop, "committed": False,
            "note": "kept the first fid's attributes; others removed.", **_validity(layer, keep)}


def _add_part(args: dict) -> dict:
    layer, err = require_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    fid = args["fid"]
    if not layer.getFeature(fid).isValid():
        return tool_error(f"No feature with fid {fid} in layer '{layer.name()}'.", "INVALID_ARGS")
    part = QgsGeometry.fromWkt(args["part_wkt"])
    if part.isNull():
        return tool_error(f"Invalid WKT: {args['part_wkt'][:100]}", "INVALID_ARGS")
    geometry = QgsGeometry(layer.getFeature(fid).geometry())
    result = geometry.addPartGeometry(part)
    code = int(result)
    if code != 0:
        return tool_error(f"addPartGeometry returned code {code} (0=Success).", "EXECUTION_FAILED",
                          hint="digitize_addpart", part_added=False, result_code=code,
                          layer=layer.name(), fid=fid)
    if not layer.changeGeometry(fid, geometry):
        return tool_error(f"changeGeometry failed for fid {fid}.", "EXECUTION_FAILED")
    layer.triggerRepaint()
    return {"part_added": True, "layer": layer.name(), "fid": fid, "committed": False,
            **_validity(layer, fid)}


def _undo_saved(layer) -> dict:


    from .code_runtime import current

    context = current()
    if context.restore_previous is None or not context.chat:
        return tool_error(f"Layer '{layer.name()}' has no edit waiting to be undone.", "EXECUTION_FAILED",
                          hint="edit_nothing_to_undo", undone=False, layer=layer.name())
    return context.restore_previous(context.chat)


def _undo(args: dict) -> dict:
    layer, err = require_vector(args["layer_name"])
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
    layer, err = require_vector(args["layer_name"])
    if err:
        return err
    err = _require_editable(layer)
    if err:
        return err
    stack = layer.undoStack()
    if not stack.canRedo():
        return {"redone": False, "layer": layer.name(),
                **tool_error("Nothing to redo in this edit session.", "EXECUTION_FAILED")}
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

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


















from __future__ import annotations

import re

from qgis.core import (
    QgsAttributeEditorRelation,
    QgsEditFormConfig,
    QgsEditorWidgetSetup,
    QgsExpression,
    QgsProject,
    QgsRelation,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

try:
    from qgis.core import Qgis
except ImportError:
    Qgis = None

from ..core import limits
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._layers import layer_not_found, resolve_layer
from .layer_lookup import _field_not_found_error

_ACTIONS = ("add", "list", "remove")
_STRENGTHS = ("association", "composition")

_LIST_CAP = 50

_ORPHAN_SAMPLE = 10


def register_relation_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="configure_relations",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Project relations[: {child_layer} to {parent_layer}]"),
        input_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_ACTIONS)},
                "name": {"type": "string"},
                "parent_layer": {"type": "string"},
                "parent_field": {"type": "string"},
                "child_layer": {"type": "string"},
                "child_field": {"type": "string"},
                "link_layer": {"type": "string"},
                "link_parent_field": {"type": "string"},
                "link_child_field": {"type": "string"},
                "strength": {"type": "string", "enum": list(_STRENGTHS)},
                "child_widget": {"type": "boolean"},
                "relation_id": {"type": "string"},
            },
            "required": ["action"],
        },
        handler=_configure_relations,
    ))


def _configure_relations(args: dict) -> dict:
    action = str(args.get("action") or "").strip().lower()
    if action not in _ACTIONS:
        return tool_error(f"Unknown action {action!r}.", "INVALID_ARGS", "add, list, remove.")
    if action == "list":
        return _list_relations(args)
    if action == "remove":
        return _remove_relation(args)
    return _add_relation(args)





def _manager():
    return QgsProject.instance().relationManager()


def _strength_value(strength: str):
    member = "Composition" if strength == "composition" else "Association"
    if Qgis is not None and hasattr(Qgis, "RelationshipStrength"):
        return getattr(Qgis.RelationshipStrength, member)
    scoped = getattr(QgsRelation, "RelationStrength", None)
    if scoped is not None and hasattr(scoped, member):
        return getattr(scoped, member)
    return getattr(QgsRelation, member)


def _strength_name(relation) -> str:
    try:
        return "composition" if relation.strength() == _strength_value("composition") else "association"
    except Exception:  # noqa: BLE001
        return "association"


def _pairs(relation) -> list:

    pairs = relation.fieldPairs()
    items = pairs.items() if hasattr(pairs, "items") else pairs
    return [(str(child), str(parent)) for child, parent in items]


def _nm_partner(relation) -> str:

    parent = relation.referencedLayer()
    if parent is None:
        return ""
    config = parent.editFormConfig()
    partner = str((config.widgetConfig(relation.id()) or {}).get("nm-rel") or "")
    if partner:
        return partner
    for element in _relation_elements(config.invisibleRootContainer()):
        if element.relation().id() == relation.id():
            nm_id = element.nmRelationId()
            if nm_id:
                return str(nm_id)
    return ""


def _describe(relation) -> dict:
    pairs = _pairs(relation)
    child = relation.referencingLayer()
    parent = relation.referencedLayer()
    out = {
        "id": relation.id(),
        "name": relation.name(),
        "parent_layer": parent.name() if parent else relation.referencedLayerId(),
        "parent_field": ", ".join(p for _c, p in pairs),
        "child_layer": child.name() if child else relation.referencingLayerId(),
        "child_field": ", ".join(c for c, _p in pairs),
        "strength": _strength_name(relation),
        "valid": relation.isValid(),
    }
    partner = _nm_partner(relation)
    if partner:
        out["nm_partner"] = partner
    return out


def _relation_elements(container) -> list:
    found = []
    for child in container.children():
        if isinstance(child, QgsAttributeEditorRelation):
            found.append(child)
        elif hasattr(child, "children"):
            found.extend(_relation_elements(child))
    return found


def _list_relations(args: dict) -> dict:
    layer_filter = None
    for key in ("parent_layer", "child_layer"):
        if args.get(key):
            layer = resolve_layer(args[key])
            if layer is None:
                return layer_not_found(args[key])
            layer_filter = layer.id()
    relations = list(_manager().relations().values())
    if layer_filter:
        relations = [r for r in relations
                     if layer_filter in (r.referencingLayerId(), r.referencedLayerId())]
    rows = [_describe(r) for r in sorted(relations, key=lambda r: r.name().casefold())]
    out = {"relation_count": len(rows), "relations": rows[:_LIST_CAP]}
    if len(rows) > _LIST_CAP:
        out["omitted"] = len(rows) - _LIST_CAP
    return out





def _vector(name, role: str):
    if not str(name or "").strip():
        return None, tool_error(f"{role} is required.", "INVALID_ARGS",
                                "It takes a layer name or id (list_layers).")
    layer = resolve_layer(name)
    if layer is None:
        error = layer_not_found(name)
        error["_error"] = f"{role}: {error.get('_error', '')}"
        return None, error
    if not isinstance(layer, QgsVectorLayer):
        return None, tool_error(f"{role} {layer.name()!r} is not a vector layer.", "INVALID_ARGS",
                                "Relations link vector layers or tables.")
    return layer, None


def _field(layer, name, role: str):
    name = str(name or "").strip()
    if not name:
        return None, tool_error(f"{role} is required.", "INVALID_ARGS",
                                f"Fields of {layer.name()!r}: "
                                + ", ".join(f.name() for f in layer.fields())[:600])
    index = layer.fields().indexOf(name)
    if index < 0:
        return None, _field_not_found_error(layer, name)
    return layer.fields().at(index), None


def _kind(field) -> str:
    if field.isNumeric():
        return "number"
    type_name = str(field.typeName() or "").lower()
    if any(word in type_name for word in ("string", "text", "char")):
        return "text"
    return type_name or "other"


def _type_mismatch(parent_layer, parent_field, child_layer, child_field):
    kinds = {_kind(parent_field), _kind(child_field)}
    if kinds == {"number", "text"}:
        return tool_error(
            f"{parent_layer.name()}.{parent_field.name()} is {_kind(parent_field)} and "
            f"{child_layer.name()}.{child_field.name()} is {_kind(child_field)}: no child would ever match "
            "its parent. Nothing was added.",
            "INVALID_ARGS",
            "field_calculator into a new field with to_int() or to_string() gives the two keys one type; "
            "the relation then holds on that field.")
    return None


def _relation_id(child_layer, child_field: str, parent_layer, parent_field: str) -> str:
    base = f"{child_layer.name()}_{child_field}_{parent_layer.name()}_{parent_field}"
    base = re.sub(r"[^0-9A-Za-z_]+", "_", base).strip("_")[:80] or "relation"
    existing = _manager().relations()
    candidate, number = base, 2
    while candidate in existing:
        candidate, number = f"{base}_{number}", number + 1
    return candidate


def _same_relation(parent_layer, parent_field: str, child_layer, child_field: str):
    for relation in _manager().relations().values():
        if (relation.referencedLayerId() == parent_layer.id()
                and relation.referencingLayerId() == child_layer.id()
                and _pairs(relation) == [(child_field, parent_field)]):
            return relation
    return None


def _key_checks(parent_layer, parent_field, child_layer, child_field) -> dict:

    ceiling = limits.current("MAX_FEATURES_MATERIALISED")
    counts = [parent_layer.featureCount(), child_layer.featureCount()]
    if any(not isinstance(c, int) or c < 0 or c > ceiling for c in counts):
        return {"key_check": f"skipped: a layer is past {ceiling:,} features or cannot count them"}
    parent_index = parent_layer.fields().indexOf(parent_field.name())
    child_index = child_layer.fields().indexOf(child_field.name())
    parent_keys = {v for v in parent_layer.uniqueValues(parent_index) if not _is_null(v)}
    child_keys = {v for v in child_layer.uniqueValues(child_index) if not _is_null(v)}
    out = {}
    parent_rows = _non_null_count(parent_layer, parent_field.name())
    if parent_rows is not None and parent_rows > len(parent_keys):
        out["parent_key_repeated"] = (
            f"{parent_rows - len(parent_keys):,} parent rows repeat a key already used: a child with that "
            "key belongs to several parents. A relation needs a unique parent key.")
    orphans = sorted((k for k in child_keys if k not in parent_keys), key=str)
    out["child_keys"] = len(child_keys)
    out["orphan_keys"] = len(orphans)
    if orphans:
        out["orphan_sample"] = [str(k) for k in orphans[:_ORPHAN_SAMPLE]]
    return out


def _is_null(value) -> bool:
    if value is None:
        return True
    is_null = getattr(value, "isNull", None)
    if callable(is_null):
        try:
            return bool(is_null())
        except TypeError:
            return False
    return False


def _non_null_count(layer, field_name: str):
    from qgis.core import QgsAggregateCalculator

    aggregate = getattr(getattr(Qgis, "Aggregate", None), "Count", None) if Qgis is not None else None
    if aggregate is None:
        aggregate = getattr(QgsAggregateCalculator, "Count", None)
    if aggregate is None:
        return None
    try:
        value, ok = layer.aggregate(aggregate, QgsExpression.quotedColumnRef(field_name))
    except Exception:  # noqa: BLE001
        return None
    return int(value) if ok and value is not None else None


def _build(name: str, parent_layer, parent_field: str, child_layer, child_field: str, strength: str):
    relation = QgsRelation()
    relation.setId(_relation_id(child_layer, child_field, parent_layer, parent_field))
    relation.setName(name)
    relation.setReferencedLayer(parent_layer.id())
    relation.setReferencingLayer(child_layer.id())
    relation.addFieldPair(child_field, parent_field)
    relation.setStrength(_strength_value(strength))
    return relation


def _show_on_form(parent_layer, relation, nm_partner=None) -> None:






    config = parent_layer.editFormConfig()
    if nm_partner is not None:
        widget_config = dict(config.widgetConfig(relation.id()) or {})
        widget_config["nm-rel"] = nm_partner.id()
        config.setWidgetConfig(relation.id(), widget_config)
    if _is_drag_and_drop(config):
        root = config.invisibleRootContainer()
        elements = [e for e in _relation_elements(root) if e.relation().id() == relation.id()]
        if not elements:
            element = QgsAttributeEditorRelation(relation, root)
            root.addChildElement(element)
            elements = [element]
        if nm_partner is not None:
            for element in elements:
                element.setNmRelationId(nm_partner.id())
    parent_layer.setEditFormConfig(config)


def _is_drag_and_drop(config) -> bool:
    layout = config.layout()
    if Qgis is not None and hasattr(Qgis, "AttributeFormLayout"):
        return layout == Qgis.AttributeFormLayout.DragAndDrop

    return layout == getattr(QgsEditFormConfig, "TabLayout", None)


def _reference_widget(child_layer, field_name: str, relation) -> None:
    index = child_layer.fields().indexOf(field_name)
    child_layer.setEditorWidgetSetup(index, QgsEditorWidgetSetup("RelationReference", {
        "Relation": relation.id(),
        "AllowNULL": True,
        "ShowForm": False,
        "ShowOpenFormButton": True,
        "MapIdentification": False,
        "ReadOnly": False,
        "OrderByValue": True,
        "AllowAddFeatures": False,
    }))


def _add_relation(args: dict) -> dict:
    strength = str(args.get("strength") or "association").strip().lower()
    if strength not in _STRENGTHS:
        return tool_error(f"Unknown strength {strength!r}.", "INVALID_ARGS",
                          "association keeps children when a parent is deleted; composition deletes them "
                          "with it (and copies them when the parent is duplicated).")
    parent_layer, error = _vector(args.get("parent_layer"), "parent_layer")
    if error:
        return error
    child_layer, error = _vector(args.get("child_layer"), "child_layer")
    if error:
        return error
    parent_field, error = _field(parent_layer, args.get("parent_field"), "parent_field")
    if error:
        return error
    child_field, error = _field(child_layer, args.get("child_field"), "child_field")
    if error:
        return error
    link_name = str(args.get("link_layer") or "").strip()
    child_widget = args.get("child_widget", True) is not False
    name = str(args.get("name") or "").strip()

    if not link_name:
        if parent_layer.id() == child_layer.id() and parent_field.name() == child_field.name():
            return tool_error("A relation needs two different keys.", "INVALID_ARGS",
                              "A layer may reference itself (a parent_id field), through another field.")
        bad = _type_mismatch(parent_layer, parent_field, child_layer, child_field)
        if bad:
            return bad
        existing = _same_relation(parent_layer, parent_field.name(), child_layer, child_field.name())
        if existing is not None:
            out = {"added": False, "note": "This relation is already in the project; nothing was added.",
                   "relation": _describe(existing)}
            if _strength_name(existing) != strength:
                out["strength_differs"] = (
                    f"Its strength is {_strength_name(existing)}, not {strength}: remove it, then add it "
                    f"again with strength {strength} to change that.")
            return out
        relation = _build(name or f"{child_layer.name()} of {parent_layer.name()}", parent_layer,
                          parent_field.name(), child_layer, child_field.name(), strength)
        if not relation.isValid():
            return tool_error(f"QGIS refused the relation: {_invalid_reason(relation)}", "EXECUTION_FAILED",
                              "Both layers must be loaded; both fields must exist.")
        checks = _key_checks(parent_layer, parent_field, child_layer, child_field)
        _manager().addRelation(relation)
        _show_on_form(parent_layer, relation)
        if child_widget:
            _reference_widget(child_layer, child_field.name(), relation)
        QgsProject.instance().setDirty(True)
        return {"added": True, "cardinality": "1:n", "relation": _describe(relation), **checks,
                "child_widget": "RelationReference" if child_widget else "unchanged",
                "count_expression": (f"relation_aggregate('{relation.id()}', 'count', "
                                     f"{QgsExpression.quotedColumnRef(child_field.name())})"),
                "note": "Saved with the project: save_project keeps it."}


    link_layer, error = _vector(link_name, "link_layer")
    if error:
        return error
    link_parent, error = _field(link_layer, args.get("link_parent_field"), "link_parent_field")
    if error:
        return error
    link_child, error = _field(link_layer, args.get("link_child_field"), "link_child_field")
    if error:
        return error
    if link_parent.name() == link_child.name():
        return tool_error("link_parent_field and link_child_field must be two different fields.",
                          "INVALID_ARGS", "The link table holds one key for each side.")
    for bad in (_type_mismatch(parent_layer, parent_field, link_layer, link_parent),
                _type_mismatch(child_layer, child_field, link_layer, link_child)):
        if bad:
            return bad
    base = name or f"{parent_layer.name()} and {child_layer.name()}"
    first = (_same_relation(parent_layer, parent_field.name(), link_layer, link_parent.name())
             or _build(f"{base} ({parent_layer.name()})", parent_layer, parent_field.name(),
                       link_layer, link_parent.name(), strength))
    second = (_same_relation(child_layer, child_field.name(), link_layer, link_child.name())
              or _build(f"{base} ({child_layer.name()})", child_layer, child_field.name(),
                        link_layer, link_child.name(), strength))
    for relation in (first, second):
        if not relation.isValid():
            return tool_error(f"QGIS refused the relation: {_invalid_reason(relation)}", "EXECUTION_FAILED",
                              "The three layers must be loaded; the fields must exist.")
    parent_checks = _key_checks(parent_layer, parent_field, link_layer, link_parent)
    child_checks = _key_checks(child_layer, child_field, link_layer, link_child)
    added = []
    for relation in (first, second):
        if relation.id() not in _manager().relations():
            _manager().addRelation(relation)
            added.append(relation.id())
    _show_on_form(parent_layer, first, nm_partner=second)
    _show_on_form(child_layer, second, nm_partner=first)
    if child_widget:
        _reference_widget(link_layer, link_parent.name(), first)
        _reference_widget(link_layer, link_child.name(), second)
    QgsProject.instance().setDirty(True)
    return {
        "added": bool(added),
        "cardinality": "n:m",
        "relations": [_describe(first), _describe(second)],
        "parent_side": parent_checks,
        "child_side": child_checks,
        "note": ("Two 1:n relations onto the link table, paired so each side's form lists the other side. "
                 "Saved with the project: save_project keeps it."),
    }


def _invalid_reason(relation) -> str:
    reason = getattr(relation, "validationError", None)
    if callable(reason):
        try:
            return str(reason() or "invalid")
        except Exception:  # noqa: BLE001
            return "invalid"
    return "invalid"





def _remove_relation(args: dict) -> dict:
    key = str(args.get("relation_id") or args.get("name") or "").strip()
    if not key:
        return tool_error("relation_id (or name) names the relation to remove.", "INVALID_ARGS",
                          "configure_relations with action list shows them.")
    relations = _manager().relations()
    relation = relations.get(key)
    if relation is None:
        named = [r for r in relations.values() if r.name().casefold() == key.casefold()]
        if len(named) > 1:
            return tool_error(f"{len(named)} relations are named {key!r}.", "INVALID_ARGS",
                              "relation_id: " + ", ".join(r.id() for r in named)[:400])
        relation = named[0] if named else None
    if relation is None:
        return tool_error(f"No relation {key!r} in this project.", "INVALID_ARGS",
                          "Relations: " + ", ".join(f"{r.id()} ({r.name()})" for r in relations.values())[:600])
    described = _describe(relation)
    child = relation.referencingLayer()
    reset = []
    if child is not None:
        for field in child.fields():
            index = child.fields().indexOf(field.name())
            setup = child.editorWidgetSetup(index)
            if setup.type() == "RelationReference" and str(setup.config().get("Relation") or "") == relation.id():
                child.setEditorWidgetSetup(index, QgsEditorWidgetSetup())
                reset.append(field.name())
    unpaired = _unpair(relation.id())
    _manager().removeRelation(relation.id())
    QgsProject.instance().setDirty(True)
    out = {"removed": described}
    if reset:
        out["widgets_reset"] = reset
    if unpaired:
        out["unpaired"] = unpaired
    return out


def _unpair(relation_id: str) -> list:

    unpaired = []
    for other in _manager().relations().values():
        if other.id() == relation_id:
            continue
        parent = other.referencedLayer()
        if parent is None:
            continue
        config = parent.editFormConfig()
        changed = False
        widget_config = dict(config.widgetConfig(other.id()) or {})
        if str(widget_config.get("nm-rel") or "") == relation_id:
            widget_config.pop("nm-rel")
            config.setWidgetConfig(other.id(), widget_config)
            changed = True
        for element in _relation_elements(config.invisibleRootContainer()):
            if element.relation().id() == other.id() and str(element.nmRelationId() or "") == relation_id:
                element.setNmRelationId("")
                changed = True
        if changed:
            parent.setEditFormConfig(config)
            unpaired.append(other.id())
    return unpaired

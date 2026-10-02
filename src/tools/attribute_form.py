# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

from qgis.core import (
    QgsAttributeEditorContainer,
    QgsAttributeEditorField,
    QgsAttributeEditorRelation,
    QgsConditionalStyle,
    QgsDefaultValue,
    QgsEditFormConfig,
    QgsEditorWidgetSetup,
    QgsExpression,
    QgsFieldConstraints,
    QgsMarkerSymbol,
    QgsOptionalExpression,
    QgsProject,
    QgsValueRelationFieldFormatter,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

try:
    from qgis.core import Qgis
except ImportError:
    Qgis = None

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._compat import enum_value
from ._layers import layer_not_found, resolve_layer
from .colour_text import qcolor_from_text, qgis_colour_text
from .layer_lookup import _field_not_found_error
from .style_tools import _color_error, _expression_error

_LAYOUTS = {"auto", "drag_and_drop", "ui_file"}


_STRENGTHS = {
    "hard": enum_value((QgsFieldConstraints, "ConstraintStrength.ConstraintStrengthHard"),
                       (QgsFieldConstraints, "ConstraintStrengthHard")),
    "soft": enum_value((QgsFieldConstraints, "ConstraintStrength.ConstraintStrengthSoft"),
                       (QgsFieldConstraints, "ConstraintStrengthSoft")),
}
_CONSTRAINTS = {
    "not_null": enum_value((QgsFieldConstraints, "Constraint.ConstraintNotNull"),
                           (QgsFieldConstraints, "ConstraintNotNull")),
    "unique": enum_value((QgsFieldConstraints, "Constraint.ConstraintUnique"),
                         (QgsFieldConstraints, "ConstraintUnique")),
}

_CONTAINER_KINDS = {
    "tab": enum_value((Qgis, "AttributeEditorContainerType.Tab")),
    "group": enum_value((Qgis, "AttributeEditorContainerType.GroupBox")),
    "row": enum_value((Qgis, "AttributeEditorContainerType.Row")),
}
_MAX_CONTAINERS_SHOWN = 40
_MAX_FIELDS_SHOWN = 60


def register_attribute_form_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="configure_attribute_form",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Configure the attribute form of {layer_name}"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "alias": {"type": "string"},
                            "widget_type": {"type": "string"},
                            "widget_config": {"type": "object"},
                            "default_expression": {"type": "string"},
                            "apply_default_on_update": {"type": "boolean"},
                            "constraints": {"type": "array", "items": {"type": "string"}},
                            "constraint_expression": {"type": "string"},
                            "constraint_strength": {"type": "string", "enum": ["hard", "soft"]},
                        },
                        "required": ["name"],
                    },
                },
                "form": {
                    "type": "object",
                    "properties": {
                        "layout": {"type": "string", "enum": sorted(_LAYOUTS)},
                        "ui_file": {"type": "string"},
                        "label_on_top": {"type": "boolean"},
                        "suppress": {"type": "boolean"},
                        "containers": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "fields": {"type": "array", "items": {"type": "string"}},
                                    "parent": {"type": "string"},
                                    "show_as": {"type": "string", "enum": ["tab", "group"]},
                                    "visibility_expression": {"type": "string"},
                                    "columns": {"type": "integer"},
                                    "collapsed": {"type": "boolean"},
                                },
                                "required": ["name"],
                            },
                        },
                        "remove_containers": {"type": "array", "items": {"type": "string"}},
                    },
                },
                "conditional_styles": {
                    "type": "object",
                    "properties": {
                        "list": {"type": "boolean"},
                        "clear": {"type": "boolean"},
                        "rules": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "field": {
                                        "type": "string",
                                    },
                                    "name": {"type": "string"},
                                    "rule": {"type": "string"},
                                    "background": {"type": "string"},
                                    "text_color": {"type": "string"},
                                    "bold": {"type": "boolean"},
                                    "italic": {"type": "boolean"},
                                    "icon_color": {"type": "string"},
                                },
                                "required": ["rule"],
                            },
                        },
                    },
                },
            },
            "required": ["layer_name"],
        },
        handler=_configure_attribute_form,
    ))


def _configure_attribute_form(args: dict) -> dict:
    layer = resolve_layer(args.get("layer_name"))
    if layer is None:
        return layer_not_found(args.get("layer_name"))
    if not isinstance(layer, QgsVectorLayer):
        return tool_error("Only vector layers have attribute forms.", "INVALID_ARGS",
                          "layer must be a vector layer.")
    entries = args.get("fields")
    form_args = args.get("form")
    conditional_args = args.get("conditional_styles")
    if entries is None and form_args is None and conditional_args is None:
        return tool_error("fields, form, conditional_styles unset.", "INVALID_ARGS",
                          "fields changes one field's alias/widget/default/constraints; "
                          "conditional_styles adds, lists or clears attribute-table highlight rules.")
    if entries is not None and (not isinstance(entries, list) or not entries):
        return tool_error("fields must be a non-empty list.", "INVALID_ARGS",
                          "fields holds one object per field: name plus settings to change.")


    fields = layer.fields()
    planned = []
    unknown = []
    for entry in (entries or []):
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            return tool_error("Each fields item needs a name.", "INVALID_ARGS",
                              "Keys: name, alias, widget_type, widget_config, default_expression.")
        name = str(entry["name"]).strip()
        index = fields.indexOf(name)
        if index < 0:
            unknown.append(name)
            continue
        widget = None
        if "widget_type" in entry or "widget_config" in entry:
            widget_type = str(entry.get("widget_type") or "TextEdit").strip()
            config = entry.get("widget_config", {})
            if not isinstance(config, dict):
                return tool_error(f"widget_config for {name!r} must be an object.", "INVALID_ARGS",
                                  "widget_config uses the native QGIS keys for the selected widget.")
            if widget_type.lower() == "valuerelation":
                widget_type = "ValueRelation"
                config, bad = _value_relation_config(layer, name, config)
                if bad:
                    return bad
            widget = (widget_type, config)
        constraints = entry.get("constraints")
        if constraints is not None and (
                not isinstance(constraints, list) or any(c not in _CONSTRAINTS for c in constraints)):
            return tool_error(f"Unknown constraints for {name!r}.", "INVALID_ARGS",
                              "Supported constraints are not_null and unique.")
        planned.append((index, name, entry, widget))

    if unknown and not planned:
        return tool_error(f"No such field: {', '.join(unknown)}.", "INVALID_ARGS",
                          f"Fields: {', '.join(f.name() for f in fields)[:600]}.")

    layout_enum = None
    tree = None
    source = None
    if form_args is not None:
        if not isinstance(form_args, dict):
            return tool_error("form must be an object.", "INVALID_ARGS",
                              "form takes layout, ui_file, label_on_top, suppress, containers or "
                              "remove_containers.")
        layout = str(form_args.get("layout") or "").lower()
        if form_args.get("containers") and layout not in ("", "drag_and_drop"):
            return tool_error(f"form.containers need the drag_and_drop layout, not {layout}.", "INVALID_ARGS",
                              "Passing containers switches the form to drag_and_drop on its own.")
        if form_args.get("containers"):
            layout = "drag_and_drop"
        if layout:
            layout_enum = _form_layout(layout)
            if layout_enum is None:
                return tool_error(f"Unsupported form layout: {layout}.", "INVALID_ARGS",
                                  "auto, drag_and_drop, ui_file work.")

        source = layer.editFormConfig()
        tree, bad = _plan_form_tree(layer, source, form_args)
        if bad:
            return bad

    applied = []
    for index, name, entry, widget in planned:
        result = {"name": name}
        if "alias" in entry:
            layer.setFieldAlias(index, str(entry.get("alias") or "").strip()[:100])
            result["alias"] = layer.attributeAlias(index)
        if widget is not None:
            layer.setEditorWidgetSetup(index, QgsEditorWidgetSetup(*widget))
            result["widget_type"] = layer.editorWidgetSetup(index).type()
            result["widget_config"] = layer.editorWidgetSetup(index).config()
        if "default_expression" in entry:
            expression = str(entry.get("default_expression") or "")
            layer.setDefaultValueDefinition(
                index, QgsDefaultValue(expression, bool(entry.get("apply_default_on_update", False)))
            )
            result["default_expression"] = layer.defaultValueDefinition(index).expression()
        constraints = entry.get("constraints")
        if constraints is not None:
            strength = _STRENGTHS.get(str(entry.get("constraint_strength") or "hard").lower())
            for constraint_name in constraints:
                layer.setFieldConstraint(index, _CONSTRAINTS[constraint_name], strength)
            result["constraints"] = list(constraints)
        if "constraint_expression" in entry:
            expression = str(entry.get("constraint_expression") or "")
            layer.setConstraintExpression(index, expression)
            result["constraint_expression"] = expression
        applied.append(result)

    form_applied = {}
    if form_args is not None:
        config = layer.editFormConfig()
        if layout_enum is not None:
            config.setLayout(layout_enum)
            form_applied["layout"] = str(form_args.get("layout") or "drag_and_drop").lower()
        if "ui_file" in form_args:
            config.setUiForm(str(form_args.get("ui_file") or ""))
            form_applied["ui_file"] = str(form_args.get("ui_file") or "")
        if "label_on_top" in form_args:
            for entry in applied:
                config.setLabelOnTop(fields.indexOf(entry["name"]), bool(form_args["label_on_top"]))
            form_applied["label_on_top"] = bool(form_args["label_on_top"])
        if "suppress" in form_args:
            suppress = bool(form_args["suppress"])
            if Qgis is not None and hasattr(Qgis, "AttributeFormSuppression"):
                config.setSuppress(Qgis.AttributeFormSuppression.On if suppress else Qgis.AttributeFormSuppression.Off)
            else:
                config.setSuppress(suppress)
            form_applied["suppress"] = bool(form_args["suppress"])
        if tree is not None:

            config.clearTabs()
            root = config.invisibleRootContainer()
            for node in tree["children"]:
                config.addTab(_build_element(node, root, fields))
        layer.setEditFormConfig(config)
        del source
        if tree is not None:
            form_applied["tree"] = _describe_form_tree(layer.editFormConfig())

    out = {"layer_id": layer.id(), "name": layer.name()}
    if entries is not None:
        out["fields"] = applied
        if unknown:
            out["unknown_fields"] = unknown
    if form_applied:
        out["form"] = form_applied
    if conditional_args is not None:
        conditional_out = _apply_conditional_styles(layer, conditional_args)
        if isinstance(conditional_out, dict) and conditional_out.get("_error"):
            return conditional_out
        out["conditional_styles"] = conditional_out
    return out













def _rule_error(layer, rule: dict):
    text = str(rule.get("rule") or "").strip()
    if not text:
        return tool_error("Each conditional_styles rule needs 'rule', a boolean expression.", "INVALID_ARGS",
                          'Example: "population" > 10000, or "status" IS NULL for a full-row rule.')
    bad = _expression_error(layer, text, "Conditional style rule")
    if bad:
        return bad
    for key in ("background", "text_color", "icon_color"):
        bad = _color_error(rule.get(key), key)
        if bad:
            return bad
    return None


def _conditional_style_from(rule: dict) -> QgsConditionalStyle:
    style = QgsConditionalStyle()
    style.setRule(str(rule.get("rule") or ""))
    name = str(rule.get("name") or "").strip()
    if name:
        style.setName(name)
    background = str(rule.get("background") or "").strip()
    if background:
        style.setBackgroundColor(qcolor_from_text(background))
    text_color = str(rule.get("text_color") or "").strip()
    if text_color:
        style.setTextColor(qcolor_from_text(text_color))
    if rule.get("bold") or rule.get("italic"):
        font = style.font()
        font.setBold(bool(rule.get("bold")))
        font.setItalic(bool(rule.get("italic")))
        style.setFont(font)
    icon_color = str(rule.get("icon_color") or "").strip()
    if icon_color:
        style.setSymbol(QgsMarkerSymbol.createSimple({"color": qgis_colour_text(icon_color), "size": "3"}))
    return style


def _describe_conditional_style(style: QgsConditionalStyle) -> dict:
    info = {"rule": style.rule()}
    if style.name():
        info["name"] = style.name()
    if style.validBackgroundColor():
        info["background"] = style.backgroundColor().name()
    if style.validTextColor():
        info["text_color"] = style.textColor().name()
    font = style.font()
    if font.bold():
        info["bold"] = True
    if font.italic():
        info["italic"] = True
    if style.symbol() is not None:
        info["icon"] = True
    return info


def _apply_conditional_styles(layer, conditional_args) -> dict:
    if not isinstance(conditional_args, dict):
        return tool_error("conditional_styles must be an object.", "INVALID_ARGS",
                          "Keys: rules, clear, list.")
    fields = layer.fields()
    styles = layer.conditionalStyles()

    if conditional_args.get("clear"):
        styles.setRowStyles([])
        for field in fields:
            styles.setFieldStyles(field.name(), [])

    rules = conditional_args.get("rules")
    if rules is not None:
        if not isinstance(rules, list) or not rules:
            return tool_error("conditional_styles.rules must be a non-empty list.", "INVALID_ARGS",
                              "Each rule is one object: {rule, field (omit for a whole row), "
                              "background, text_color, bold, italic, icon_color, name}.")
        cleared = bool(conditional_args.get("clear"))
        by_field: dict = {}
        row_rules = []
        for rule in rules:
            if not isinstance(rule, dict):
                return tool_error("Each conditional_styles rule must be an object.", "INVALID_ARGS",
                                  "Holds rule, field, background, text_color, bold, italic, icon_color.")
            bad = _rule_error(layer, rule)
            if bad:
                return bad
            field_name = str(rule.get("field") or "").strip()
            if field_name and fields.indexOf(field_name) < 0:
                return _field_not_found_error(layer, field_name)
            new_style = _conditional_style_from(rule)
            if field_name:
                by_field.setdefault(field_name, []).append(new_style)
            else:
                row_rules.append(new_style)
        if row_rules:
            base = [] if cleared else list(styles.rowStyles())
            styles.setRowStyles(base + row_rules)
        for field_name, field_rules in by_field.items():
            base = [] if cleared else list(styles.fieldStyles(field_name))
            styles.setFieldStyles(field_name, base + field_rules)

    return {
        "row_rules": [_describe_conditional_style(s) for s in styles.rowStyles()],
        "field_rules": {
            field.name(): [_describe_conditional_style(s) for s in styles.fieldStyles(field.name())]
            for field in fields if styles.fieldStyles(field.name())
        },
    }













def _value_relation_config(layer, name: str, config: dict):

    config = dict(config)
    wanted = str(config.get("Layer") or config.get("LayerName") or "").strip()
    if not wanted:
        return None, tool_error(
            f"ValueRelation on {name!r} needs widget_config.Layer, the layer that holds the choices.",
            "INVALID_ARGS", "Needs Layer (name or id), Key (the field stored), Value (the field shown).")
    choices = resolve_layer(wanted)
    if choices is None:
        return None, layer_not_found(wanted)
    if not isinstance(choices, QgsVectorLayer):
        return None, tool_error(f"ValueRelation choices must come from a vector layer, not {choices.name()!r}.",
                                "INVALID_ARGS", "widget_config.Layer must be a vector or table layer.")
    config.update({"Layer": choices.id(), "LayerName": choices.name(),
                   "LayerSource": choices.publicSource(), "LayerProviderName": choices.providerType()})
    for key in ("Key", "Value"):
        field_name = str(config.get(key) or "").strip()
        if not field_name:
            return None, tool_error(
                f"ValueRelation on {name!r} needs widget_config.{key}.", "INVALID_ARGS",
                f"Key is the field of {choices.name()!r} stored in {name!r}; Value is the field shown. "
                f"Fields: {', '.join(f.name() for f in choices.fields())[:400]}.")
        if choices.fields().indexOf(field_name) < 0:
            return None, _field_not_found_error(choices, field_name)
        config[key] = field_name
    text = str(config.get("FilterExpression") or "").strip()
    if text:
        bad = _expression_error(choices, text, f"FilterExpression of {name!r}")
        if bad:
            return None, bad

        for form_field in sorted(QgsValueRelationFieldFormatter.expressionFormAttributes(text)):
            if layer.fields().indexOf(form_field) < 0:
                return None, _field_not_found_error(layer, form_field)
    return config, None













def _read_tree(root) -> dict:
    top = {"kind": "root", "name": None, "el": None, "parent": None, "children": []}

    def node(element, parent):
        if isinstance(element, QgsAttributeEditorContainer):
            item = {"kind": "container", "name": element.name(), "el": element, "parent": parent}
            item["children"] = [node(child, item) for child in element.children()]
            return item
        kind = "field" if isinstance(element, QgsAttributeEditorField) else "other"
        return {"kind": kind, "name": element.name(), "el": element, "parent": parent}

    top["children"] = [node(child, top) for child in root.children()]
    return top


def _is_drag_and_drop(config) -> bool:
    layout = config.layout()
    if Qgis is not None and hasattr(Qgis, "AttributeFormLayout"):
        return layout == Qgis.AttributeFormLayout.DragAndDrop
    return layout == enum_value((QgsEditFormConfig, "EditorLayout.TabLayout"),
                                (QgsEditFormConfig, "TabLayout"))


def _keep_relation_editors(layer, source, tree) -> None:





    placed = {n["el"].relation().id() for n in _nodes(tree)
              if isinstance(n["el"], QgsAttributeEditorRelation)}
    for relation in QgsProject.instance().relationManager().referencedRelations(layer):
        if relation.id() in placed:
            continue
        element = QgsAttributeEditorRelation(relation, None)
        partner = str((source.widgetConfig(relation.id()) or {}).get("nm-rel") or "")
        if partner:
            element.setNmRelationId(partner)
        _attach(tree, {"kind": "other", "name": relation.name(), "el": element})


def _nodes(node):
    for child in node["children"]:
        yield child
        if child["kind"] == "container":
            yield from _nodes(child)


def _attach(parent, node, position=None):
    node["parent"] = parent
    if position is None:
        parent["children"].append(node)
    else:
        parent["children"].insert(position, node)


def _detach(node):
    if node["parent"] is not None:
        node["parent"]["children"] = [c for c in node["parent"]["children"] if c is not node]
        node["parent"] = None


def _take_field(tree, name: str):

    found = [n for n in _nodes(tree) if n["kind"] == "field" and n["name"] == name]
    for item in found:
        _detach(item)
    return found[0]["el"] if found else None


def _container_error(message: str, index: dict) -> dict:
    names = ", ".join(sorted(index))[:400] or "none yet"
    return tool_error(message, "INVALID_ARGS", f"Containers on this form: {names}.")


def _plan_form_tree(layer, source, form_args: dict):


    specs = form_args.get("containers")
    removals = form_args.get("remove_containers")
    if not specs and not removals:
        return None, None
    if specs and (not isinstance(specs, list) or not all(isinstance(s, dict) for s in specs)):
        return None, tool_error("form.containers must be a list of objects.", "INVALID_ARGS",
                                "Items: name, fields, parent, show_as, visibility_expression.")
    if removals and not isinstance(removals, list):
        return None, tool_error("form.remove_containers must be a list of container names.", "INVALID_ARGS",
                                "Names the tabs or groups to remove.")
    specs = specs or []
    removals = [str(r).strip() for r in removals or [] if str(r).strip()]
    fields = layer.fields()
    tree = _read_tree(source.invisibleRootContainer())
    if not tree["children"]:
        for field in fields:
            _attach(tree, {"kind": "field", "name": field.name(), "el": None})
    if not _is_drag_and_drop(source):
        _keep_relation_editors(layer, source, tree)
    index = {}
    for item in _nodes(tree):
        if item["kind"] == "container":
            index.setdefault(item["name"], item)

    names = [str(spec.get("name") or "").strip() for spec in specs]
    if not all(names):
        return None, tool_error("Each form.containers item needs a name.", "INVALID_ARGS",
                                "The name is the tab or group title shown in the form.")
    if len(set(names)) != len(names):
        return None, tool_error("form.containers names a container twice.", "INVALID_ARGS",
                                "Each container appears once, with all its fields.")
    for name in removals:
        if name not in index:
            return None, _container_error(f"No container named {name!r} to remove.", index)
        if name in names:
            return None, tool_error(f"{name!r} is both in containers and remove_containers.", "INVALID_ARGS",
                                    "Not both: update or remove the container.")
    owner = {}
    parent_of = {name: (item["parent"]["name"] if item["parent"]["kind"] == "container" else None)
                 for name, item in index.items()}
    for spec, name in zip(specs, names):
        listed = spec.get("fields") or []
        if not isinstance(listed, list):
            return None, tool_error(f"fields of container {name!r} must be a list of field names.",
                                    "INVALID_ARGS", "fields orders them as they should appear.")
        for field_name in listed:
            field_name = str(field_name).strip()
            if fields.indexOf(field_name) < 0:
                return None, _field_not_found_error(layer, field_name)
            if owner.setdefault(field_name, name) != name:
                return None, tool_error(f"Field {field_name!r} is listed in {owner[field_name]!r} and {name!r}.",
                                        "INVALID_ARGS", "A field goes in one container.")
        parent = str(spec.get("parent") or "").strip()
        if parent:
            if parent not in index and parent not in names:
                return None, _container_error(f"No container named {parent!r} to put {name!r} in.", index)
            if parent in removals:
                return None, tool_error(f"{parent!r} is being removed, so {name!r} cannot go in it.",
                                        "INVALID_ARGS", "A removed container parents none.")
            parent_of[name] = parent
        elif name not in index:
            parent_of[name] = None
        show_as = spec.get("show_as")
        if show_as not in (None, "", "tab", "group"):
            return None, tool_error(f"show_as of {name!r} must be tab or group.", "INVALID_ARGS",
                                    "tab is a page of the form, group a framed box inside a page.")
        text = str(spec.get("visibility_expression") or "").strip()
        if text:
            bad = _expression_error(layer, text, f"visibility_expression of {name!r}")
            if bad:
                return None, bad
        columns = spec.get("columns")
        if columns is not None and (isinstance(columns, bool) or not isinstance(columns, int)
                                    or not 1 <= columns <= 20):
            return None, tool_error(f"columns of {name!r} must be a whole number from 1 to 20.",
                                    "INVALID_ARGS", "Most forms use 1 or 2 columns.")
    for name in names:
        seen = {name}
        step = parent_of.get(name)
        while step is not None:
            if step in seen:
                return None, tool_error(f"Container {step!r} would end up inside itself.",
                                        "INVALID_ARGS", "A container cannot sit inside one of its own groups.")
            seen.add(step)
            step = parent_of.get(step)

    wanted = {}
    for spec, name in zip(specs, names):
        item = index.get(name)
        if item is None:
            item = {"kind": "container", "name": name, "el": None, "parent": None, "children": []}
            index[name] = item
        item["spec"] = spec
        wanted[name] = item


    for spec, name in zip(specs, names):
        item = wanted[name]
        parent = str(spec.get("parent") or "").strip()
        target = index[parent] if parent else (tree if item["parent"] is None else item["parent"])
        if item["parent"] is not target:
            _detach(item)
            _attach(target, item)
        for field_name in spec.get("fields") or []:
            field_name = str(field_name).strip()
            element = _take_field(tree, field_name)
            _attach(wanted[name], {"kind": "field", "name": field_name, "el": element})
    returned = []
    for name in removals:
        item = index[name]
        parent = item["parent"]
        position = parent["children"].index(item)
        _detach(item)
        for child in item["children"]:
            if child["kind"] == "field":
                returned.append(child)
            else:
                _attach(parent, child, position)
                position += 1
    order = {field.name(): i for i, field in enumerate(fields)}
    for child in sorted(returned, key=lambda c: order.get(c["name"], len(order))):
        _attach(tree, child)
    return tree, None


def _set_show_as(element, show_as: str):
    kind = _CONTAINER_KINDS.get(show_as)
    if kind is not None:
        element.setType(kind)
    else:
        element.setIsGroupBox(show_as == "group")


def _show_as(element) -> str:
    if _CONTAINER_KINDS["group"] is None:
        return "group" if element.isGroupBox() else "tab"
    kind = element.type()
    return next((name for name, value in _CONTAINER_KINDS.items() if value == kind), "tab")


def _build_element(node: dict, parent, fields):

    if node["kind"] != "container":
        if node["el"] is not None:
            return node["el"].clone(parent)
        return QgsAttributeEditorField(node["name"], fields.indexOf(node["name"]), parent)
    if node["el"] is not None:
        element = node["el"].clone(parent)
        element.clear()
    else:
        element = QgsAttributeEditorContainer(node["name"], parent)
    spec = node.get("spec")
    if spec is not None:
        at_root = node["parent"]["kind"] == "root"
        show_as = str(spec.get("show_as") or "").lower()
        if not show_as and (node["el"] is None or spec.get("parent")):
            show_as = "tab" if at_root else "group"
        if show_as:
            _set_show_as(element, show_as)
        if "visibility_expression" in spec:
            text = str(spec.get("visibility_expression") or "").strip()
            element.setVisibilityExpression(
                QgsOptionalExpression(QgsExpression(text)) if text else QgsOptionalExpression())
        if spec.get("columns") is not None:
            element.setColumnCount(int(spec["columns"]))
        if "collapsed" in spec:
            element.setCollapsed(bool(spec["collapsed"]))
    for child in node["children"]:
        element.addChildElement(_build_element(child, element, fields))
    return element


def _describe_form_tree(config) -> dict:

    containers = []

    def walk(container, parent_name):
        for child in container.children():
            if not isinstance(child, QgsAttributeEditorContainer):
                continue
            info = {"name": child.name(), "show_as": _show_as(child)}
            if parent_name is not None:
                info["parent"] = parent_name
            names = [c.name() for c in child.children() if isinstance(c, QgsAttributeEditorField)]
            info["fields"] = names[:_MAX_FIELDS_SHOWN]
            other = sum(1 for c in child.children()
                        if not isinstance(c, (QgsAttributeEditorField, QgsAttributeEditorContainer)))
            if other:
                info["other_elements"] = other
            visibility = child.visibilityExpression()
            if visibility.enabled() and visibility.data().expression():
                info["visibility_expression"] = visibility.data().expression()
            containers.append(info)
            walk(child, child.name())

    root = config.invisibleRootContainer()
    walk(root, None)
    root_fields = [c.name() for c in root.children() if isinstance(c, QgsAttributeEditorField)]
    out = {"containers": containers[:_MAX_CONTAINERS_SHOWN], "root_fields": root_fields[:_MAX_FIELDS_SHOWN]}
    other = sum(1 for c in root.children()
                if not isinstance(c, (QgsAttributeEditorField, QgsAttributeEditorContainer)))
    if other:
        out["root_other_elements"] = other
    if len(containers) > _MAX_CONTAINERS_SHOWN:
        out["containers_total"] = len(containers)
    if len(root_fields) > _MAX_FIELDS_SHOWN:
        out["root_fields_total"] = len(root_fields)
    return out


def _form_layout(layout: str):
    if Qgis is not None and hasattr(Qgis, "AttributeFormLayout"):
        values = Qgis.AttributeFormLayout
        return {"auto": values.AutoGenerated, "drag_and_drop": values.DragAndDrop,
                "ui_file": values.UiFile}.get(layout)


    return {"auto": enum_value((QgsEditFormConfig, "EditorLayout.GeneratedLayout"),
                               (QgsEditFormConfig, "GeneratedLayout")),
            "drag_and_drop": enum_value((QgsEditFormConfig, "EditorLayout.TabLayout"),
                                        (QgsEditFormConfig, "TabLayout")),
            "ui_file": enum_value((QgsEditFormConfig, "EditorLayout.UiFileLayout"),
                                  (QgsEditFormConfig, "UiFileLayout"))}.get(layout)

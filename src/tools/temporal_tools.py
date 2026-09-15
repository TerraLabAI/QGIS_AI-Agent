# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A layer animated over time: its temporal properties and the temporal controller, in one call."""





























from __future__ import annotations

import calendar
import datetime
import math
import re
import statistics

from qgis.core import (
    Qgis,
    QgsDateTimeRange,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsInterval,
    QgsProject,
    QgsRasterLayer,
    QgsRasterLayerTemporalProperties,
    QgsTemporalNavigationObject,
    QgsUnitTypes,
    QgsVectorLayer,
    QgsVectorLayerTemporalProperties,
)
from qgis.PyQt.QtCore import QDate, QDateTime, Qt, QTime

from ..core import net
from ..core.context import _temporal_range
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .data_tools import _run_on_main_thread
from .layer_lookup import _field_not_found_error, _find_layer, _is_qgis_null, _layer_not_found_error
from .query_tools import STATS_CHUNK

UNITS = ("seconds", "minutes", "hours", "days", "weeks", "months", "years", "decades", "centuries")
_DAY_MS = 86_400_000
_FIXED_MS = {"seconds": 1_000, "minutes": 60_000, "hours": 3_600_000, "days": _DAY_MS, "weeks": 7 * _DAY_MS}
_MONTHS_IN = {"months": 1, "years": 12, "decades": 120, "centuries": 1200}
_APPROX_MS = {**_FIXED_MS, "months": 30.436875 * _DAY_MS, "years": 365.2425 * _DAY_MS,
              "decades": 3652.425 * _DAY_MS, "centuries": 36524.25 * _DAY_MS}


_LADDER = ((1, "seconds"), (1, "minutes"), (15, "minutes"), (1, "hours"), (6, "hours"), (1, "days"),
           (1, "weeks"), (1, "months"), (3, "months"), (1, "years"), (1, "decades"), (1, "centuries"))


_MAX_DERIVED_FRAMES = 500

_DISTINCT_KEPT = 100_000

_PARSE_CACHE = 50_000

_JD_TO_ORDINAL = 1_721_425
_UNIT_MEMBERS = {
    "seconds": ("Seconds", "TemporalSeconds"), "minutes": ("Minutes", "TemporalMinutes"),
    "hours": ("Hours", "TemporalHours"), "days": ("Days", "TemporalDays"), "weeks": ("Weeks", "TemporalWeeks"),
    "months": ("Months", "TemporalMonths"), "years": ("Years", "TemporalYears"),
    "decades": ("Decades", "TemporalDecades"), "centuries": ("Centuries", "TemporalCenturies"),
}
_ISO_FORMS = "2024-03-04, 2024-03-04T10:30:00, 2024-03-04 10:30 or 2024-03-04T10:30:00Z"


def register_temporal_tools(registry: ToolRegistry):
    unit = {"type": "string", "enum": list(UNITS)}
    registry.register(Tool(
        name="set_layer_temporal",
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "field": {"type": "string"},
                "end_field": {"type": "string"},
                "start_expression": {"type": "string"},
                "end_expression": {"type": "string"},
                "begin": {"type": "string"},
                "end": {"type": "string"},
                "duration": {"type": "number", "exclusiveMinimum": 0},
                "duration_unit": unit,
                "accumulate": {"type": "boolean"},
                "step": {"type": "number", "exclusiveMinimum": 0},
                "step_unit": unit,
                "enabled": {"type": "boolean"},
            },
            "required": ["layer_name"],
        },
        handler=_set_layer_temporal,
        background=True,
    ))


    registry.register(Tool(
        name="export_animation_frames",
        input_schema={
            "type": "object",
            "properties": {
                "out_dir": {"type": "string"},
                "prefix": {"type": "string"},
                "first_frame": {"type": "integer", "minimum": 0},
                "last_frame": {"type": "integer", "minimum": 0},
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
                "width": {"type": "integer", "minimum": 16},
                "height": {"type": "integer", "minimum": 16},
            },
            "required": [],
        },
        handler=_export_animation_frames,
    ))


def _export_animation_frames(args: dict) -> dict:
    from .temporal_export import export_animation_frames

    return export_animation_frames(args)








def _to_date(moment: int) -> datetime.date:
    return datetime.date.fromordinal(moment // _DAY_MS - _JD_TO_ORDINAL)


def _from_date(day: datetime.date, ms_of_day: int = 0) -> int:
    return (day.toordinal() + _JD_TO_ORDINAL) * _DAY_MS + ms_of_day


def _shift(moment: int, count: float, unit: str) -> int:
    """*moment* plus *count* units; months and years on the calendar, the day clamped like Qt's addMonths."""
    if unit in _FIXED_MS:
        return moment + int(round(count * _FIXED_MS[unit]))
    day, ms = _to_date(moment), moment % _DAY_MS
    months = day.month - 1 + int(round(count)) * _MONTHS_IN[unit]
    year = min(max(day.year + months // 12, 1), 9999)
    month = months % 12 + 1
    return _from_date(datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1])), ms)


def _floor(moment: int, unit: str) -> int:
    """The start of the *unit* holding *moment*: frames begin on the hour, the day, the first of the month."""
    if unit in ("seconds", "minutes", "hours"):
        return moment - moment % _FIXED_MS[unit]
    if unit in ("days", "weeks"):
        return moment - moment % _DAY_MS
    day = _to_date(moment)
    return _from_date(day.replace(day=1) if unit == "months" else day.replace(month=1, day=1))


def frame_count(begin: int, last: int, step: float, unit: str, inclusive: bool) -> int:
    """Frames from *begin* to the one holding *last*."""





    def covered(k: int) -> bool:
        start = _shift(begin, step * k, unit)
        return start > last if inclusive else start >= last

    n = max(1, int((last - begin) / (step * _APPROX_MS[unit])))
    while n > 1 and covered(n - 1):
        n -= 1
    while not covered(n):
        n += 1
    return n


def alignment(moments) -> str | None:
    """years, months or days when every moment sits on that boundary, else None."""
    moments = list(moments)
    if not moments or any(m % _DAY_MS for m in moments):
        return None
    days = [_to_date(m) for m in moments]
    if all(d.month == 1 and d.day == 1 for d in days):
        return "years"
    if all(d.day == 1 for d in days):
        return "months"
    return "days"


def derive_step(distinct, first: int, last: int, inclusive: bool) -> tuple[int, str]:
    """The step the data suggests: the typical gap between distinct moments, rounded up the ladder."""
    ordered = sorted(distinct)
    gaps = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
    typical = statistics.median(gaps) if gaps else max(last - first, 0)
    floor_rung = {"years": (1, "years"), "months": (1, "months"), "days": (1, "days")}.get(alignment(ordered))
    lowest = _LADDER.index(floor_rung) if floor_rung else 0
    index = next((i for i in range(lowest, len(_LADDER))
                  if _LADDER[i][0] * _APPROX_MS[_LADDER[i][1]] >= 0.9 * typical), len(_LADDER) - 1)
    while index < len(_LADDER) - 1:
        count, unit = _LADDER[index]
        if frame_count(_floor(first, unit), last, count, unit, inclusive) <= _MAX_DERIVED_FRAMES:
            break
        index += 1
    return _LADDER[index]


def _fmt(moment: int, with_time: bool) -> str:
    day = _to_date(moment)
    if not with_time:
        return day.isoformat()
    ms = moment % _DAY_MS
    return f"{day.isoformat()}T{ms // 3_600_000:02d}:{ms // 60_000 % 60:02d}:{ms // 1000 % 60:02d}"


def _plural(count: float, unit: str) -> str:
    shown = f"{count:g}"
    return f"{shown} {unit[:-1] if shown == '1' else unit}"




def _plan(args: dict) -> dict:
    """What the call asks for, checked before QGIS is touched: off, field, expression or fixed."""
    def text(key):
        return str(args.get(key) or "").strip()

    if args.get("enabled") is False:
        return {"kind": "off"}
    field, end_field = text("field"), text("end_field")
    start_expression, end_expression = text("start_expression"), text("end_expression")
    begin, end = text("begin"), text("end")
    ways = [name for name, given in (("field", field), ("start_expression", start_expression),
                                     ("begin and end", begin or end)) if given]
    if end_field and not field:
        return tool_error("end_field needs field, the date each feature starts.", "INVALID_ARGS",
                          "Pass field with the start and end_field with the end.")
    if end_expression and not start_expression:
        return tool_error("end_expression needs start_expression.", "INVALID_ARGS",
                          "Pass start_expression with the start and end_expression with the end.")
    if not ways:
        return tool_error("Nothing says where the layer's time is.", "INVALID_ARGS",
                          "Pass field (a date, datetime, ISO text or year field), field and end_field, "
                          "start_expression, or begin and end for a layer that shows one period.")
    if len(ways) > 1:
        return tool_error(f"{' and '.join(ways)} are two ways to give the time; pass one.", "INVALID_ARGS",
                          "Keep field for a date field, start_expression for a computed date, begin and end "
                          "for one period.")
    duration = args.get("duration")
    if duration is not None:
        if not (isinstance(duration, (int, float)) and duration > 0 and math.isfinite(duration)):
            return tool_error("duration must be a positive number.", "INVALID_ARGS",
                              "Pass how long each feature stays visible, with duration_unit.")
        if not field or end_field:
            return tool_error("duration applies to one date field: how long each feature stays after its date.",
                              "INVALID_ARGS", "Pass field with duration, or end_field instead of duration.")
        if args.get("duration_unit") not in UNITS:
            return tool_error("duration needs duration_unit.", "INVALID_ARGS", f"Pass one of {', '.join(UNITS)}.")
        if args["duration_unit"] in _MONTHS_IN and not float(duration).is_integer():
            return tool_error(f"A duration in {args['duration_unit']} must be a whole number.", "INVALID_ARGS",
                              "Pass a whole number, or the duration in days or weeks.")
    step = _step_arg(args)
    if isinstance(step, dict) and "_error" in step:
        return step
    plan = {"field": field, "end_field": end_field, "start_expression": start_expression,
            "end_expression": end_expression if end_expression != start_expression else "",
            "begin": begin, "end": end, "duration": duration, "duration_unit": args.get("duration_unit"),
            "accumulate": bool(args.get("accumulate")), "step": step}
    if field:
        plan["kind"] = "field"
    elif start_expression:
        plan["kind"] = "expression"
    else:
        if not (begin and end):
            return tool_error("A fixed period needs both begin and end.", "INVALID_ARGS",
                              "Pass begin and end, for example 2019-06-01 and 2019-06-30, or 2019 and 2019.")
        plan["kind"] = "fixed"
    return plan


def _step_arg(args: dict):
    """(count, unit) asked for, None to derive it, or the refusal."""
    step, unit = args.get("step"), args.get("step_unit")
    if step is None and unit is None:
        return None
    if unit not in UNITS:
        return tool_error("step needs step_unit.", "INVALID_ARGS", f"Pass step_unit, one of {', '.join(UNITS)}.")
    count = 1 if step is None else step
    if not (isinstance(count, (int, float)) and count > 0 and math.isfinite(count)):
        return tool_error("step must be a positive number.", "INVALID_ARGS",
                          "Pass step 1 with step_unit, or leave both out.")
    if unit in _MONTHS_IN and not float(count).is_integer():
        return tool_error(f"A step in {unit} must be a whole number.", "INVALID_ARGS",
                          f"Pass a whole number of {unit}, or a step in days or weeks.")
    return (int(count) if float(count).is_integer() else float(count), unit)




def _iso():
    return enum_member(Qt, "DateFormat", "ISODate")


def _moment(value: QDateTime) -> int:
    return value.date().toJulianDay() * _DAY_MS + value.time().msecsSinceStartOfDay()


def _qdt(moment: int) -> QDateTime:
    return QDateTime(QDate.fromJulianDay(moment // _DAY_MS), QTime.fromMSecsSinceStartOfDay(moment % _DAY_MS))


def _unit(name: str):
    new, old = _UNIT_MEMBERS[name]
    found = enum_member(Qgis, "TemporalUnit", new, None)
    return found if found is not None else enum_member(QgsUnitTypes, "TemporalUnit", old)


def _vector_mode(member: str):
    found = enum_member(Qgis, "VectorTemporalMode", member, None)
    if found is not None:
        return found
    return enum_member(QgsVectorLayerTemporalProperties, "TemporalMode", "Mode" + member)


def _navigation_mode(member: str):
    found = enum_member(Qgis, "TemporalNavigationMode", member, None)
    if found is not None:
        return found



    old = {"Disabled": "NavigationOff"}.get(member, member)
    return enum_member(QgsTemporalNavigationObject, "NavigationMode", old)


def _show_panel() -> str:
    """Open the Temporal Controller panel, where the play button is; an animation nobody can start is not one."""
    from qgis.PyQt.QtCore import QObject
    from qgis.utils import iface

    window = iface.mainWindow() if iface is not None else None
    action = window.findChild(QObject, "mActionTemporalController") if window is not None else None
    if action is None or not hasattr(action, "isChecked"):
        return ""
    if action.isChecked():
        return "already open"
    action.trigger()
    return "opened"


def _canvas_and_controller():
    """The main canvas and its temporal controller, or (None, None) without a QGIS window."""
    from qgis.utils import iface

    canvas = iface.mapCanvas() if iface is not None else None
    controller = canvas.temporalController() if canvas is not None else None
    if controller is None or not hasattr(controller, "setFrameDuration"):
        return canvas, None
    return canvas, controller


def _bound(text: str, is_end: bool):
    """A begin or end argument as a moment: an ISO date or datetime, or a bare year (its first or last day)."""
    value = QDateTime.fromString(text, _iso())
    if value.isValid():
        return _moment(value)
    if re.fullmatch(r"\d{4}", text) and 1000 <= int(text) <= 9999:
        year = int(text)
        if is_end:
            return _from_date(datetime.date(year, 12, 31), _DAY_MS - 1)
        return _from_date(datetime.date(year, 1, 1))
    return None


def _counters(label: str, ref: str, kind: str) -> dict:
    return {"label": label, "ref": ref, "kind": kind, "none": 0, "iso": 0, "years": 0, "bad": 0, "sample": None,
            "year_sample": None, "min": None, "max": None, "distinct": set(), "cache": {}}


def _field_source(layer, name: str) -> dict:
    fields = layer.fields()
    index = fields.indexOf(name)
    if index < 0:
        index = fields.lookupField(name)
    if index < 0:
        return _field_not_found_error(layer, name)
    field = fields.at(index)
    type_name = str(field.typeName() or "").lower()
    if field.isDateOrTime():
        if "time" in type_name and "date" not in type_name:
            return tool_error(f"{field.name()} holds a time of day without a date.", "INVALID_ARGS",
                              "Pass a date or datetime field, or start_expression that joins a date to this time.")
        kind = "date"
    elif field.isNumeric():
        kind = "number"
    elif any(word in type_name for word in ("string", "text", "char")):
        kind = "text"
    else:
        dates = [f.name() for f in fields if f.isDateOrTime()]
        return tool_error(f"{field.name()} is a {field.typeName()} field, not a date.", "INVALID_ARGS",
                          ("Date fields of the layer: " + ", ".join(dates[:8]) + ".") if dates else
                          "Pass a date, datetime, ISO text or whole-year field, or start_expression.")
    source = _counters(field.name(), QgsExpression.quotedColumnRef(field.name()), kind)
    source["index"] = index
    return source


def _expression_source(layer, text: str, context) -> dict:
    expression = QgsExpression(text)
    if expression.hasParserError():
        return tool_error(f"The expression {text[:120]!r} does not parse: {expression.parserErrorString()[:160]}",
                          "INVALID_ARGS", "Fix the expression; field names go in double quotes, text in single.")
    expression.prepare(context)
    source = _counters(text, "", "expression")
    source["expression"] = expression
    return source


def _open(args: dict, plan: dict) -> dict:
    """Main thread: the layer, where its time is read from, and the iterator the slices advance."""
    ref = str(args.get("layer_name") or "").strip()
    layer = _find_layer(ref)
    if layer is None:
        return _layer_not_found_error(ref)
    state = {"layer_id": layer.id(), "layer_name": layer.name(), "plan": plan, "total": 0}
    if isinstance(layer, QgsVectorLayer):
        state["kind"] = "vector"
    elif isinstance(layer, QgsRasterLayer):
        state["kind"] = "raster"
    else:
        return tool_error(f"{layer.name()} is neither a vector nor a raster layer.", "INVALID_ARGS",
                          "Pass a vector layer with a date field, or a raster with begin and end.")
    if plan["kind"] == "off":
        return state
    if state["kind"] == "raster" and plan["kind"] != "fixed":
        return tool_error(f"{layer.name()} is a raster: it has no fields to read dates from.", "INVALID_ARGS",
                          "Pass begin and end, the period the raster shows: 2019-06-01 and 2019-06-30, "
                          "or 2019 and 2019 for a year.")
    if plan["kind"] == "fixed":
        begin, end = _bound(plan["begin"], False), _bound(plan["end"], True)
        for key, value in (("begin", begin), ("end", end)):
            if value is None:
                return tool_error(f"{key} {plan[key]!r} is not a date QGIS can read.", "INVALID_ARGS",
                                  f"Pass an ISO date or datetime ({_ISO_FORMS}) or a year (2019).")
        if end < begin:
            return tool_error(f"end {plan['end']} is before begin {plan['begin']}.", "INVALID_ARGS",
                              "Swap them, or pass the period's first and last dates.")
        state.update(begin=begin, end=end)
        return state
    flag = enum_member(QgsFeatureRequest, "Flag", "NoGeometry")
    request = QgsFeatureRequest()
    if plan["kind"] == "field":
        start = _field_source(layer, plan["field"])
        if "_error" in start:
            return start
        end = None
        if plan["end_field"]:
            end = _field_source(layer, plan["end_field"])
            if "_error" in end:
                return end
        request.setFlags(flag)
        request.setSubsetOfAttributes([start["index"]] + ([end["index"]] if end else []))
    else:
        context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        start = _expression_source(layer, plan["start_expression"], context)
        if "_error" in start:
            return start
        end = None
        if plan["end_expression"]:
            end = _expression_source(layer, plan["end_expression"], context)
            if "_error" in end:
                return end
        expressions = [start["expression"]] + ([end["expression"]] if end else [])
        if not any(e.needsGeometry() for e in expressions):
            request.setFlags(flag)
        columns = set()
        for expression in expressions:
            columns |= set(expression.referencedColumns())

        if columns and all(layer.fields().lookupField(column) >= 0 for column in columns):
            request.setSubsetOfAttributes(sorted(columns), layer.fields())
        state["context"] = context
    state.update(start=start, end=end, features=layer.getFeatures(request))
    return state


def _classify(value, source: dict):
    """(kind, moment) for one value: none, iso, years or bad."""
    if value is None or _is_qgis_null(value):
        return "none", None
    if isinstance(value, QDateTime):
        return ("iso", _moment(value)) if value.isValid() else ("none", None)
    if isinstance(value, QDate):
        return ("iso", value.toJulianDay() * _DAY_MS) if value.isValid() else ("none", None)
    if isinstance(value, datetime.datetime):
        ms = ((value.hour * 60 + value.minute) * 60 + value.second) * 1000 + value.microsecond // 1000
        return "iso", _from_date(value.date(), ms)
    if isinstance(value, datetime.date):
        return "iso", _from_date(value)
    years_allowed = source["kind"] != "expression"
    if isinstance(value, str):
        cached = source["cache"].get(value)
        if cached is not None:
            return cached
        parsed = QDateTime.fromString(value, _iso())
        if parsed.isValid():
            found = ("iso", _moment(parsed))
        elif years_allowed and re.fullmatch(r"\d{4}", value) and int(value) >= 1000:
            found = ("years", _from_date(datetime.date(int(value), 1, 1)))
        else:
            found = ("bad", None)
        if len(source["cache"]) < _PARSE_CACHE:
            source["cache"][value] = found
        return found
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if years_allowed and float(value).is_integer() and 1000 <= value <= 9999:
            return "years", _from_date(datetime.date(int(value), 1, 1))
    return "bad", None


def _fold(source: dict, value) -> None:
    kind, moment = ("bad", None) if isinstance(value, _EvalError) else _classify(value, source)
    if kind == "none":
        source["none"] += 1
        return
    if kind == "bad":
        source["bad"] += 1
        if source["sample"] is None:
            source["sample"] = str(value)[:80]
        return
    source[kind] += 1
    if kind == "years" and source["year_sample"] is None:
        source["year_sample"] = str(value)[:80]
    source["min"] = moment if source["min"] is None else min(source["min"], moment)
    source["max"] = moment if source["max"] is None else max(source["max"], moment)
    if len(source["distinct"]) < _DISTINCT_KEPT:
        source["distinct"].add(moment)


class _EvalError(str):
    """An expression that raised on a feature; its text is the sample shown."""


def _value(feature, source: dict, context):
    if "expression" not in source:
        return feature[source["index"]]
    value = source["expression"].evaluate(context)
    if source["expression"].hasEvalError():
        return _EvalError(source["expression"].evalErrorString())
    return value


def _read(state: dict) -> bool:
    """Main thread: fold at most ``STATS_CHUNK`` features in. True when the layer is read."""
    start, end, context = state["start"], state["end"], state.get("context")
    for count, feature in enumerate(state["features"], 1):
        state["total"] += 1
        if context is not None:
            context.setFeature(feature)
        _fold(start, _value(feature, start, context))
        if end is not None:
            _fold(end, _value(feature, end, context))
        if count >= STATS_CHUNK:
            return False
    return True


def _close(state: dict) -> None:
    state["features"] = None
    state.pop("context", None)
    for source in (state.get("start"), state.get("end")):
        if source:
            source.pop("expression", None)
            source.pop("cache", None)




def _refuse_values(source: dict, total: int) -> dict | None:
    label = source["label"]
    if source["bad"]:
        read = source["bad"] + source["iso"] + source["years"]
        what = "the expression gives" if source["kind"] == "expression" else f"values of {label} are"
        message = (f"{source['bad']} of {read} {what} not dates QGIS can read, for example {source['sample']!r}."
                   if source["kind"] != "expression" else
                   f"The expression {label[:80]!r} gives no date on {source['bad']} of {read} features, "
                   f"for example {source['sample']!r}.")
        if source["kind"] == "number":
            advice = ("A number field is read as whole years (2019). For another encoding pass start_expression, "
                      'for example make_date("year", "month", 1).')
        elif source["kind"] == "expression":
            advice = ('The expression must give a date or datetime: to_datetime("text", \'dd/MM/yyyy\') '
                      "or make_date(year, 1, 1).")
        else:
            advice = (f"QGIS reads ISO dates ({_ISO_FORMS}) and years (2019). For another form pass start_expression "
                      f"with to_datetime({source['ref'] or label}, 'dd/MM/yyyy') in the form the values use.")
        return tool_error(message, "INVALID_ARGS", advice)
    if source["iso"] and source["years"]:
        return tool_error(f"{label} mixes dates ({source['iso']} values) and bare years ({source['years']} values, "
                          f"for example {source['year_sample']!r}).", "INVALID_ARGS",
                          "Pass start_expression that reads both, or make the field one form first.")
    if not (source["iso"] or source["years"]):
        return tool_error(f"None of the {total} features has a value in {label}.", "INVALID_ARGS",
                          "Pick the field that holds the dates, or check the layer's filter.")
    return None


def _year_start(ref: str) -> str:
    return f"make_datetime(to_int({ref}), 1, 1, 0, 0, 0)"


def _decide(state: dict) -> dict:
    """How the layer is set and which range and step the controller gets, from what was read."""
    plan = state["plan"]
    setting = {"accumulate": plan["accumulate"], "include_end": False, "notes": []}
    if plan["kind"] == "fixed":
        first, last, inclusive = state["begin"], state["end"], True

        distinct = [first, last]
        setting.update(mode="fixed", begin=first, end=last, time_from="one period for the whole layer")
    else:
        start, end = state["start"], state["end"]
        for source in filter(None, (start, end)):
            refused = _refuse_values(source, state["total"])
            if refused:
                return refused
        start_years, end_years = bool(start["years"]), bool(end and end["years"])
        first, distinct = start["min"], start["distinct"]
        duration, duration_unit = plan["duration"], plan["duration_unit"]
        if plan["kind"] == "expression":
            setting.update(mode="expressions", start_expression=start["label"],
                           end_expression=end["label"] if end else start["label"],
                           time_from="expressions, each feature from start_expression to "
                                     + ("end_expression" if end else "the same moment"))
            if end:
                last, inclusive = max(end["max"] if end["max"] is not None else start["max"], start["max"]), False
            else:
                last, inclusive = start["max"], True
                setting["include_end"] = True
        elif not end and not start_years:
            setting.update(mode="instant", field=start["label"], duration=duration, duration_unit=duration_unit,
                           time_from=f"field {start['label']}, each feature at its date"
                                     + (f" for {_plural(duration, duration_unit)}" if duration else ""))
            if duration:
                last, inclusive = _shift(start["max"], duration, duration_unit), False
            else:
                last, inclusive = start["max"], True
        elif end and not (start_years or end_years):
            setting.update(mode="fields", field=start["label"], end_field=end["label"],
                           time_from=f"fields {start['label']} to {end['label']}")
            ends = [m for m in (end["max"], start["max"]) if m is not None]
            last, inclusive = max(ends), end["max"] is None
        else:

            begin_expression = _year_start(start["ref"]) if start_years else f"to_datetime({start['ref']})"
            if end:
                finish = (f"make_datetime(to_int({end['ref']}) + 1, 1, 1, 0, 0, 0)" if end_years
                          else f"to_datetime({end['ref']})")
                last = max(_shift(end["max"], 1, "years") if end_years else end["max"], start["max"])
                described = f"years {start['label']} to {end['label']}"
            elif duration:
                if duration_unit in _MONTHS_IN:

                    months = int(duration) * _MONTHS_IN[duration_unit]
                    finish = f"make_datetime(to_int({start['ref']}) + {months // 12}, {months % 12 + 1}, 1, 0, 0, 0)"
                else:
                    finish = f"{begin_expression} + make_interval({duration_unit}:={duration:g})"
                last = _shift(start["max"], duration, duration_unit)
                described = f"year in {start['label']} for {_plural(duration, duration_unit)}"
            else:
                finish = f"make_datetime(to_int({start['ref']}) + 1, 1, 1, 0, 0, 0)"
                last = _shift(start["max"], 1, "years")
                described = f"year in {start['label']}, each feature for its whole year"
            inclusive = False
            setting.update(mode="expressions", start_expression=begin_expression, end_expression=finish,
                           generated=True, time_from=described)
        setting["features"] = state["total"]
        setting["no_date"] = start["none"]
        if end:
            setting["no_end"] = end["none"]
        if start["none"]:
            where = (f"stay on the map in every frame; set_layer_filter with {start['ref']} IS NOT NULL hides them"
                     if setting["mode"] in ("instant", "fields") else "are not drawn while the animation runs")
            setting["notes"].append(
                f"{start['none']} of {state['total']} features have no {start['label']} and {where}.")
        if end and end["none"] and setting["mode"] == "fields":
            setting["notes"].append(f"{end['none']} features have no {end['label']}: each stays on from its start to "
                                    "the end of the animation.")
    step = plan["step"]
    if step is None:
        count, unit = derive_step(distinct, first, last, inclusive)
        setting["step_from"] = "data"
    else:
        count, unit = step
        setting["step_from"] = "asked"
    with_time = any(m % _DAY_MS for m in (first, last)) or unit in ("seconds", "minutes", "hours")
    shown_last = last if inclusive or last % _DAY_MS else last - _DAY_MS
    setting.update(first=first, last=last, inclusive=inclusive, step=count, unit=unit,
                   range={"begin": _fmt(first, with_time), "end": _fmt(max(shown_last, first), with_time)},
                   with_time=with_time)
    return setting




def _set_vector(props, setting: dict) -> None:
    mode = setting["mode"]
    props.setMode(_vector_mode({"instant": "FeatureDateTimeInstantFromField",
                                "fields": "FeatureDateTimeStartAndEndFromFields",
                                "expressions": "FeatureDateTimeStartAndEndFromExpressions",
                                "fixed": "FixedTemporalRange"}[mode]))
    if mode == "instant":
        props.setStartField(setting["field"])
        props.setEndField("")
        props.setFixedDuration(float(setting["duration"] or 0))
        if setting["duration"]:
            props.setDurationUnits(_unit(setting["duration_unit"]))
    elif mode == "fields":
        props.setStartField(setting["field"])
        props.setEndField(setting["end_field"])
    elif mode == "expressions":
        props.setStartExpression(setting["start_expression"])
        props.setEndExpression(setting["end_expression"])
    else:
        props.setFixedTemporalRange(QgsDateTimeRange(_qdt(setting["begin"]), _qdt(setting["end"])))
    props.setAccumulateFeatures(bool(setting["accumulate"]))


    limit = enum_member(Qgis, "VectorTemporalLimitMode",
                        "IncludeBeginIncludeEnd" if setting["include_end"] else "IncludeBeginExcludeEnd", None)
    if limit is not None:
        props.setLimitMode(limit)


def _frame_holds(controller, n: int, last: int, inclusive: bool) -> bool:
    span = controller.dateTimeRangeForFrameNumber(n - 1)
    begin, end = _moment(span.begin()), _moment(span.end())
    return begin <= last < end if inclusive else begin < last <= end


def _drive_controller(layer, setting: dict) -> dict:
    """Animation mode over the data's range, joined with the range of layers already animated."""
    canvas, controller = _canvas_and_controller()
    if controller is None:
        return {"controller": "no temporal controller in this QGIS window"}
    others = [other.name() for other in QgsProject.instance().mapLayers().values()
              if other.id() != layer.id() and other.temporalProperties() is not None
              and other.temporalProperties().isActive()]
    first, last, inclusive = setting["first"], setting["last"], setting["inclusive"]
    count, unit = setting["step"], setting["unit"]
    extra = None
    if others and controller.navigationMode() != _navigation_mode("Disabled"):
        extents = controller.temporalExtents()
        if extents.begin().isValid() and extents.end().isValid():
            first = min(first, _moment(extents.begin()))
            extra = _moment(extents.end())
    begin = _floor(first, unit)
    n = frame_count(begin, last, count, unit, inclusive)
    if extra is not None:
        n = max(n, frame_count(begin, extra, count, unit, True))
    interval = QgsInterval(count, _unit(unit))
    controller.setNavigationMode(_navigation_mode("Animated"))
    controller.setFrameDuration(interval)
    candidates = [_shift(begin, count * n, unit), _shift(begin, count * (n - 1), unit),
                  begin + int((n - 0.5) * interval.seconds() * 1000)]
    chosen = candidates[-1]
    for end in candidates:
        if end <= begin:
            continue
        controller.setTemporalExtents(QgsDateTimeRange(_qdt(begin), _qdt(end)))
        if controller.totalFrameCount() == n and _frame_holds(controller, n, last, inclusive):
            chosen = end
            break
    else:
        controller.setTemporalExtents(QgsDateTimeRange(_qdt(begin), _qdt(chosen)))
    controller.rewindToStart()
    out = {"frames": int(controller.totalFrameCount()),
           "controller": {"begin": _fmt(begin, setting["with_time"]), "end": _fmt(chosen, setting["with_time"])}}
    if canvas is not None:
        canvas.refresh()
        shown = _temporal_range(canvas)
        if shown:
            out["controller"]["first_frame"] = shown
    panel = _show_panel()
    if panel:
        out["panel"] = panel
    if others:
        out["other_temporal_layers"] = others[:10]
    return out


def _switch_off(state: dict) -> dict:
    layer = QgsProject.instance().mapLayer(state["layer_id"])
    if layer is None:
        return _layer_not_found_error(state["layer_name"])
    layer.temporalProperties().setIsActive(False)
    layer.triggerRepaint()
    others = [other.name() for other in QgsProject.instance().mapLayers().values()
              if other.id() != layer.id() and other.temporalProperties() is not None
              and other.temporalProperties().isActive()]
    _canvas, controller = _canvas_and_controller()
    result = {"layer_name": layer.name(), "layer_id": layer.id(), "temporal": "off"}
    if controller is not None and not others:
        controller.setNavigationMode(_navigation_mode("Disabled"))
        result["controller"] = "off"
    elif others:
        result["controller"] = "still animating " + ", ".join(others[:10])
    return result


def _apply(state: dict, setting: dict) -> dict:
    """Main thread: the layer's temporal properties, then the controller, then what they now say."""
    layer = QgsProject.instance().mapLayer(state["layer_id"])
    if layer is None:
        return tool_error(f"{state['layer_name']} was removed while its dates were read.", "LAYER_NOT_FOUND",
                          "Load it again, then call set_layer_temporal again.")
    props = layer.temporalProperties()
    if isinstance(layer, QgsVectorLayer):
        _set_vector(props, setting)
    else:
        mode = enum_member(Qgis, "RasterTemporalMode", "FixedTemporalRange", None)
        props.setMode(mode if mode is not None else
                      enum_member(QgsRasterLayerTemporalProperties, "TemporalMode", "ModeFixedTemporalRange"))
        props.setFixedTemporalRange(QgsDateTimeRange(_qdt(setting["begin"]), _qdt(setting["end"])))
    props.setIsActive(True)
    layer.triggerRepaint()
    result = {"layer_name": layer.name(), "layer_id": layer.id(), "time_from": setting["time_from"],
              "range": setting["range"], "step": _plural(setting["step"], setting["unit"]),
              "step_from": setting["step_from"]}
    if setting.get("generated"):
        result["start_expression"], result["end_expression"] = setting["start_expression"], setting["end_expression"]
    for key in ("features", "no_date", "no_end"):
        if key in setting:
            result[key] = setting[key]
    result.update(_drive_controller(layer, setting))
    if setting["notes"]:
        result["note"] = " ".join(setting["notes"])
    return result




def _stopped() -> dict:
    return tool_error("Stopped while the layer's dates were read.", "CANCELLED", "The layer was left as it was.")


def _set_layer_temporal(args: dict) -> dict:
    plan = _plan(args)
    if "_error" in plan:
        return plan
    cancelled = net.current_cancel_check()
    try:
        state = _run_on_main_thread(_open, args, plan, timeout=60)
        if "_error" in state:
            return state
        if plan["kind"] == "off":
            return _run_on_main_thread(_switch_off, state, timeout=60)
        if plan["kind"] in ("field", "expression"):
            try:
                while True:
                    if cancelled is not None and cancelled():
                        return _stopped()
                    if _run_on_main_thread(_read, state, timeout=120):
                        break
            finally:
                _run_on_main_thread(_close, state, timeout=30)
        setting = _decide(state)
        if "_error" in setting:
            return setting
        return _run_on_main_thread(_apply, state, setting, timeout=60)
    except InterruptedError:
        return _stopped()


__all__ = ["register_temporal_tools", "derive_step", "frame_count", "UNITS"]

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A chart of a vector layer's attributes drawn to a PNG: histogram, bar, scatter or line."""






















from __future__ import annotations

import calendar
import contextlib
import datetime as _dt
import math
import os
import re
import threading
import time
from array import array
from bisect import bisect_right

from qgis.core import (
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsFeedback,
    QgsProject,
    QgsVectorLayer,
    QgsVectorLayerFeatureSource,
)

from ..core import background, limits, net, output_paths, security
from ..core.host_platform import retry_file_op
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .data_tools import _run_on_main_thread
from .layer_lookup import _field_not_found_error, _find_layer, _is_qgis_null, _layer_not_found_error

KINDS = ("histogram", "bar", "scatter", "line")
AGGREGATES = ("count", "sum", "mean")


WIDTH, HEIGHT = 1600, 1000
_DPI = 200


_BATCH = 1_000

_STOP_POLL_S = 0.05

_TABLE_ROWS = 40
_TABLE_HEAD = 30


_DISTINCT_PER_BAR = 50
_LABEL_CHARS = 24

_PAPER = "#ffffff"
_INK = "#1f2328"
_MUTED = "#57606a"
_GRID = "#d8dee4"
_FILL = "#3572a5"
_EDGE = "#24527a"
_OTHER = "#8c959f"


def register_chart_tools(registry: ToolRegistry):


    registry.register(Tool(
        name="create_chart",
        input_schema={
            "type": "object",
            "properties": {
                "layer": {"type": "string"},
                "kind": {"type": "string", "enum": list(KINDS)},
                "x_field": {"type": "string"},
                "y_field": {"type": "string"},
                "aggregate": {"type": "string", "enum": list(AGGREGATES)},
                "bins": {"type": "integer", "minimum": 1},
                "filter": {"type": "string"},
                "title": {"type": "string"},
                "output_path": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["layer", "kind", "x_field"],
        },
        handler=_create_chart,


        background=True,
    ))




def _check_args(args: dict) -> dict | None:
    """The refusals that need no layer: the kind, and the fields and aggregate it takes."""
    kind = str(args.get("kind") or "")
    if kind not in KINDS:
        return tool_error(
            f"kind {kind!r} is not a chart this tool draws.", "INVALID_ARGS",
            "Pass histogram (one numeric field), bar (a category field, and a numeric y_field to sum or average), "
            "scatter (two numeric fields) or line (a numeric y_field over a date, time or number x_field).")
    x_field = str(args.get("x_field") or "").strip()
    y_field = str(args.get("y_field") or "").strip()
    aggregate = str(args.get("aggregate") or "").strip()
    if not x_field:
        return tool_error("x_field is missing.", "INVALID_ARGS", "Pass the field the chart is about as x_field.")
    if aggregate and aggregate not in AGGREGATES:
        return tool_error(f"aggregate {aggregate!r} is not one this tool knows.", "INVALID_ARGS",
                          "Pass count, sum or mean.")
    if kind == "histogram" and y_field:
        return tool_error(
            "A histogram counts the values of one field, so it takes no y_field.", "INVALID_ARGS",
            f"Leave y_field out, or pass kind scatter to plot {x_field!r} against {y_field!r}.")


    if kind in ("histogram", "scatter") and aggregate in ("sum", "mean"):
        return tool_error(f"A {kind} takes no aggregate {aggregate}.", "INVALID_ARGS",
                          "Leave aggregate out, or pass kind bar to sum or average y_field per category.")
    if kind == "scatter" and not y_field:
        return tool_error("A scatter plots one numeric field against another, and y_field is missing.",
                          "INVALID_ARGS", "Pass the numeric y_field, or kind histogram for one field.")
    if kind == "line" and not y_field and aggregate != "count":
        return tool_error(
            "A line draws y_field over x_field, and y_field is missing.", "INVALID_ARGS",
            "Pass the numeric y_field, or aggregate count to draw how many features share each x value.")
    if aggregate in ("sum", "mean") and not y_field:
        return tool_error(f"aggregate {aggregate} needs the numeric y_field it adds up.", "INVALID_ARGS",
                          "Pass y_field, or aggregate count.")
    return None


def _field_kind(field) -> str:
    if field.isNumeric():
        return "number"
    name = str(field.typeName() or "").lower()
    if name == "time":
        return "time"
    check = getattr(field, "isDateOrTime", None)
    if (callable(check) and check()) or name in ("date", "datetime", "timestamp"):
        return "date"
    return "text"


_KIND_WORDS = {"text": "text", "date": "date", "time": "time of day", "number": "numeric"}


def _not_numeric(layer, field: dict, kind: str, axis: str) -> dict:
    numeric = [f.name() for f in layer.fields() if f.isNumeric()]
    listed = f"Numeric fields of {layer.name()}: {', '.join(numeric[:12])}." if numeric else \
        f"{layer.name()} has no numeric field."
    other = ""
    if kind == "histogram" and field["kind"] == "text":
        other = f" To count the features per value of {field['name']!r}, pass kind bar."
    elif kind == "histogram":
        other = f" To count the features per {_KIND_WORDS[field['kind']]}, pass kind line with aggregate count."
    return tool_error(
        f"{field['name']!r} holds {_KIND_WORDS[field['kind']]} values, and the {axis} axis of a {kind} needs "
        "numbers.", "INVALID_ARGS", listed + other)


def _plan(args: dict) -> dict:
    """Main thread: the layer, the fields and their kinds, the filter and the file, or the refusal."""
    ref = str(args.get("layer") or "").strip()
    layer = _find_layer(ref)
    if layer is None:
        return _layer_not_found_error(ref)
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(
            f"{layer.name()} is not a vector layer, and a chart draws the values of a layer's fields.",
            "INVALID_ARGS", "Pass a vector layer with an attribute table; a raster's values are read with "
            "get_raster_band_stats.")
    kind = str(args["kind"])
    fields = layer.fields()
    aggregate = str(args.get("aggregate") or "").strip()
    plan: dict = {"layer_id": layer.id(), "layer": layer.name(), "kind": kind, "notes": []}
    for axis, key in (("x", "x_field"), ("y", "y_field")):
        name = str(args.get(key) or "").strip()
        if not name:
            continue
        index = fields.indexOf(name)
        if index < 0:
            return _field_not_found_error(layer, name)
        plan[axis] = {"name": fields.at(index).name(), "index": index, "kind": _field_kind(fields.at(index))}
    if kind == "bar":
        aggregate = aggregate or ("sum" if plan.get("y") else "count")
        if not args.get("aggregate") and plan.get("y"):
            plan["notes"].append("aggregate sum was assumed; pass mean for an average.")
    if aggregate == "count" and plan.get("y"):
        plan["notes"].append(f"A count reads no y_field, so {plan['y']['name']!r} was left out.")
        plan.pop("y")
    plan["aggregate"] = aggregate
    numeric_axes = {"histogram": ("x",), "scatter": ("x", "y"), "line": ("y",), "bar": ("y",)}[kind]
    for axis in numeric_axes:
        if axis in plan and plan[axis]["kind"] != "number":
            return _not_numeric(layer, plan[axis], kind, axis)
    if kind == "line" and plan["x"]["kind"] == "text":
        return tool_error(
            f"{plan['x']['name']!r} is text, and a line needs x values in an order: a number, a date or a time.",
            "INVALID_ARGS", "Pass kind bar to compare its values as categories, or chart a date field "
            "(field_calculator with to_date() makes one from text).")
    if args.get("bins") is not None and kind != "histogram":
        plan["notes"].append("bins only shapes a histogram, so it was not used.")

    text = str(args.get("filter") or "").strip()
    if text:
        expression = QgsExpression(text)
        if expression.hasParserError():
            return tool_error(f"filter does not parse: {expression.parserErrorString()[:200]}", "INVALID_ARGS",
                              "Write a QGIS expression, for example \"height\" > 5 AND \"species\" = 'oak'.")
        everything = getattr(QgsFeatureRequest, "ALL_ATTRIBUTES", "#!allattributes!#")
        referenced = set(expression.referencedColumns())
        unknown = sorted(c for c in referenced if c != everything and fields.indexOf(c) < 0)
        if unknown:
            return tool_error(
                f"filter names {', '.join(repr(c) for c in unknown)}, which {layer.name()} does not have.",
                "INVALID_ARGS", f"Fields of {layer.name()}: {', '.join(f.name() for f in fields)[:300]}.")
        plan["filter"] = text
        plan["filter_columns"] = (None if everything in referenced
                                  else [fields.indexOf(c) for c in referenced])
        plan["filter_geometry"] = bool(expression.needsGeometry())

    target, refused = _output_target(args, plan)
    if refused:
        return refused
    plan["target"] = target
    plan["max_features"] = int(limits.current("CHART_MAX_FEATURES"))
    plan["max_bins"] = int(limits.current("CHART_MAX_BINS"))
    plan["max_categories"] = max(2, int(limits.current("CHART_MAX_CATEGORIES")))
    plan["max_points"] = int(limits.current("CHART_MAX_POINTS"))
    plan["bins"] = args.get("bins") if kind == "histogram" else None
    plan["title"] = str(args.get("title") or "").strip() or _default_title(plan)
    return plan


def _default_title(plan: dict) -> str:
    x, layer, kind = plan["x"]["name"], plan["layer"], plan["kind"]
    y = plan["y"]["name"] if plan.get("y") else ""
    if kind == "histogram":
        return f"Distribution of {x} in {layer}"
    if kind == "scatter":
        return f"{y} against {x} in {layer}"
    if plan["aggregate"] == "count":
        return f"Features per {x} in {layer}"
    if kind == "bar":
        return f"{plan['aggregate'].capitalize()} of {y} per {x} in {layer}"
    return f"{plan['aggregate'].capitalize()} of {y} per {x} in {layer}" if plan["aggregate"] else \
        f"{y} over {x} in {layer}"


def _output_target(args: dict, plan: dict):
    """(path, None) for the PNG, or (None, refusal). A named file is replaced only with overwrite."""
    asked = str(args.get("output_path") or "").strip()
    expanded = security.expand_path(asked) if asked else ""
    if expanded and not os.path.isdir(expanded):
        target = expanded
        extension = os.path.splitext(target)[1].lower()
        if not extension:
            target += ".png"
        elif extension != ".png":
            return None, tool_error(f"{os.path.basename(target)} is not a PNG name.", "INVALID_ARGS",
                                    "End output_path in .png: the tool writes a PNG.")
        error = security.validate_path(target, write=True)
        if error:
            return None, tool_error(error, "PERMISSION_DENIED",
                                    "Pick a path under the project folder, your home folder or the temp folder.")
        if os.path.exists(target) and args.get("overwrite") is not True:
            return None, tool_error(f"{target} already exists.", "INVALID_ARGS",
                                    "Tell the user the file exists and ask whether to replace it (overwrite true) "
                                    "or name a new file.")
        return target, None

    words = [plan["layer"], plan["kind"], plan["x"]["name"]] + ([plan["y"]["name"]] if plan.get("y") else [])
    stem = re.sub(r"\s+", "_", output_paths.safe_file_name("_".join(words), "chart"))[:120]
    folder = expanded or output_paths.default_folder()
    for index in range(1, 1000):
        candidate = os.path.join(folder, f"{stem}_{index}.png" if index > 1 else f"{stem}.png")
        if not os.path.exists(candidate):
            error = security.validate_path(candidate, write=True)
            if error:
                return None, tool_error(error, "PERMISSION_DENIED", "Pass output_path under your home folder.")
            return candidate, None
    return None, tool_error(f"{folder} already holds 999 charts named {stem}.", "INVALID_ARGS",
                            "Pass output_path with a new file name.")




def _open(plan: dict, state: dict) -> dict | None:
    """Main thread: the feature source and the request the worker reads, kept in *state*."""
    layer = QgsProject.instance().mapLayer(plan["layer_id"])
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{plan['layer']} left the project before it was read.", "EXECUTION_FAILED",
                          "Load the layer again and ask for the chart again.")
    request = QgsFeatureRequest()
    columns: list | None = [plan["x"]["index"]] + ([plan["y"]["index"]] if plan.get("y") else [])
    if plan.get("filter"):


        request.setFilterExpression(plan["filter"])
        request.setExpressionContext(
            QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer)))
        columns = None if plan["filter_columns"] is None else columns + plan["filter_columns"]
    if not plan.get("filter_geometry"):
        request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    if columns is not None:
        request.setSubsetOfAttributes(sorted(set(columns)))

    request.setLimit(plan["max_features"] + 1)
    feedback = QgsFeedback()
    if hasattr(request, "setFeedback"):
        request.setFeedback(feedback)
    state.update(source=QgsVectorLayerFeatureSource(layer), request=request, feedback=feedback)
    return None


def _read(state: dict, plan: dict, values: _Values, cancelled) -> None:
    """Worker: the features the request yields, as plain Python values, added to *values*."""












    halted, over, failure = threading.Event(), threading.Event(), []

    def reader(source, request, feedback):
        try:
            _fetch(source, request, state, plan, values, halted)
        except BaseException as exc:  # noqa: BLE001 - handed to the waiting call, which raises it
            failure.append(exc)
        finally:
            over.set()

    thread = threading.Thread(target=reader, name="create_chart read",
                              args=(state["source"], state["request"], state["feedback"]))
    thread.start()
    while not over.wait(_STOP_POLL_S):
        if _is_cancelled(cancelled):
            halted.set()
            state["feedback"].cancel()
            raise InterruptedError("Stopped while the features were read")
    if failure:
        raise failure[0]


def _fetch(source, request, state: dict, plan: dict, values: _Values, halted) -> None:
    """Reader thread: the rows *request* yields from *source*, to the last one, the ceiling, a refusal or Stop."""
    x_index, x_kind = plan["x"]["index"], plan["x"]["kind"]
    y_index = plan["y"]["index"] if plan.get("y") else None
    features = source.getFeatures(request)
    batch: list = []
    try:
        for index, feature in enumerate(features, 1):
            if halted.is_set():
                return
            if state["read"] >= plan["max_features"]:
                state["cut"] = True
                break
            state["read"] += 1
            batch.append((_plain(feature[x_index], x_kind),
                          None if y_index is None else _plain(feature[y_index], "number")))
            if len(batch) >= _BATCH:
                values.add(batch)
                batch = []
                if values.refusal:
                    return
            background.breathe(index)
        values.add(batch)
    finally:
        features.close()


def _plain(value, kind: str):
    """A number (dates and times as seconds), a string, or None for a missing value."""
    if value is None or _is_qgis_null(value):
        return None
    if kind == "number":
        if isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None
    if kind in ("date", "time"):
        return _seconds(value)
    return str(value)


def _seconds(value):
    """A date or datetime as seconds since 1970 read as UTC wall time; a time as seconds since midnight."""
    for name in ("toPyDateTime", "toPyDate", "toPyTime"):
        convert = getattr(value, name, None)
        if callable(convert):
            if hasattr(value, "isValid") and not value.isValid():
                return None
            value = convert()
            break
    if isinstance(value, _dt.datetime):
        return calendar.timegm(value.replace(tzinfo=None).timetuple()) + value.microsecond / 1e6
    if isinstance(value, _dt.date):
        return float(calendar.timegm(value.timetuple()))
    if isinstance(value, _dt.time):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    return None




class _Values:
    """What the worker keeps between slices: doubles, or one row per category."""

    def __init__(self, plan: dict):
        self.plan = plan
        self.kind = plan["kind"]
        self.grouped = self.kind == "bar" or (self.kind == "line" and bool(plan["aggregate"]))
        self.xs = array("d")
        self.ys = array("d")
        self.groups: dict = {}
        self.missing = 0
        self.distinct_cap = plan["max_categories"] * _DISTINCT_PER_BAR
        self.refusal = None

    def add(self, batch: list) -> None:
        if self.kind == "histogram":
            for x, _y in batch:
                if x is None:
                    self.missing += 1
                else:
                    self.xs.append(x)
            return
        if not self.grouped:
            for x, y in batch:
                if x is None or y is None:
                    self.missing += 1
                else:
                    self.xs.append(x)
                    self.ys.append(y)
            return
        counting = self.plan["aggregate"] == "count"
        for x, y in batch:
            if x is None and self.kind == "line":
                self.missing += 1
                continue
            group = self.groups.get(x)
            if group is None:
                if len(self.groups) >= self.distinct_cap:
                    name = self.plan["x"]["name"]
                    self.refusal = tool_error(
                        f"{name!r} has more than {self.distinct_cap:,} distinct values in the first "
                        f"features read, so a bar per value cannot be read.", "INVALID_ARGS",
                        "For a numeric field pass kind histogram; otherwise pass a filter that keeps the "
                        "categories that matter, or chart a coarser field.")
                    return
                group = self.groups[x] = [0, 0.0, 0]
            group[0] += 1
            if y is not None:
                group[1] += y
                group[2] += 1
            elif not counting:
                self.missing += 1


_EPOCH = _dt.datetime(1970, 1, 1)


def _utc(seconds: float) -> _dt.datetime:
    """Seconds since 1970 as UTC wall time, before 1970 too."""




    return _EPOCH + _dt.timedelta(seconds=seconds)


def _stamp(seconds: float, fmt: str) -> str:
    """*fmt*'s %Y %m %d %H %M %S filled from _utc(seconds), without the platform's strftime."""
    try:
        moment = _utc(seconds)
    except OverflowError:
        return f"{seconds:.0f} s"
    fields = {"%Y": f"{moment.year:04d}", "%m": f"{moment.month:02d}", "%d": f"{moment.day:02d}",
              "%H": f"{moment.hour:02d}", "%M": f"{moment.minute:02d}", "%S": f"{moment.second:02d}"}
    return re.sub(r"%[YmdHMS]", lambda match: fields[match.group(0)], fmt)


def _label(key, kind: str) -> str:
    if key is None:
        return "(no value)"
    if kind == "number":
        return f"{int(key)}" if float(key).is_integer() and abs(key) < 1e15 else f"{key:.6g}"
    if kind == "date":
        return _stamp(key, "%Y-%m-%d" if key % 86400 == 0 else "%Y-%m-%d %H:%M")
    if kind == "time":
        return _stamp(key, "%H:%M:%S")
    return str(key)


def _head_tail(rows: list) -> list:
    if len(rows) <= _TABLE_ROWS:
        return rows
    tail = _TABLE_ROWS - _TABLE_HEAD
    return rows[:_TABLE_HEAD] + [{"omitted": len(rows) - _TABLE_ROWS}] + rows[-tail:]


def _no_values(plan: dict) -> dict:
    where = " that match the filter" if plan.get("filter") else ""
    fields = plan["x"]["name"] + (f" and {plan['y']['name']}" if plan.get("y") else "")
    return tool_error(f"No feature of {plan['layer']}{where} has a value in {fields}, so nothing was charted.",
                      "INVALID_ARGS", "Check the filter and the field names; get_field_statistics counts a "
                      "field's values and its empty ones.")


def _edge(value: float) -> float:
    """A bin edge as the result carries it (core/serialization: 6 decimals, 6 digits below 1e-4)."""
    return float(f"{value:.6g}") if value and abs(value) < 1e-4 else round(value, 6)


def _histogram(plan: dict, values: _Values) -> dict:
    data = values.xs
    n = len(data)
    lo, hi = min(data), max(data)
    cuts = []
    asked = plan.get("bins")
    if asked:
        bins = int(asked)
        if bins > plan["max_bins"]:
            cuts.append(f"{bins} bins were asked and {plan['max_bins']} drawn (CHART_MAX_BINS).")
            bins = plan["max_bins"]
    else:

        bins = min(plan["max_bins"], max(5, min(30, math.ceil(math.log2(n)) + 1))) if n > 1 else 1
    if hi == lo:
        bins, edges = 1, [lo, hi]
    else:
        width = (hi - lo) / bins



        edges = [_edge(lo + i * width) for i in range(bins)] + [_edge(hi)]
    counts = [0] * bins
    last = bins - 1
    for value in data:
        index = bisect_right(edges, value) - 1
        counts[0 if index < 0 else min(index, last)] += 1
    rows = [{"lower": edges[i], "upper": edges[i + 1], "count": counts[i]} for i in range(bins)]
    return {"n": n, "rows": rows, "cuts": cuts,
            "result": {"bins": bins, "table": _head_tail(rows),
                       "table_note": "Each bin holds lower <= value < upper; the last one includes its upper."}}


def _grouped(plan: dict, values: _Values) -> dict:
    aggregate, x_kind = plan["aggregate"], plan["x"]["kind"]

    def worth(group):
        if aggregate == "count":
            return group[0]
        if not group[2]:
            return None
        return group[1] if aggregate == "sum" else group[1] / group[2]

    items = [(key, worth(group), group) for key, group in values.groups.items()]
    items = [item for item in items if item[1] is not None]
    if not items:
        return _no_values(plan)
    n = sum(item[2][0] for item in items)
    cuts = []
    other = None
    ordered = x_kind in ("number", "date", "time") or plan["kind"] == "line"
    if plan["kind"] == "bar" and len(items) > plan["max_categories"]:
        by_worth = sorted(items, key=lambda item: (-item[1], _label(item[0], x_kind)))
        kept, rest = by_worth[:plan["max_categories"] - 1], by_worth[plan["max_categories"] - 1:]
        count, total, valued = (sum(item[2][i] for item in rest) for i in range(3))
        folded = {"count": count, "sum": total, "mean": total / valued if valued else 0.0}[aggregate]
        other = {"category": f"Other ({len(rest)} values)", aggregate: folded, "features": count, "other": True}
        cuts.append(f"The {len(rest)} categories with the smallest {aggregate} share the last bar, Other "
                    f"(CHART_MAX_CATEGORIES is {plan['max_categories']}).")
        items = kept
    if ordered:
        items.sort(key=lambda item: (item[0] is None, item[0] if item[0] is not None else 0))
    else:
        items.sort(key=lambda item: (-item[1], _label(item[0], x_kind)))
    rows = []
    for key, value, group in items:
        row = {"category" if plan["kind"] == "bar" else "x": _label(key, x_kind), aggregate: value}
        if aggregate != "count":
            row["features"] = group[0]
        rows.append(row)
    positions = [key for key, _value, _group in items]
    if other is not None:
        rows.append(other)
    result = {"categories" if plan["kind"] == "bar" else "points": len(rows), "table": _head_tail(rows)}
    return {"n": n, "rows": rows, "positions": positions, "cuts": cuts, "result": result}


def _pairs(plan: dict, values: _Values) -> dict:
    xs, ys = values.xs, values.ys
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    x_kind = plan["x"]["kind"]

    def shown(value: float):
        """A date or a time as the model reads one, not as seconds."""
        return value if x_kind == "number" else _label(value, x_kind)

    summary = {"points": n, "x_min": shown(min(xs)), "x_max": shown(max(xs)), "x_mean": shown(mean_x),
               "y_min": min(ys), "y_max": max(ys), "y_mean": mean_y}
    if sxx > 0 and syy > 0:
        summary["correlation"] = round(sxy / math.sqrt(sxx * syy), 4)
    order = range(n)
    result: dict = {"summary": summary}
    if plan["kind"] == "line":
        order = sorted(range(n), key=xs.__getitem__)

        short = n <= _TABLE_ROWS
        picked = order if short else order[:_TABLE_HEAD] + order[_TABLE_HEAD - _TABLE_ROWS:]
        rows = [{"x": shown(xs[i]), "y": ys[i]} for i in picked]
        result["table"] = rows if short else rows[:_TABLE_HEAD] + [{"omitted": n - _TABLE_ROWS}] + rows[_TABLE_HEAD:]
    stride = max(1, math.ceil(n / plan["max_points"]))
    drawn = list(order)[::stride]
    cuts = []
    if stride > 1:
        cuts.append(f"{n:,} points: every {stride}th is drawn ({len(drawn):,}, CHART_MAX_POINTS); the summary "
                    "uses all of them.")
    return {"n": n, "drawn": [(xs[i], ys[i]) for i in drawn], "cuts": cuts, "result": result}


def _chart(plan: dict, values: _Values) -> dict:
    if plan["kind"] == "histogram":
        return _no_values(plan) if not values.xs else _histogram(plan, values)
    if values.grouped:
        return _grouped(plan, values)
    return _no_values(plan) if not values.xs else _pairs(plan, values)




def _nice_step(span: float, target: int) -> float:
    raw = span / max(1, target)
    magnitude = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 2.5, 5, 10):
        if raw <= multiple * magnitude * (1 + 1e-9):
            return multiple * magnitude
    return 10 * magnitude


def _number_ticks(lo: float, hi: float, target: int = 6):
    if hi <= lo:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    step = _nice_step(hi - lo, target)
    start, end = math.floor(lo / step + 1e-9) * step, math.ceil(hi / step - 1e-9) * step
    count = int(round((end - start) / step))
    decimals = next((d for d in range(7) if abs(round(step * 10 ** d) - step * 10 ** d) < 1e-6), 6)
    ticks = [start + i * step for i in range(count + 1)]
    return start, end, [(t, _number_text(t, decimals)) for t in ticks]


def _number_text(value: float, decimals: int) -> str:
    text = f"{value:,.{decimals}f}"
    return "0" if text.strip("-0.,") == "" else text


_DAY = 86400.0


def _date_ticks(lo: float, hi: float, time_of_day: bool = False):
    if hi <= lo:
        lo, hi = lo - (_DAY if not time_of_day else 1800), hi + (_DAY if not time_of_day else 1800)
    span = hi - lo
    if not time_of_day and span >= 2 * 365 * _DAY:
        first, last = _utc(lo).year, _utc(hi).year + 1
        step = next((s for s in (1, 2, 5, 10, 20, 50, 100, 500, 1000) if (last - first) / s <= 7), 2000)

        years = range(max(1, first - first % step), min(10000, last + step), step)
        ticks = [float(calendar.timegm((year, 1, 1, 0, 0, 0))) for year in years]
        fmt = "%Y"
    elif not time_of_day and span >= 60 * _DAY:
        start, end = _utc(lo), _utc(hi)
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        step = next(s for s in (1, 2, 3, 6, 12) if months / s <= 7)
        month = start.month - 1 - (start.month - 1) % step
        year, ticks = start.year, []
        while True:
            stamp = float(calendar.timegm((year + month // 12, month % 12 + 1, 1, 0, 0, 0)))
            ticks.append(stamp)
            if stamp >= hi or year + (month + step) // 12 > 9999:
                break
            month += step
        fmt = "%Y-%m"
    else:
        steps = ((60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200) if span < 2 * _DAY
                 else (_DAY, 2 * _DAY, 7 * _DAY, 14 * _DAY, 28 * _DAY))
        step = next((s for s in steps if span / s <= 7), steps[-1])
        first = math.floor(lo / step) * step
        ticks = [first + i * step for i in range(int(math.ceil((hi - first) / step)) + 1)]
        fmt = "%H:%M" if time_of_day else ("%Y-%m-%d" if span >= 2 * _DAY else "%m-%d %H:%M")
    return min(ticks[0], lo), max(ticks[-1], hi), [(t, _stamp(t, fmt)) for t in ticks]


def _axis_ticks(lo: float, hi: float, kind: str):
    if kind == "date":
        return _date_ticks(lo, hi)
    if kind == "time":
        return _date_ticks(lo, hi, time_of_day=True)
    return _number_ticks(lo, hi)


def _elide(text: str, chars: int = _LABEL_CHARS) -> str:
    text = str(text)
    return text if len(text) <= chars else text[:chars - 3].rstrip() + "..."


def _render(plan: dict, chart: dict, note: str):
    from qgis.PyQt.QtCore import QPointF, QRectF, Qt
    from qgis.PyQt.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32)
    dots = int(round(_DPI / 0.0254))
    image.setDotsPerMeterX(dots)
    image.setDotsPerMeterY(dots)
    image.fill(QColor(_PAPER))
    painter = QPainter(image)

    def font(pixels: int, bold: bool = False):
        face = QFont()
        face.setPixelSize(pixels)
        face.setBold(bold)
        return face

    left_middle = int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    right_middle = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    center_top = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
    center_bottom = int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        kind, pad = plan["kind"], 48
        title_font, axis_font, tick_font, note_font = font(34, True), font(24), font(20), font(18)


        x_kind = plan["x"]["kind"]
        if kind == "histogram":
            rows = chart["rows"]
            x_lo, x_hi = rows[0]["lower"], rows[-1]["upper"]
            y_lo, y_hi = 0.0, float(max(row["count"] for row in rows))
            y_title = "Features"
        elif kind == "bar":
            rows = chart["rows"]
            aggregate = plan["aggregate"]
            worths = [float(row[aggregate]) for row in rows]
            y_lo, y_hi = min(0.0, min(worths)), max(0.0, max(worths))
            y_title = "Features" if aggregate == "count" else f"{aggregate.capitalize()} of {plan['y']['name']}"
        elif "positions" in chart:
            aggregate = plan["aggregate"]
            points = [(float(key), float(row[aggregate])) for key, row in zip(chart["positions"], chart["rows"])]
            x_lo, x_hi = min(p[0] for p in points), max(p[0] for p in points)
            y_lo, y_hi = min(p[1] for p in points), max(p[1] for p in points)
            y_title = "Features" if aggregate == "count" else f"{aggregate.capitalize()} of {plan['y']['name']}"
        else:
            points = chart["drawn"]
            x_lo, x_hi = min(p[0] for p in points), max(p[0] for p in points)
            y_lo, y_hi = min(p[1] for p in points), max(p[1] for p in points)
            y_title = plan["y"]["name"]
        if kind in ("histogram", "bar") or (kind == "line" and plan["aggregate"] == "count"):
            y_lo = min(0.0, y_lo)
        y_start, y_end, y_ticks = _number_ticks(y_lo, y_hi)
        if kind != "bar":
            x_start, x_end, x_ticks = _axis_ticks(x_lo, x_hi, "number" if kind == "histogram" else x_kind)


        painter.setFont(title_font)
        title_height = painter.fontMetrics().height()
        painter.setPen(QColor(_INK))
        title = painter.fontMetrics().elidedText(plan["title"], Qt.TextElideMode.ElideRight, WIDTH - 2 * pad)
        painter.drawText(QRectF(pad, 28, WIDTH - 2 * pad, title_height), left_middle, title)
        painter.setFont(tick_font)
        ticks_metrics = painter.fontMetrics()
        tick_height = ticks_metrics.height()
        y_label_width = max(ticks_metrics.horizontalAdvance(text) for _value, text in y_ticks)
        painter.setFont(axis_font)
        axis_height = painter.fontMetrics().height()
        painter.setFont(note_font)
        note_height = painter.fontMetrics().height()

        labels = []
        if kind == "bar":
            labels = [_elide(row["category"]) for row in rows]
            widest = max(ticks_metrics.horizontalAdvance(text) for text in labels)
        plot_left = pad + axis_height + 16 + y_label_width + 14
        plot_right = WIDTH - pad
        plot_top = 28 + title_height + 36
        slot = (plot_right - plot_left) / max(1, len(labels)) if labels else 0.0
        rotate = bool(labels) and widest > slot - 8
        x_label_height = (int(widest * 0.64 + tick_height * 0.77) + 10) if rotate else tick_height + 8
        plot_bottom = HEIGHT - pad // 2 - note_height - 14 - axis_height - 12 - x_label_height
        plot_width, plot_height = plot_right - plot_left, plot_bottom - plot_top

        def y_at(value: float) -> float:
            return plot_bottom - (value - y_start) / ((y_end - y_start) or 1.0) * plot_height

        def x_at(value: float) -> float:
            return plot_left + (value - x_start) / ((x_end - x_start) or 1.0) * plot_width


        painter.setFont(tick_font)
        for value, text in y_ticks:
            y = y_at(value)
            painter.setPen(QPen(QColor(_GRID), 1))
            painter.drawLine(QPointF(plot_left, y), QPointF(plot_right, y))
            painter.setPen(QColor(_MUTED))
            painter.drawText(QRectF(pad + axis_height + 16, y - tick_height / 2, y_label_width, tick_height),
                             right_middle, text)
        if kind != "bar":
            for value, text in x_ticks:
                x = x_at(value)
                painter.setPen(QPen(QColor(_GRID), 1))
                painter.drawLine(QPointF(x, plot_top), QPointF(x, plot_bottom))
                painter.setPen(QColor(_MUTED))
                width = ticks_metrics.horizontalAdvance(text) + 8
                painter.drawText(QRectF(x - width / 2, plot_bottom + 8, width, tick_height), center_top, text)


        if kind == "histogram":
            show_counts = len(rows) <= 20
            for row in rows:
                x0, x1 = x_at(row["lower"]), x_at(row["upper"])
                top = y_at(row["count"])
                painter.setPen(QPen(QColor(_EDGE), 1))
                painter.setBrush(QColor(_FILL))
                painter.drawRect(QRectF(x0 + 1, top, max(1.0, x1 - x0 - 2), plot_bottom - top))
                if show_counts and row["count"]:
                    painter.setPen(QColor(_INK))
                    painter.drawText(QRectF(x0, top - tick_height - 4, x1 - x0, tick_height), center_bottom,
                                     _number_text(row["count"], 0))
        elif kind == "bar":
            zero = y_at(0.0)
            show_values = len(rows) <= 25
            for index, row in enumerate(rows):
                worth = float(row[plan["aggregate"]])
                centre = plot_left + slot * (index + 0.5)
                half = slot * 0.36
                top = y_at(worth)
                painter.setPen(QPen(QColor(_EDGE), 1))
                painter.setBrush(QColor(_OTHER if row.get("other") else _FILL))
                painter.drawRect(QRectF(centre - half, min(top, zero), 2 * half, abs(zero - top)))
                if show_values:
                    painter.setPen(QColor(_INK))
                    text = _number_text(worth, 0 if float(worth).is_integer() else 2)
                    painter.drawText(QRectF(centre - slot / 2, min(top, zero) - tick_height - 4, slot, tick_height),
                                     center_bottom, text)
                painter.setPen(QColor(_MUTED))
                if rotate:
                    painter.save()
                    painter.translate(centre, plot_bottom + 10)
                    painter.rotate(-40)
                    advance = ticks_metrics.horizontalAdvance(labels[index])
                    painter.drawText(QRectF(-advance - 4, -tick_height / 2, advance + 4, tick_height),
                                     right_middle, labels[index])
                    painter.restore()
                else:
                    painter.drawText(QRectF(centre - slot / 2, plot_bottom + 8, slot, tick_height), center_top,
                                     labels[index])
        elif kind == "scatter":

            colour = QColor(_FILL)
            colour.setAlpha(150 if len(points) > 500 else 220)
            pen = QPen(colour, 6.0 if len(points) > 2000 else 9.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.drawPoints(QPolygonF([QPointF(x_at(x), y_at(y)) for x, y in points]))
        else:
            line = QPolygonF([QPointF(x_at(x), y_at(y)) for x, y in points])
            painter.setPen(QPen(QColor(_FILL), 3))
            painter.drawPolyline(line)
            if len(points) <= 60:
                painter.setBrush(QColor(_FILL))
                for point in line:
                    painter.drawEllipse(point, 4.5, 4.5)


        painter.setPen(QPen(QColor(_INK), 2))
        painter.drawLine(QPointF(plot_left, plot_bottom), QPointF(plot_right, plot_bottom))
        painter.drawLine(QPointF(plot_left, plot_top), QPointF(plot_left, plot_bottom))
        painter.setFont(axis_font)
        painter.drawText(QRectF(plot_left, plot_bottom + x_label_height + 10, plot_width, axis_height), center_top,
                         plan["x"]["name"])
        painter.save()
        painter.translate(pad, (plot_top + plot_bottom) / 2)
        painter.rotate(-90)
        painter.drawText(QRectF(-plot_height / 2, 0, plot_height, axis_height), center_top,
                         painter.fontMetrics().elidedText(y_title, Qt.TextElideMode.ElideRight, int(plot_height)))
        painter.restore()
        painter.setFont(note_font)
        painter.setPen(QColor(_MUTED))
        painter.drawText(QRectF(pad, HEIGHT - pad // 2 - note_height, WIDTH - 2 * pad, note_height), left_middle,
                         painter.fontMetrics().elidedText(note, Qt.TextElideMode.ElideRight, WIDTH - 2 * pad))
    finally:
        painter.end()
    return image




def _stopped() -> dict:
    return tool_error("Stopped before the chart was written.", "CANCELLED", "No file was written.")


def _is_cancelled(check) -> bool:
    try:
        return callable(check) and bool(check())
    except Exception:  # noqa: BLE001 - a broken cancel check does not stop a chart
        return False


def _remove(path: str) -> None:
    if path:
        with contextlib.suppress(OSError):
            os.remove(path)


def _note(plan: dict, state: dict, values: _Values) -> str:
    parts = [plan["layer"], f"{state['read']:,} features read"]
    if values.missing:
        parts.append(f"{values.missing:,} without a value")
    if plan.get("filter"):
        parts.append(f"filter: {plan['filter']}")
    if state["cut"]:
        parts.append(f"only the first {plan['max_features']:,}")
    return " | ".join(parts)


def _create_chart(args: dict) -> dict:
    refused = _check_args(args)
    if refused:
        return refused
    started = time.monotonic()
    cancelled = net.current_cancel_check()
    state: dict = {"read": 0, "cut": False}
    try:
        plan = _run_on_main_thread(_plan, args, timeout=60)
        if "_error" in plan:
            return plan
        values = _Values(plan)
        opened = _run_on_main_thread(_open, plan, state, timeout=60)
        if opened:
            return opened
        _read(state, plan, values, cancelled)
        if values.refusal:
            return values.refusal
    except InterruptedError:
        return _stopped()
    finally:
        state.pop("request", None)
        state.pop("source", None)

    chart = _chart(plan, values)
    if "_error" in chart:
        return chart
    if _is_cancelled(cancelled):
        return _stopped()
    target = plan["target"]
    folder = os.path.dirname(target)
    part = os.path.join(folder, f".{os.path.basename(target)}.{os.getpid()}_{time.monotonic_ns()}.part")
    try:
        image = _render(plan, chart, _note(plan, state, values))
        os.makedirs(folder, exist_ok=True)
        if not image.save(part, "PNG"):
            _remove(part)
            return tool_error(f"The chart could not be written in {folder}.", "EXECUTION_FAILED",
                              "Check the folder exists and has room, or pass another output_path.")
        if _is_cancelled(cancelled):
            _remove(part)
            return _stopped()
        try:
            retry_file_op(os.replace, part, target)
        except PermissionError as exc:

            _remove(part)
            return tool_error(f"The chart was drawn but could not replace {target}: another program holds that "
                              f"file open ({exc.strerror or exc}).", "EXECUTION_FAILED",
                              "Close the image in the program showing it, or pass another output_path.")
    except OSError as exc:
        _remove(part)
        return tool_error(f"The chart could not be written: {str(exc)[:200]}", "EXECUTION_FAILED",
                          "Check the folder exists and has room, or pass another output_path.")

    result = {"output_path": target, "layer": plan["layer"], "kind": plan["kind"], "x_field": plan["x"]["name"]}
    if plan.get("y"):
        result["y_field"] = plan["y"]["name"]
    if plan["aggregate"]:
        result["aggregate"] = plan["aggregate"]
    if plan.get("filter"):
        result["filter"] = plan["filter"]
    result.update({"title": plan["title"], "features_read": state["read"], "values_charted": chart["n"],
                   "without_value": values.missing})
    result.update(chart["result"])
    cuts = list(chart["cuts"])
    if state["cut"]:
        cuts.insert(0, f"Only the first {plan['max_features']:,} features were read (CHART_MAX_FEATURES), so the "
                       "chart and the table cover those, not the whole layer; a filter charts the part that matters.")
    if cuts:
        result["cut"] = cuts
    if plan["notes"]:
        result["note"] = " ".join(plan["notes"])
    result.update({"size_px": [WIDTH, HEIGHT], "seconds": round(time.monotonic() - started, 1)})
    return result


__all__ = ["register_chart_tools", "KINDS", "AGGREGATES"]

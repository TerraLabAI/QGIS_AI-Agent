# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later

















from __future__ import annotations

import contextlib
import threading

from qgis.core import (
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeature,
    QgsFeatureRequest,
    QgsFeedback,
    QgsGeometry,
    QgsSpatialIndex,
    QgsVectorLayerFeatureSource,
)
from qgis.PyQt.QtCore import QLocale, QVariant

from ..core import background, net
from ..core.qt_compat import enum_member
from ..core.tool_registry import tool_error


_STOP_POLL_S = 0.05



_NUMERIC_NULL_TYPES = frozenset({1, 2, 3, 4, 5, 6})


def type_code(kind) -> int:

    return int(getattr(kind, "value", kind))


def is_null(value) -> bool:

    return value is None or (isinstance(value, QVariant) and value.isNull())


def _c_locale():
    locale = QLocale.c()
    locale.setNumberOptions(enum_member(QLocale, "NumberOption", "RejectGroupSeparator"))
    return locale


def qt_double(value, null_reads_zero: bool = False):








    if value is None:
        return 0.0 if null_reads_zero else None
    if isinstance(value, QVariant):
        if value.isNull():
            return 0.0 if (null_reads_zero or type_code(value.type()) in _NUMERIC_NULL_TYPES) else None
        return None
    if isinstance(value, (bool, int, float)):
        return float(value)
    if isinstance(value, str):
        number, ok = _c_locale().toDouble(value)
        return float(number) if ok else None
    return None


def _distinct_key(value):

    if is_null(value):
        return _NULL
    try:
        hash(value)
    except TypeError:
        return ("text", str(value))
    return value


_NULL = object()


class Read:


    def __init__(self, layer):
        self._layer = layer
        self.fields = layer.fields()
        self.context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        self.jobs: dict = {}
        self._names: set | None = set()
        self._geometry = False
        self._expressions: dict = {}

    def __bool__(self) -> bool:
        return bool(self.jobs)

    def field(self, index: int) -> tuple:

        if self._names is not None:
            self._names.add(self.fields.at(index).name())
        return ("f", index)

    def expression(self, text: str) -> tuple:

        if text not in self._expressions:
            expression = QgsExpression(text)
            expression.prepare(self.context)
            columns = {str(column) for column in expression.referencedColumns()}
            if QgsFeatureRequest.ALL_ATTRIBUTES in columns:
                self._names = None
            elif self._names is not None:
                self._names |= columns
            if expression.needsGeometry():
                self._geometry = True
            self._expressions[text] = expression
        return ("e", text)

    def needs(self, names=(), geometry: bool = False) -> None:

        if self._names is not None:
            self._names |= {str(name) for name in names}
        self._geometry = self._geometry or geometry

    def add(self, name: str, job):
        self.jobs[name] = job
        return job

    def spec(self) -> dict:

        request = QgsFeatureRequest()
        if self._names is not None:
            request.setSubsetOfAttributes(sorted(self._names), self.fields)
        if not self._geometry:
            request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
        rows = [job.rows for job in self.jobs.values()]
        if rows and all(count is not None for count in rows):
            request.setLimit(max(rows))
        feedback = QgsFeedback()
        if hasattr(request, "setFeedback"):
            request.setFeedback(feedback)
        return {"source": QgsVectorLayerFeatureSource(self._layer), "request": request, "feedback": feedback,
                "jobs": self.jobs, "context": self.context, "expressions": self._expressions}


class _Row:


    __slots__ = ("feature", "values", "context", "expressions")

    def __init__(self, context, expressions):
        self.feature = None
        self.values: dict = {}
        self.context = context
        self.expressions = expressions

    def value(self, key):
        try:
            return self.values[key]
        except KeyError:
            pass
        if key[0] == "f":
            value = self.feature[key[1]]
        else:
            self.context.setFeature(self.feature)
            value = self.expressions[key[1]].evaluate(self.context)
        self.values[key] = value
        return value


def read(spec: dict) -> dict:








    source, request, feedback = spec.pop("source"), spec.pop("request"), spec.pop("feedback")
    if background.on_main_thread():
        return _fetch(spec, source, request, threading.Event())
    cancelled = net.current_cancel_check()
    halted, over, failure, out = threading.Event(), threading.Event(), [], {}

    def reader(source, request, feedback):
        try:
            with background.on_any_failure(failure.append):
                out["facts"] = _fetch(spec, source, request, halted)
        finally:
            over.set()

    background.start_kept_thread(reader, source, request, feedback, name="set_layer_style read")
    while not over.wait(_STOP_POLL_S):
        if net.is_cancelled(cancelled):
            halted.set()
            feedback.cancel()
            return tool_error("Stopped while the layer's rows were read.", "CANCELLED",
                              "Nothing was changed: the layer keeps its style.")
    if failure:
        raise failure[0]
    return out["facts"]


def _fetch(spec: dict, source, request, halted) -> dict:





    jobs = spec["jobs"]
    row = _Row(spec["context"], spec["expressions"])
    features = None
    try:
        for job in jobs.values():
            job.start()
        features = source.getFeatures(request)
        feature = QgsFeature()
        row.feature = feature
        active = list(jobs.values())
        index = 0
        while features.nextFeature(feature):
            if halted.is_set():
                return {}
            row.values.clear()
            wanting = [job for job in active if job.take(index, row)]
            if len(wanting) != len(active):
                active = wanting
                if not active:
                    break
            index += 1
            if index % background.BREATHE_EVERY == 0:
                background.breathe(index)
        if halted.is_set():
            return {}
        return {name: job.result() for name, job in jobs.items()}
    finally:
        for job in jobs.values():
            with contextlib.suppress(Exception):
                job.finish()
        if features is not None:
            with contextlib.suppress(Exception):
                features.close()




class _Job:
    rows: int | None = None

    def start(self) -> None:
        pass

    def finish(self) -> None:
        pass

    def take(self, index: int, row: _Row) -> bool:

        raise NotImplementedError

    def result(self):
        raise NotImplementedError


class Distinct(_Job):






    def __init__(self, key, most: int, counted: int):
        self.key, self.most, self.counted = key, most, counted
        self.seen: dict = {}

    def take(self, index, row):
        if index >= self.counted and len(self.seen) > self.most:
            return False
        value = row.value(self.key)
        self.seen.setdefault(_distinct_key(value), value)
        return True

    def result(self):
        return list(self.seen.values())


class Frequencies(_Job):


    def __init__(self, key, rows: int):
        self.key, self.rows = key, rows
        self.counts: dict = {}
        self.scanned = 0

    def take(self, index, row):
        if index >= self.rows:
            return False
        text = str(row.value(self.key))
        self.counts[text] = self.counts.get(text, 0) + 1
        self.scanned += 1
        return True

    def result(self):
        return {"counts": self.counts, "scanned": self.scanned}


class Values(_Job):


    def __init__(self, key, rows: int):
        self.key, self.rows = key, rows
        self.values: list = []
        self.scanned = 0

    def take(self, index, row):
        if index >= self.rows:
            return False
        self.scanned += 1
        value = row.value(self.key)
        if value is None or (hasattr(value, "isNull") and value.isNull()) or str(value) == "NULL":
            return True
        self.values.append(value)
        return True

    def result(self):
        return {"values": self.values, "scanned": self.scanned}


class Numbers(_Job):


    def __init__(self, key, rows: int, number):
        self.key, self.rows, self.number = key, rows, number
        self.values: list = []

    def take(self, index, row):
        if index >= self.rows:
            return False
        number = self.number(row.value(self.key))
        if number is not None:
            self.values.append(number)
        return True

    def result(self):
        return self.values


class Doubles(_Job):









    def __init__(self, key, values: bool, extremes: bool, null_reads_zero: bool = False):
        self.key, self.keep, self.extremes, self.zero = key, values, extremes, null_reads_zero
        self.values: list = []
        self.low = self.high = None

    def take(self, index, row):
        value = row.value(self.key)
        if self.keep:
            number = qt_double(value, self.zero)
            if number is not None:
                self.values.append(number)
        if self.extremes and not is_null(value):
            with contextlib.suppress(TypeError):
                if self.low is None or value < self.low:
                    self.low = value
                if self.high is None or value > self.high:
                    self.high = value
        return True

    def result(self):
        low = 0.0 if self.low is None else (qt_double(self.low) or 0.0)
        high = 0.0 if self.high is None else (qt_double(self.high) or 0.0)
        return {"values": self.values if self.keep else None, "min": low, "max": high}


class Nulls(_Job):




    def __init__(self, key, test: bool):
        self.key, self.test = key, test
        self.count = 0

    def take(self, index, row):
        value = row.value(self.key)
        if (not is_null(value) and bool(value)) if self.test else is_null(value):
            self.count += 1
        return True

    def result(self):
        return self.count


class LabelVotes(_Job):


    def __init__(self, key, label_key, rows: int):
        self.key, self.label_key, self.rows = key, label_key, rows
        self.votes: dict = {}
        self.scanned = 0

    def take(self, index, row):
        if index >= self.rows:
            return False
        self.scanned += 1
        label = row.value(self.label_key)
        if label is not None and str(label) != "NULL" and str(label).strip():
            bucket = self.votes.setdefault(str(row.value(self.key)), {})
            text = str(label).strip()
            bucket[text] = bucket.get(text, 0) + 1
        return True

    def result(self):
        return {"votes": self.votes, "scanned": self.scanned}


class ColourVotes(_Job):







    def __init__(self, key, rows: int, colour_of, renderer=None, context=None, fields=None):
        self.key, self.rows, self.colour_of = key, rows, colour_of
        self.renderer, self.context, self.fields = renderer, context, fields
        self.votes: dict = {}
        self.values: dict = {}
        self.blank: dict = {}
        self.scanned = self.no_value = self.unreadable = 0
        self.started = False

    def start(self):
        if self.renderer is not None:
            self.renderer.startRender(self.context, self.fields)
            self.started = True

    def finish(self):
        if self.started:
            self.started = False
            self.renderer.stopRender(self.context)

    def take(self, index, row):
        if index >= self.rows:
            return False
        self.scanned += 1
        value = row.value(self.key)
        colour = self.colour_of(row)
        if value is None or str(value) == "NULL":
            self.no_value += 1
            bucket = self.blank
        else:
            text = str(value)
            bucket = self.votes.get(text)
            if bucket is None:
                bucket = self.votes[text] = {}
                self.values[text] = value
        if colour is None:
            self.unreadable += 1
        else:
            bucket[colour] = bucket.get(colour, 0) + 1
        return True

    def result(self):
        return {"votes": self.votes, "values": self.values, "blank": self.blank, "scanned": self.scanned,
                "no_value": self.no_value, "unreadable": self.unreadable}


class MinMax(_Job):



    def __init__(self, key):
        self.key = key
        self.low = self.high = None

    def take(self, index, row):
        value = row.value(self.key)
        if not is_null(value):
            number = float(value)
            if self.low is None or number < self.low:
                self.low = number
            if self.high is None or number > self.high:
                self.high = number
        return True

    def result(self):
        return self.low, self.high


class Largest(_Job):


    def __init__(self, key, rows: int):
        self.key, self.rows = key, rows
        self.largest = 0.0

    def take(self, index, row):
        if index >= self.rows:
            return False
        try:
            value = float(row.value(self.key))
        except (TypeError, ValueError):
            return True
        if value > self.largest:
            self.largest = value
        return True

    def result(self):
        return self.largest


class Touching(_Job):







    def __init__(self, key):
        self.key = key
        self.features: dict = {}
        self.classes: dict = {}

    def take(self, index, row):
        feature = row.feature
        self.classes[feature.id()] = str(row.value(self.key))
        self.features[feature.id()] = QgsGeometry(feature.geometry())
        return True

    def result(self):
        index = QgsSpatialIndex()
        for fid, geometry in self.features.items():
            index.addFeature(fid, geometry.boundingBox())
        pairs = set()
        for fid, geometry in self.features.items():
            for other in index.intersects(geometry.boundingBox()):
                a, b = self.classes[fid], self.classes.get(other)
                if other <= fid or a == b or b is None:
                    continue
                if geometry.intersects(self.features[other]):
                    pairs.add((a, b))
        return pairs

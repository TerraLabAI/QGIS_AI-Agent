# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""The feature side of a snapshot: per-feature signatures and memory-layer copies, gathered off the main thread."""









from __future__ import annotations

import os
import threading
import time
import zlib

from qgis.core import QgsProject, QgsVectorLayer

from .logger import log_warning
from .qt_compat import enum_member

MAX_MEMORY_FEATURES = 20_000








MAX_SIGNATURE_FEATURES = 8_000
MAX_SIGNATURE_TOTAL = 60_000





MAX_CAPTURE_SECONDS = 0.35

_CLOCK_EVERY = 200


def _signature(feature) -> int:
    try:
        attrs = feature.attributes()
    except Exception:  # noqa: BLE001
        attrs = ()
    return zlib.crc32(repr(list(attrs)).encode("utf-8", "replace"))


def _no_geometry_request():
    try:
        from qgis.core import QgsFeatureRequest

        return QgsFeatureRequest().setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
    except Exception:  # noqa: BLE001
        return None


def _feature_count(layer) -> int:
    """The provider's count, -1 when it has none or refuses to say."""
    try:
        return int(layer.featureCount())
    except Exception:  # noqa: BLE001 - a stub, a dropped layer
        return -1


class _FeaturePass:
    """The feature passes of one capture, off the main thread."""









    def __init__(self, project):
        self.signatures: dict[str, dict] = {}
        self.memory_features: dict[str, list] = {}
        self._done = threading.Event()
        self._plan: list = []
        self._short: list[str] = []
        budget = MAX_SIGNATURE_TOTAL
        for lid, layer in project.mapLayers().items():
            if not isinstance(layer, QgsVectorLayer):
                continue
            count = _feature_count(layer)
            if count < 0:
                continue
            memory = layer.providerType() == "memory" and count <= MAX_MEMORY_FEATURES
            signed = count <= MAX_SIGNATURE_FEATURES and count <= budget
            if not (memory or signed):
                continue
            try:
                from qgis.core import QgsVectorLayerFeatureSource

                source = QgsVectorLayerFeatureSource(layer)
            except Exception:  # noqa: BLE001 - no source (a stub, an odd provider): read it here
                self._inline(lid, layer, signed, memory)
            else:
                self._plan.append((lid, source, signed, memory, count))
            if signed:
                budget -= count

    def _inline(self, lid: str, layer, signed: bool, memory: bool) -> None:
        """Main thread, the old way, for a layer no feature source can read."""
        try:
            features = list(layer.getFeatures()) if memory else None
            if features is not None:
                self.memory_features[lid] = features
            if signed:
                source = features if features is not None else layer.getFeatures(_no_geometry_request())
                self.signatures[lid] = {f.id(): _signature(f) for f in source}
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot skipped the features of {layer.name()}: {exc}")

    def __len__(self) -> int:
        return len(self._plan)

    def start(self, on_done=None) -> None:
        if not self._plan:
            self._done.set()
            if on_done is not None:
                on_done()
            return
        try:
            from . import background

            task = background.run_off_thread("AI Agent: snapshot signatures", self._work,
                                             lambda _result, error: self._finish(error, on_done))
        except Exception:  # noqa: BLE001
            task = None
        if task is None:

            error = ""
            try:
                self._work()
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            self._finish(error, on_done)

    def _work(self):
        request = _no_geometry_request()
        for lid, source, signed, memory, count in self._plan:
            if memory:
                features = list(source.getFeatures())
                if len(features) != count:


                    self._short.append(lid)
                    continue
                self.memory_features[lid] = features
                if signed:
                    self.signatures[lid] = {f.id(): _signature(f) for f in features}
                continue
            out: dict = {}
            iterator = source.getFeatures(request) if request is not None else source.getFeatures()
            for feature in iterator:
                out[feature.id()] = _signature(feature)
                if len(out) > MAX_SIGNATURE_FEATURES:
                    out = None
                    break
            if out is not None and len(out) == count:
                self.signatures[lid] = out

    def _finish(self, error: str, on_done) -> None:
        if error:
            log_warning(f"Snapshot feature pass failed: {error}")
        if self._short:
            layers = QgsProject.instance().mapLayers()
            for lid in self._short:
                layer = layers.get(lid)
                if layer is not None:
                    self._inline(lid, layer, True, True)
            self._short = []
        self._done.set()
        if on_done is not None:
            on_done()

    def wait(self, timeout: float = 2.0) -> bool:
        return self._done.wait(timeout)

    @property
    def done(self) -> bool:
        return self._done.is_set()


_JOBS = None


def _jobs():
    """The writer thread the snapshots hand their housekeeping to."""
    global _JOBS
    if _JOBS is None:
        from .writeback import WriteBehind

        _JOBS = WriteBehind(name="ai-agent-snapshots")
    return _JOBS


def shutdown() -> None:
    """Plugin unload: the housekeeping thread finishes its queue and stops."""

    global _JOBS
    jobs, _JOBS = _JOBS, None
    if jobs is not None:
        try:
            jobs.close()
        except Exception as exc:  # noqa: BLE001 - unload never fails on housekeeping
            log_warning(f"Snapshot jobs not closed: {exc}")






REPARSE_SOURCE_BYTES = 256 * 1024
_REPARSED_SUFFIXES = (".geojson", ".json", ".kml", ".gml", ".csv")


def _reparses_per_pass(layer) -> bool:
    """Whether a feature pass over ``layer`` costs a parse of its whole file."""
    try:
        if layer.providerType() != "ogr":
            return False
        from .snapshot import layer_file_path

        path = layer_file_path(layer) or ""
        if not path.lower().endswith(_REPARSED_SUFFIXES):
            return False
        return os.path.getsize(path) > REPARSE_SOURCE_BYTES
    except Exception:  # noqa: BLE001
        return False


def feature_signatures(layer, budget: int, deadline: float | None = None) -> dict | None:
    """``{fid: attribute hash}`` for a vector layer under the caps, else None."""






    if not isinstance(layer, QgsVectorLayer):
        return None
    try:
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return None
    if count < 0 or count > MAX_SIGNATURE_FEATURES or count > budget:
        return None
    if _reparses_per_pass(layer):
        return None
    request = _no_geometry_request()
    out: dict = {}
    try:
        features = layer.getFeatures(request) if request is not None else layer.getFeatures()
        for feature in features:
            out[feature.id()] = _signature(feature)
            if len(out) > MAX_SIGNATURE_FEATURES:
                return None
            if deadline is not None and len(out) % _CLOCK_EVERY == 0 and time.monotonic() > deadline:
                return None
    except Exception:  # noqa: BLE001
        return None
    return out


def compare_signatures(before: dict, after: dict) -> dict:
    """How many features were added, removed and changed between two signature maps."""
    added = sum(1 for fid in after if fid not in before)
    removed = sum(1 for fid in before if fid not in after)
    changed = sum(1 for fid, sig in after.items() if fid in before and before[fid] != sig)
    return {"added": added, "removed": removed, "changed": changed}

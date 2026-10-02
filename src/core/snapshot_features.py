# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import collections
import hashlib
import os
import sqlite3
import threading
import time
import zlib

from qgis.core import QgsProject, QgsVectorLayer

from . import tuning
from .host_platform import remove_quietly
from .logger import log, log_warning
from .qt_compat import enum_member, field_type
from .snapshot_files import sqlite_read_only_uri


def _bytes_field_type():


    from qgis.core import Qgis

    if Qgis.QGIS_VERSION_INT >= 33800:
        from qgis.PyQt.QtCore import QMetaType

        return getattr(QMetaType, "Type", QMetaType).QByteArray
    return field_type("ByteArray")




MAX_MEMORY_FEATURES = 20_000
















MEMORY_COPY_LAYER = "features"
MEMORY_COPY_FID = "ai_agent_snapshot_fid"
MEMORY_COPY_WKB = "ai_agent_snapshot_wkb"
_COPY_BATCH = 5_000




MEMORY_COPY_FILE = "memory.gpkg"




_LAST_COPIES: dict[str, tuple[bytes, str, str, int]] = {}
_COPIES_LOCK = threading.Lock()


def forget_copies(folder: str) -> None:





    root = os.path.join(os.path.normpath(str(folder or "")), "")
    if root == os.sep:
        return
    with _COPIES_LOCK:
        for lid in [lid for lid, known in _LAST_COPIES.items() if os.path.normpath(known[1]).startswith(root)]:
            del _LAST_COPIES[lid]
_GPKG_META = ("gpkg_contents", "gpkg_ogr_contents", "gpkg_data_columns", "gpkg_extensions",
              "gpkg_metadata_reference")

REASON_TOO_LARGE = "too_large"
REASON_COPY_FAILED = "copy_failed"








MAX_SIGNATURE_FEATURES = 8_000
MAX_SIGNATURE_TOTAL = 60_000





MAX_CAPTURE_SECONDS = 0.35


def signature_cap() -> int:
    return tuning.ceiling("snapshot_max_signature_features", MAX_SIGNATURE_FEATURES, 1_000)


def signature_total() -> int:
    return tuning.ceiling("snapshot_max_signature_total", MAX_SIGNATURE_TOTAL, 8_000)


def capture_seconds() -> float:
    return tuning.ceiling("snapshot_max_capture_seconds", MAX_CAPTURE_SECONDS, 0.1)


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

    try:
        return int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return -1


def _layer_name(layer, fallback: str) -> str:
    try:
        return str(layer.name() or fallback)
    except Exception:  # noqa: BLE001
        return fallback


def _copy_field_name(index: int) -> str:
    return f"a{index}"


def _copy_table(lid: str) -> str:

    return "m_" + hashlib.sha1(str(lid).encode("utf-8", "replace"), usedforsecurity=False).hexdigest()[:20]


def is_copy_table(name) -> bool:
    return (isinstance(name, str) and len(name) == 22 and name.startswith("m_")
            and all(c in "0123456789abcdef" for c in name[2:]))


def _content_digest(fields, features) -> bytes:

    digest = hashlib.blake2b(digest_size=20)
    for i in range(fields.count()):
        field = fields.at(i)
        digest.update(repr((field.name(), field.typeName())).encode("utf-8", "replace"))
    for feature in features:
        digest.update(repr((feature.id(), list(feature.attributes()))).encode("utf-8", "replace"))
        digest.update(bytes(feature.geometry().asWkb()) if feature.hasGeometry() else b"\0")
    return digest.digest()


def _compact_copy(base: str, target: str, keep: set) -> None:







    source = sqlite3.connect(sqlite_read_only_uri(base), uri=True, timeout=5.0)
    try:
        source.execute("VACUUM INTO ?", (target,))
    finally:
        source.close()
    db = sqlite3.connect(target, timeout=5.0)
    try:
        present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        stale = [row[0] for row in db.execute("SELECT table_name FROM gpkg_contents")
                 if is_copy_table(row[0]) and row[0] not in keep]
        for table in stale:


            db.execute(f'DROP TABLE IF EXISTS "{table}"')  # nosec B608
            for meta in _GPKG_META:
                if meta in present:
                    db.execute(f"DELETE FROM {meta} WHERE lower(table_name) = lower(?)", (table,))  # nosec B608
            if "sqlite_sequence" in present:
                db.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        db.commit()
    finally:
        db.close()


def _materialised_ceiling():

    try:
        from . import limits

        return int(limits.current("MAX_FEATURES_MATERIALISED"))
    except Exception:  # noqa: BLE001
        return None


def _remove_copy(path: str) -> None:


    for suffix in ("", "-wal", "-shm", "-journal"):
        remove_quietly(path + suffix)


def refill_from_copy(layer, path: str, table: str = MEMORY_COPY_LAYER) -> int:









    from qgis.core import QgsFeature, QgsGeometry

    source = QgsVectorLayer(f"{path}|layername={table}", "snapshot copy", "ogr")
    if not source.isValid():
        raise OSError(f"copy not readable: {os.path.basename(path)}")
    provider = layer.dataProvider()
    fields = provider.fields()
    names = source.fields()
    index = [names.indexFromName(_copy_field_name(i)) for i in range(fields.count())]
    wkb = names.indexFromName(MEMORY_COPY_WKB)
    if wkb < 0:
        raise OSError(f"copy has no geometry column: {os.path.basename(path)}")
    added = 0
    batch: list = []

    def flush() -> None:
        answer = provider.addFeatures(batch)
        accepted = answer[0] if isinstance(answer, tuple) else answer
        if accepted is False:
            raise OSError("the memory provider refused the features")
        batch.clear()

    for feature in source.getFeatures():
        attrs = feature.attributes()
        out = QgsFeature(fields)
        blob = attrs[wkb]
        if blob:
            geometry = QgsGeometry()
            geometry.fromWkb(blob)
            out.setGeometry(geometry)
        out.setAttributes([attrs[j] if j >= 0 else None for j in index])
        batch.append(out)
        if len(batch) >= _COPY_BATCH:
            size = len(batch)
            flush()
            added += size
    if batch:
        size = len(batch)
        flush()
        added += size
    del source
    layer.updateExtents()
    return added


def _subset_of(layer) -> str:
    try:
        return str(layer.subsetString() or "")
    except Exception:  # noqa: BLE001
        return ""


def _with_subset_lifted(layer, read):








    provider = layer.dataProvider()
    subset = provider.subsetString()
    if not subset:
        return read(provider)
    blocked = provider.blockSignals(True)
    try:
        provider.setSubsetString("")
        return read(provider)
    finally:
        provider.setSubsetString(subset)
        provider.blockSignals(blocked)


def _unfiltered_source(layer):

    return _with_subset_lifted(layer, lambda provider: (provider.featureSource(), int(provider.featureCount()),
                                                        provider.fields()))


def held_features(layer) -> int:

    try:
        return int(_with_subset_lifted(layer, lambda provider: provider.featureCount()))
    except Exception:  # noqa: BLE001
        return _feature_count(layer)


class _FeaturePass:










    def __init__(self, project, folder: str = ""):
        self.signatures: dict[str, dict] = {}
        self.memory_features: dict[str, list] = {}




        self.memory_files: dict[str, tuple[str, int]] = {}
        self.memory_planned: dict[str, str] = {}
        self.unbacked: dict[str, tuple[str, str]] = {}
        self._done = threading.Event()




        self._features_done = threading.Event()



        self._copies_done = threading.Event()
        self._plan: list = []
        self._files: list = []
        self._folder = folder
        self._short: list[str] = []
        budget = signature_total()
        for lid, layer in project.mapLayers().items():
            if not isinstance(layer, QgsVectorLayer):
                continue
            count = _feature_count(layer)
            if count < 0:
                continue
            is_memory = layer.providerType() == "memory"
            full = None
            if is_memory and _subset_of(layer):
                try:
                    full = _unfiltered_source(layer)
                except Exception as exc:  # noqa: BLE001
                    name = _layer_name(layer, lid)
                    log_warning(f"Snapshot cannot read every feature of memory layer {name}: {exc}")
                    self.unbacked[lid] = (name, REASON_COPY_FAILED)
                    is_memory = False
            held = full[1] if full is not None else count
            if is_memory and held > 0:
                self._plan_copy(lid, layer, held, folder, full)
            memory = is_memory and held <= tuning.ceiling("snapshot_max_memory_features",
                                                          MAX_MEMORY_FEATURES, 2_000)
            signed = count <= signature_cap() and count <= budget
            if not (memory or signed):
                continue
            try:
                from qgis.core import QgsVectorLayerFeatureSource

                source = QgsVectorLayerFeatureSource(layer)
            except Exception:  # noqa: BLE001
                self._inline(lid, layer, signed, memory)
            else:
                self._plan.append((lid, source, signed, memory, count, full if memory else None))
            if signed:
                budget -= count

    def _plan_copy(self, lid: str, layer, count: int, folder: str, full=None) -> None:






        name = _layer_name(layer, lid)
        ceiling = _materialised_ceiling()
        if ceiling is not None and count > ceiling:
            self.unbacked[lid] = (name, REASON_TOO_LARGE)
            return
        if not folder:
            self.unbacked[lid] = (name, REASON_COPY_FAILED)
            return
        try:
            if full is not None:
                source, fields = full[0], full[2]
            else:
                from qgis.core import QgsVectorLayerFeatureSource

                source = QgsVectorLayerFeatureSource(layer)
                fields = layer.fields()

            job = (lid, name, source, fields, count, _copy_table(lid))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot cannot copy memory layer {name}: {exc}")
            self.unbacked[lid] = (name, REASON_COPY_FAILED)
            return
        self._files.append(job)
        self.memory_planned[lid] = name

    def _write_copy(self, lid, name, source, fields, count, table, path) -> None:

        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransformContext,
            QgsField,
            QgsFields,
            QgsVectorFileWriter,
            QgsWkbTypes,
        )

        written = 0
        error = ""
        writer = None
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            copy_fields = QgsFields()
            for i in range(fields.count()):
                field = QgsField(fields.at(i))
                field.setName(_copy_field_name(i))


                field.setLength(0)
                field.setPrecision(0)
                copy_fields.append(field)
            copy_fields.append(QgsField(MEMORY_COPY_WKB, _bytes_field_type()))
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            options.layerName = table
            options.layerOptions = [f"FID={MEMORY_COPY_FID}", "SPATIAL_INDEX=NO"]

            options.actionOnExistingFile = enum_member(
                QgsVectorFileWriter, "ActionOnExistingFile",
                "CreateOrOverwriteLayer" if os.path.exists(path) else "CreateOrOverwriteFile")

            writer = QgsVectorFileWriter.create(path, copy_fields, enum_member(QgsWkbTypes, "Type", "NoGeometry"),
                                                QgsCoordinateReferenceSystem(), QgsCoordinateTransformContext(),
                                                options)
            if writer is None or writer.hasError() != enum_member(QgsVectorFileWriter, "WriterError", "NoError"):
                error = writer.errorMessage() if writer is not None else "no writer"
            else:
                batch: list = []
                width = fields.count()
                for feature in source.getFeatures():
                    attrs = list(feature.attributes())[:width]
                    attrs.extend([None] * (width - len(attrs)))
                    attrs.append(feature.geometry().asWkb() if feature.hasGeometry() else None)
                    feature.setAttributes(attrs)
                    feature.clearGeometry()
                    batch.append(feature)
                    if len(batch) >= _COPY_BATCH:
                        if not writer.addFeatures(batch):
                            error = writer.errorMessage() or "features refused"
                            break
                        written += len(batch)
                        batch = []
                if not error and batch:
                    if writer.addFeatures(batch):
                        written += len(batch)
                    else:
                        error = writer.errorMessage() or "features refused"



                if hasattr(writer, "finalize") and writer.finalize() is False and not error:
                    error = writer.errorMessage() or "file not closed"
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
        finally:


            del writer
        if not error and written != count:
            error = f"read {written} of {count} features"
        if error:


            log_warning(f"Snapshot copy of memory layer {name} failed: {error}")
            self.unbacked[lid] = (name, REASON_COPY_FAILED)
        else:
            self.memory_files[lid] = (path, count, table)
        self.memory_planned.pop(lid, None)

    def _write_copies(self, jobs: list) -> None:







        path = os.path.join(self._folder, MEMORY_COPY_FILE)
        digests: dict[str, bytes] = {}
        for lid, name, source, fields, count, _table in jobs:
            held = self.memory_features.get(lid)
            try:
                digests[lid] = _content_digest(
                    fields, held if held is not None and len(held) == count else source.getFeatures())
            except Exception as exc:  # noqa: BLE001
                log_warning(f"Snapshot digest of memory layer {name} skipped: {exc}")
        with _COPIES_LOCK:
            previous = dict(_LAST_COPIES)
        carried = self._carry(jobs, digests, previous, path)
        for lid, name, source, fields, count, table in jobs:
            if lid in carried:
                self.memory_files[lid] = (path, count, table)
                self.memory_planned.pop(lid, None)
            else:
                self._write_copy(lid, name, source, fields, count, table, path)
        with _COPIES_LOCK:
            for lid, copy in list(self.memory_files.items()):
                if lid in digests and len(copy) > 2 and copy[0] == path:
                    _LAST_COPIES[lid] = (digests[lid], path, copy[2], copy[1])

    def _carry(self, jobs: list, digests: dict, previous: dict, path: str) -> set:







        same: dict[str, str] = {}
        for lid, _name, _source, _fields, count, table in jobs:
            known = previous.get(lid)
            if (known is not None and lid in digests and known[0] == digests[lid] and known[2] == table
                    and known[3] == count and os.path.isfile(known[1])):
                same[lid] = known[1]
        if not same:
            return set()
        base = collections.Counter(same.values()).most_common(1)[0][0]
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _remove_copy(path)
            _compact_copy(base, path, {job[5] for job in jobs})
        except (OSError, sqlite3.Error) as exc:
            log_warning(f"Snapshot memory copies not carried over, every layer is written: {exc}")
            _remove_copy(path)
            return set()
        return {lid for lid, held_in in same.items() if held_in == base}

    def _inline(self, lid: str, layer, signed: bool, memory: bool) -> None:

        try:
            full = _unfiltered_source(layer) if memory and _subset_of(layer) else None
            if full is not None:
                features = list(full[0].getFeatures())
            elif memory:
                features = list(layer.getFeatures())
            else:
                features = None
            if features is not None:
                self.memory_features[lid] = features
            if signed:
                source = layer.getFeatures(_no_geometry_request()) if full is not None or features is None else features
                self.signatures[lid] = {f.id(): _signature(f) for f in source}
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot skipped the features of {layer.name()}: {exc}")

    def __len__(self) -> int:
        return len(self._plan) + len(self._files)

    def start(self, on_done=None) -> None:
        if not self._plan and not self._files:
            self._features_done.set()
            self._copies_done.set()
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
        try:
            self._read_and_copy()
        finally:

            self._features_done.set()
            self._copies_done.set()

    def _read_and_copy(self):
        request = _no_geometry_request()
        from .net import current_cancel_check

        cancelled = current_cancel_check() or (lambda: False)
        for lid, source, signed, memory, count, full in self._plan:
            if cancelled():
                raise InterruptedError("Snapshot cancelled")
            if memory:
                reader, expected = (full[0], full[1]) if full is not None else (source, count)
                features = []
                for feature in reader.getFeatures():
                    features.append(feature)
                    if len(features) % 2000 == 0 and cancelled():
                        raise InterruptedError("Snapshot cancelled")
                if len(features) != expected:


                    self._short.append(lid)
                    continue
                self.memory_features[lid] = features
                if not signed:
                    continue
                if full is None:
                    self.signatures[lid] = {f.id(): _signature(f) for f in features}
                    continue
            out: dict = {}
            iterator = source.getFeatures(request) if request is not None else source.getFeatures()
            for feature in iterator:
                out[feature.id()] = _signature(feature)
                if len(out) % 2000 == 0 and cancelled():
                    raise InterruptedError("Snapshot cancelled")
                if len(out) > signature_cap():
                    out = None
                    break
            if out is not None and len(out) == count:
                self.signatures[lid] = out
        self._features_done.set()

        files, self._files = self._files, []
        if files:
            self._write_copies(files)

    def _finish(self, error: str, on_done) -> None:
        if error:
            log_warning(f"Snapshot feature pass failed: {error}")

        for lid, name in list(self.memory_planned.items()):
            self.unbacked[lid] = (name, REASON_COPY_FAILED)
        self.memory_planned.clear()
        if self._short:
            layers = QgsProject.instance().mapLayers()
            for lid in self._short:
                layer = layers.get(lid)
                if layer is not None:
                    self._inline(lid, layer, True, True)
            self._short = []
        self._features_done.set()
        self._copies_done.set()
        self._done.set()
        if on_done is not None:
            on_done()

    def wait_copies(self, timeout: float) -> bool:

        return self._copies_done.wait(timeout)

    def wait_features(self, timeout: float = 2.0) -> bool:

        return self._features_done.wait(timeout)

    def wait(self, timeout: float = 2.0) -> bool:
        return self._done.wait(timeout)

    @property
    def done(self) -> bool:
        return self._done.is_set()


_JOBS = None


def _jobs():

    global _JOBS
    if _JOBS is None:
        from .writeback import WriteBehind

        _JOBS = WriteBehind(name="ai-agent-snapshots")
    return _JOBS


def shutdown() -> None:


    global _JOBS
    with _COPIES_LOCK:
        _LAST_COPIES.clear()
    jobs, _JOBS = _JOBS, None
    if jobs is not None:
        try:
            jobs.close()
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Snapshot jobs not closed: {exc}")






REPARSE_SOURCE_BYTES = 256 * 1024
_REPARSED_SUFFIXES = (".geojson", ".json", ".kml", ".gml", ".csv")


def _reparses_per_pass(layer) -> bool:

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









_CONNECTION_WAIT_S = 0.1


def feature_signatures(layer, budget: int, deadline: float | None = None) -> dict | None:
















    if not isinstance(layer, QgsVectorLayer):
        return None
    try:
        count = int(layer.featureCount())
    except Exception:  # noqa: BLE001
        return None
    if count < 0 or count > signature_cap() or count > budget:
        return None
    if _reparses_per_pass(layer):
        return None
    try:
        from qgis.core import QgsVectorLayerFeatureSource

        reader = _SignatureReader(QgsVectorLayerFeatureSource(layer), _no_geometry_request())
    except Exception:  # noqa: BLE001
        return None
    reader.start()

    def left() -> float | None:
        return None if deadline is None else max(0.0, deadline - time.monotonic())

    wait = _CONNECTION_WAIT_S if deadline is None else min(_CONNECTION_WAIT_S, left())
    if not reader.connected.wait(wait):
        reader.stop.set()
        log(f"Snapshot diff: {_layer_name(layer, 'a layer')} is being read by a running task, "
            "compared on its feature count and file only.")
        return None
    if not reader.done.wait(left()):
        reader.stop.set()
        return None
    return reader.out


class _SignatureReader:







    def __init__(self, source, request):
        self._source = source
        self._request = request
        self.connected = threading.Event()
        self.done = threading.Event()
        self.stop = threading.Event()
        self.out: dict | None = None

    def start(self) -> None:
        from . import background

        background.start_kept_thread(self.run, name="AI Agent snapshot signatures")

    def run(self) -> None:
        out: dict | None = {}
        try:
            request = self._request
            features = self._source.getFeatures(request) if request is not None else self._source.getFeatures()
            self.connected.set()
            for feature in features:
                if self.stop.is_set() or len(out) >= signature_cap():
                    out = None
                    break
                out[feature.id()] = _signature(feature)
        except Exception:  # noqa: BLE001
            out = None
        self.out = out
        self.connected.set()
        self.done.set()


def compare_signatures(before: dict, after: dict) -> dict:










    from collections import Counter

    left = {fid: sig for fid, sig in before.items() if after.get(fid) != sig}
    right = {fid: sig for fid, sig in after.items() if before.get(fid) != sig}
    spare = Counter(left.values()) & Counter(right.values())
    for side in (left, right):
        budget = Counter(spare)
        for fid in list(side):
            if budget[side[fid]] > 0:
                budget[side[fid]] -= 1
                del side[fid]
    changed = sum(1 for fid in right if fid in left)
    return {"added": len(right) - changed, "removed": len(left) - changed, "changed": changed}

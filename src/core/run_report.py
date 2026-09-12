# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



































from __future__ import annotations

import os
from collections.abc import Iterable

from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from . import tuning
from .snapshot import RunSnapshot, layer_file_path
from .snapshot_report import diff_changed


VERIFY_RUN = "verify_run"




MAX_LAYERS = 20

MAX_FILES = 20

MAX_WARNINGS = 12


MAX_CALL_WARNINGS = 40


MAX_OUTPUT_FILES = 20


def _cap(key: str, shipped: int) -> int:
    return tuning.ceiling(key, shipped, 5)


def _listed(value) -> list:

    return value if isinstance(value, list) else []


def _empty_report() -> dict:

    return {
        "changed": False,
        "project_checked": False,
        "layers_added": [],
        "layers_changed": [],
        "layers_removed": [],
        "files_written": [],



        "files_made": [],
        "empty_outputs": [],
        "working_copies": [],
        "warnings": [],
        "counts": {"layers_added": 0, "layers_changed": 0, "layers_removed": 0,
                   "files_written": 0, "empty_outputs": 0, "warnings": 0},
    }


def _counts(report: dict) -> dict:

    return {
        "layers_added": len(report["layers_added"]),
        "layers_changed": len(report["layers_changed"]),
        "layers_removed": len(report["layers_removed"]),
        "files_written": len(report["files_written"]),
        "empty_outputs": len(report["empty_outputs"]),
        "warnings": len(report["warnings"]),
    }


def _view():

    try:
        import qgis.utils

        iface = getattr(qgis.utils, "iface", None)
        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is None:
            return None
        return canvas.extent(), canvas.mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001
        return None


def _is_table(layer) -> bool:

    try:
        return isinstance(layer, QgsVectorLayer) and not layer.isSpatial()
    except Exception:  # noqa: BLE001
        return False


def _layer_kind(layer) -> str:
    if isinstance(layer, QgsVectorLayer):
        return "table" if _is_table(layer) else "vector"
    if isinstance(layer, QgsRasterLayer):
        return "raster"
    return "other"


def _crs_name(layer) -> str:

    try:
        crs = layer.crs()
        return str(crs.authid() or "") if crs.isValid() else ""
    except Exception:  # noqa: BLE001
        return ""


def _feature_count(layer) -> int | None:





    from .layer_order import feature_count_of

    return feature_count_of(layer)


def _in_view(layer, extent, view) -> bool | None:





    try:
        if extent is None or extent.isEmpty():
            return None
        view_extent, view_crs = view
        layer_crs = layer.crs()
        if layer_crs != view_crs and layer_crs.isValid() and view_crs.isValid():
            transform = QgsCoordinateTransform(layer_crs, view_crs, QgsProject.instance())
            extent = transform.transformBoundingBox(extent)
        return bool(extent.intersects(view_extent))
    except Exception:  # noqa: BLE001
        return None


def _switched_on(node) -> bool | None:

    try:
        return bool(node.isVisible())
    except Exception:  # noqa: BLE001
        return None


def _local_raster(layer) -> bool:

    try:
        if not isinstance(layer, QgsRasterLayer) or (layer.providerType() or "").lower() != "gdal":
            return False
        path = layer_file_path(layer)
        return bool(path) and os.path.isfile(path)
    except Exception:  # noqa: BLE001
        return False


def _raster_has_data(layer) -> bool | None:

    try:
        from ..tools.postconditions import _has_any_data

        return _has_any_data(layer)
    except Exception:  # noqa: BLE001
        return None


def _added_layer(project, raw: dict, orphans: set, view, hidden: frozenset = frozenset(),
                 spoken: frozenset = frozenset()) -> tuple[dict | None, str | None]:








    lid = str(raw.get("id") or "")
    name = str(raw.get("name") or lid or "?")
    layer = project.mapLayer(lid)
    if layer is None:
        return None, None
    item: dict = {"id": lid, "name": name, "kind": _layer_kind(layer), "crs": _crs_name(layer)}

    table = item["kind"] == "table"
    empty = False

    extent = None
    try:
        extent = layer.extent()
        if extent is not None and extent.isEmpty():
            extent = None
    except Exception:  # noqa: BLE001
        extent = None

    count = _feature_count(layer) if isinstance(layer, QgsVectorLayer) else None
    if count is not None:
        item["features"] = count
        item["empty"] = empty = count == 0

    sampled = None
    try:
        from ..tools.postconditions import _invalid_geometries

        sampled = _invalid_geometries(layer)
    except Exception:  # noqa: BLE001
        sampled = None
    if sampled is not None:
        item["invalid_geometries"] = sampled

    if view is not None and not table:
        seen = _in_view(layer, extent, view)
        if seen is not None:
            item["in_view"] = seen

    if count is not None and count > 0 and extent is None and not table:
        item["no_extent"] = True

    if lid not in spoken and _local_raster(layer) and _raster_has_data(layer) is False:
        item["empty"] = empty = True
        item["no_data"] = True

    node = None if lid in orphans else project.layerTreeRoot().findLayer(lid)
    item["in_tree"] = node is not None
    if node is not None:
        visible = _switched_on(node)
        if visible is not None:
            item["visible"] = visible
            if not visible and lid in hidden:
                item["hidden_by_run"] = True

    path = layer_file_path(layer)
    if path:
        item["file"] = path
        try:
            item["size_bytes"] = int(os.path.getsize(path))
        except OSError:  # noqa: BLE001
            pass
    return item, (name if empty else None)


def _changed_layers(diff: dict) -> tuple[list, list]:





    merged: dict[str, dict] = {}
    order: list[str] = []
    empty: list[str] = []

    def item_for(raw: dict) -> dict:
        lid = str(raw.get("id") or "")
        item = merged.get(lid)
        if item is None:
            item = {"id": lid, "name": str(raw.get("name") or lid or "?")}
            merged[lid] = item
            order.append(lid)
        return item

    for raw in _listed(diff.get("feature_count_changes")):
        if not isinstance(raw, dict):
            continue
        item = item_for(raw)
        if "before" in raw:
            item["features_before"] = raw["before"]
        if "after" in raw:
            item["features_after"] = raw["after"]
        before, after = raw.get("before"), raw.get("after")
        if isinstance(before, int) and not isinstance(before, bool) and before > 0 and after == 0:
            empty.append(item["name"])
    for raw in _listed(diff.get("crs_changes")):
        if not isinstance(raw, dict):
            continue
        item = item_for(raw)
        if "before" in raw:
            item["crs_before"] = raw["before"]
        if "after" in raw:
            item["crs_after"] = raw["after"]
    return [merged[lid] for lid in order], empty


def _stopped_output():








    try:
        from ..tools.processing_tools import canceled_output
    except Exception:  # noqa: BLE001
        return lambda _path: False
    return canceled_output


def _made_files(declared) -> list:






    entries: list = []
    seen: set = set()
    for raw in declared or ():
        if not isinstance(raw, dict) or len(entries) >= _cap("run_report_max_output_files", MAX_OUTPUT_FILES):
            continue
        path = raw.get("path")
        if not isinstance(path, str) or not path or path in seen:
            continue
        seen.add(path)
        entry = {"path": path}
        if isinstance(raw.get("kind"), str) and raw["kind"]:
            entry["kind"] = raw["kind"]
        try:
            entry["exists"] = bool(os.path.exists(path))
            if entry["exists"]:
                entry["size_bytes"] = int(os.path.getsize(path))
        except (OSError, ValueError):
            entry["exists"] = False
        entries.append(entry)
    return entries


def _list_files(paths: list) -> list:





    entries: list = []
    seen: set = set()
    for path in paths:
        if len(entries) >= _cap("run_report_max_files", MAX_FILES):
            break
        try:
            key = os.path.realpath(path)
        except (OSError, ValueError):
            key = path
        if key in seen:
            continue
        seen.add(key)
        try:
            exists = bool(os.path.exists(path))
            entry = {"path": path, "exists": exists}
            if exists:
                entry["size_bytes"] = int(os.path.getsize(path))
        except (OSError, ValueError):
            continue
        entries.append(entry)
    return entries


def _standing_call_warnings(entries, report: dict) -> list[str]:






    if not entries:
        return []
    said = {str(item.get("id") or "") for item in report["layers_added"]}
    said |= {str(item.get("id") or "") for item in report["layers_changed"] if item.get("features_after") == 0}
    project = QgsProject.instance()
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not entry["text"].strip():
            continue
        layers = [lid for lid in entry.get("layers") or () if isinstance(lid, str) and lid]
        if layers and all(project.mapLayer(lid) is None for lid in layers):
            continue
        if entry.get("about") == "empty" and said.intersection(layers):
            continue
        if not _still_true(entry):
            continue
        out.append(entry["text"])
    return out


def _changed_view(project, items: list, view, hidden: frozenset) -> None:

    root = project.layerTreeRoot()
    for item in items[:_cap("run_report_max_layers", MAX_LAYERS)]:
        lid = str(item.get("id") or "")
        layer = project.mapLayer(lid) if lid else None
        if layer is None:
            continue
        try:
            if view is not None and not _is_table(layer):
                seen = _in_view(layer, layer.extent(), view)
                if seen is not None:
                    item["in_view"] = seen
            node = root.findLayer(lid)
            visible = _switched_on(node) if node is not None else None
            if visible is not None:
                item["visible"] = visible
                if not visible and lid in hidden:
                    item["hidden_by_run"] = True
        except Exception:  # noqa: BLE001  # nosec B112
            continue


def _working_copies(entries) -> list:





    project = QgsProject.instance()
    copies: list = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        lid = str(entry.get("layer_id") or entry.get("id") or "")
        layer = project.mapLayer(lid) if lid else None
        if layer is None:
            continue
        name = str(layer.name() or entry.get("name") or lid)
        copies.append({"id": lid, "name": name})
    return copies


def _joined_warnings(calls: list) -> list:

    return list(dict.fromkeys(calls))[:_cap("run_report_max_warnings", MAX_WARNINGS)]


def _build_report(snapshot, written, view, call_warnings=(), diff=None, working=(), hidden=(),
                  declared=()) -> dict:
    if view is None:

        view = _view()
    report = _empty_report()
    report["files_made"] = _made_files(declared)
    empty_outputs: list[str] = []
    report["working_copies"] = _working_copies(working)

    known, diff = diff, None
    if snapshot is not None and getattr(snapshot, "captured", False):

        found = known if isinstance(known, dict) else snapshot.diff()
        if isinstance(found, dict):
            diff = found

    report["project_checked"] = diff is not None
    if diff is not None:
        project = QgsProject.instance()
        orphans = {str(entry.get("id") or "") for entry in _listed(diff.get("orphan_temporary_layers"))
                   if isinstance(entry, dict)}

        switched_off = frozenset({str(lid) for lid in hidden or ()} | {
            str(entry.get("id") or "") for entry in _listed(diff.get("visibility_changes"))
            if isinstance(entry, dict) and entry.get("visible") is False})

        spoken = frozenset(lid for entry in call_warnings if isinstance(entry, dict)
                           for lid in entry.get("layers") or () if isinstance(lid, str))
        for raw in _listed(diff.get("layers_added"))[:_cap("run_report_max_layers", MAX_LAYERS)]:
            if not isinstance(raw, dict):
                continue
            try:
                item, layer_empty = _added_layer(project, raw, orphans, view, switched_off, spoken)
            except Exception:  # noqa: BLE001  # nosec B112
                continue
            if item is None:
                continue
            report["layers_added"].append(item)
            if layer_empty:
                empty_outputs.append(layer_empty)
        changed_items, changed_empty = _changed_layers(diff)
        report["layers_changed"] = changed_items
        _changed_view(project, changed_items, view, switched_off)
        empty_outputs.extend(changed_empty)
        for raw in _listed(diff.get("layers_removed")):
            if isinstance(raw, dict):
                report["layers_removed"].append({"id": str(raw.get("id") or ""),
                                                 "name": str(raw.get("name") or "")})

    ordered: list[str] = []
    if diff is not None:
        for raw in _listed(diff.get("files_changed")):
            if isinstance(raw, dict) and isinstance(raw.get("path"), str) and raw["path"]:
                ordered.append(raw["path"])
        for item in report["layers_added"]:
            path = item.get("file")
            if isinstance(path, str) and path:
                ordered.append(path)
    stopped = _stopped_output()
    for path in written or ():
        if isinstance(path, str) and path and not stopped(path):
            ordered.append(path)

    report["files_written"] = _list_files(ordered)
    if diff is None:

        report["warnings"] = _joined_warnings(_standing_call_warnings(call_warnings, report))
        report["changed"] = any(entry["exists"] for entry in report["files_written"])
        report["counts"] = _counts(report)
        return report

    report["empty_outputs"] = list(dict.fromkeys(empty_outputs))
    report["warnings"] = _joined_warnings(_standing_call_warnings(call_warnings, report))
    report["changed"] = bool(diff_changed(diff) or any(entry["exists"] for entry in report["files_written"]))
    report["counts"] = _counts(report)
    return report


def written_paths(name: str, args, result) -> list[str]:











    try:
        candidates: list = []
        declared: tuple = ()
        try:
            from ..tools import guards

            declared = tuple(guards.WRITE_PATH_ARGS.get(name, ()))
            try:
                candidates = list(guards._write_targets(name, args))
            except Exception:  # noqa: BLE001
                candidates = [args.get(key) for key in declared]
        except Exception:  # noqa: BLE001
            candidates = []
        if isinstance(result, dict) and "_error" not in result:
            candidates += [value for value in (result.get(key) for key in declared)
                           if isinstance(value, str) and not os.path.isdir(value)]
        outputs = result.get("outputs") if isinstance(result, dict) else None
        if isinstance(outputs, dict):
            for value in outputs.values():
                if isinstance(value, dict):
                    candidates.append(value.get("path"))
        out: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            path = candidate.split("|", 1)[0]
            if not os.path.isabs(path):
                continue
            if path.startswith(("memory:", "/vsi", "http://", "https://")) or path == "TEMPORARY_OUTPUT":
                continue
            if path not in seen:
                seen.add(path)
                out.append(path)
        return out
    except Exception:  # noqa: BLE001
        return []


def result_files(result) -> list[dict]:













    try:
        if not isinstance(result, dict) or "_error" in result:
            return []
        listed = result.get("files")
        if not isinstance(listed, (list, tuple)):




            listed = [result["exported"]] if isinstance(result.get("exported"), str) else []
        out: list[dict] = []
        seen: set[str] = set()
        for entry in listed:
            if isinstance(entry, str):
                path, kind = entry, ""
            elif isinstance(entry, dict):
                path, kind = entry.get("path"), entry.get("kind")
            else:
                continue
            if not isinstance(path, str):
                continue
            path = path.split("|", 1)[0]
            if not os.path.isabs(path) or path in seen:
                continue
            if path.startswith(("memory:", "/vsi", "http://", "https://")) or path == "TEMPORARY_OUTPUT":
                continue


            try:
                if os.path.isdir(path):
                    continue
            except (OSError, ValueError):
                continue
            seen.add(path)
            out.append({"path": path, "kind": kind} if isinstance(kind, str) and kind else {"path": path})
        return out
    except Exception:  # noqa: BLE001
        return []


def _sentence(text) -> str:

    line = " ".join(str(text or "").split())
    return line[:1].upper() + line[1:]


def _named_layers(result: dict) -> tuple[list[str], list[str]]:

    ids: list[str] = []
    names: list[str] = []
    entries = [result]
    outputs = result.get("outputs")
    if isinstance(outputs, dict):
        entries.extend(outputs.values())
    layers = result.get("layers")
    if isinstance(layers, list):
        entries.extend(layers)
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        lid = entry.get("layer_id")
        if not isinstance(lid, str) or not lid or lid in ids:
            continue
        ids.append(lid)
        name = entry.get("layer_name") or entry.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return ids, names


def _field_fault(verified: dict) -> bool:

    return verified.get("field_present") is False or bool(verified.get("sampled") and not verified.get("non_null"))


def _unit_recheck(unit) -> dict | None:

    if not isinstance(unit, dict) or not unit.get("warning") or not unit.get("layer_id") or not unit.get("field"):
        return None
    return {"kind": "unit", "layer_id": str(unit["layer_id"]), "field": str(unit["field"])}


def _still_true(entry: dict) -> bool:






    recheck = entry.get("recheck")
    if not isinstance(recheck, dict) or recheck.get("kind") != "unit":
        return True
    try:
        from .invariants import unit_still_wrong

        return unit_still_wrong(recheck.get("layer_id"), recheck.get("field")) is not False
    except Exception:  # noqa: BLE001
        return True


def _warnings_of(name: str, result, found: list) -> None:
    if not isinstance(result, dict) or "_error" in result:
        return
    ids, names = _named_layers(result)
    subject = repr(names[0]) if len(names) == 1 else str(result.get("algorithm") or name)

    def add(text, about: str = "", lead: bool = True, recheck: dict | None = None) -> None:
        sentence = _sentence(text)
        if sentence:
            entry = {"text": f"{subject}: {sentence}" if lead else sentence, "layers": list(ids), "about": about}
            if recheck:
                entry["recheck"] = recheck
            found.append(entry)

    checks = result.get("checks")
    if isinstance(checks, dict) and isinstance(checks.get("warnings"), (list, tuple)):
        unit = _unit_recheck(checks.get("unit_factor"))
        for text in checks["warnings"]:
            add(text, recheck=unit if unit and text == checks["unit_factor"].get("warning") else None)
    verified = result.get("verified")
    if isinstance(verified, dict) and verified.get("warning"):
        unit = _unit_recheck(verified.get("unit_factor"))
        if unit and verified["warning"] == verified["unit_factor"].get("warning"):
            add(verified["warning"], lead=False, recheck=unit)
        else:



            restated = (verified.get("present") is True and "removed" not in verified
                        and not _field_fault(verified) and "value_range" not in verified)
            add(verified["warning"], "empty" if restated else "", lead=False)


def call_warnings(name: str, result) -> list[dict]:











    found: list[dict] = []
    try:
        _warnings_of(str(name or ""), result, found)
    except Exception:  # noqa: BLE001  # nosec B110
        return found
    return found


def build_report(snapshot: RunSnapshot | None, written: Iterable[str] = (), view=None,
                 call_warnings: Iterable[dict] = (), diff: dict | None = None,
                 working_copies: Iterable[dict] = (), hidden: Iterable[str] = (),
                 declared: Iterable[dict] = ()) -> dict:














    try:
        return _build_report(snapshot, written, view, tuple(call_warnings or ()), diff,
                             tuple(working_copies or ()), tuple(hidden or ()),
                             tuple(declared or ()))
    except Exception as exc:  # noqa: BLE001
        report = _empty_report()
        report["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return report

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the run left in the project, read back before the final answer."""




























from __future__ import annotations

import os
from collections.abc import Iterable

from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from .snapshot import RunSnapshot, layer_file_path
from .snapshot_report import diff_changed
from .tool_registry import Tool, ToolRegistry, tool_error


VERIFY_RUN = "verify_run"

MAX_LAYERS = 20

MAX_FILES = 20

MAX_WARNINGS = 12


MAX_CALL_WARNINGS = 40




_WARNING_TOOLS = frozenset({"add_table_join"})
_BATCH_TOOL = "batch_commands"


def _listed(value) -> list:
    """A diff list, or [] when the key is missing or carries another shape."""
    return value if isinstance(value, list) else []


def _empty_report() -> dict:
    """The report with nothing in it: no snapshot, no change, nothing to say."""
    return {
        "changed": False,
        "project_checked": False,
        "layers_added": [],
        "layers_changed": [],
        "layers_removed": [],
        "files_written": [],
        "empty_outputs": [],
        "working_copies": [],
        "warnings": [],
        "counts": {"layers_added": 0, "layers_changed": 0, "layers_removed": 0,
                   "files_written": 0, "empty_outputs": 0, "warnings": 0},
    }


def _counts(report: dict) -> dict:
    """The lengths the run record keeps; names and paths never leave the report."""
    return {
        "layers_added": len(report["layers_added"]),
        "layers_changed": len(report["layers_changed"]),
        "layers_removed": len(report["layers_removed"]),
        "files_written": len(report["files_written"]),
        "empty_outputs": len(report["empty_outputs"]),
        "warnings": len(report["warnings"]),
    }


def _view():
    """The canvas extent and CRS, or None when no QGIS window is running."""
    try:
        import qgis.utils

        iface = getattr(qgis.utils, "iface", None)
        canvas = iface.mapCanvas() if iface is not None else None
        if canvas is None:
            return None
        return canvas.extent(), canvas.mapSettings().destinationCrs()
    except Exception:  # noqa: BLE001 - no iface in a headless run, and no view to judge
        return None


def _layer_kind(layer) -> str:
    if isinstance(layer, QgsVectorLayer):
        return "vector"
    if isinstance(layer, QgsRasterLayer):
        return "raster"
    return "other"


def _crs_name(layer) -> str:
    """The layer's authid, "" when it declares none that QGIS accepts."""
    try:
        crs = layer.crs()
        return str(crs.authid() or "") if crs.isValid() else ""
    except Exception:  # noqa: BLE001 - a layer without a readable CRS is reported without one
        return ""


def _feature_count(layer) -> int | None:
    """What the layer holds: the stamped true count for a capped remote load (``layer_order.mark_truncated_count``), else the provider's count."""




    from .layer_order import feature_count_of

    return feature_count_of(layer)


def _in_view(layer, extent, view) -> bool | None:
    """Whether the layer's extent meets the view, or None when it cannot be told."""




    try:
        if extent is None or extent.isEmpty():
            return None
        view_extent, view_crs = view
        layer_crs = layer.crs()
        if layer_crs != view_crs and layer_crs.isValid() and view_crs.isValid():
            transform = QgsCoordinateTransform(layer_crs, view_crs, QgsProject.instance())
            extent = transform.transformBoundingBox(extent)
        return bool(extent.intersects(view_extent))
    except Exception:  # noqa: BLE001 - an extent that cannot be compared skips in_view
        return None


def _switched_on(node) -> bool | None:
    """Whether a layer tree node draws: checked, and every group above it checked too."""
    try:
        return bool(node.isVisible())
    except Exception:  # noqa: BLE001 - a node that cannot say is reported without it
        return None


def _local_raster(layer) -> bool:
    """A raster read from a file on this computer, so a sample of it reads the disk and never the network."""
    try:
        if not isinstance(layer, QgsRasterLayer) or (layer.providerType() or "").lower() != "gdal":
            return False
        path = layer_file_path(layer)
        return bool(path) and os.path.isfile(path)
    except Exception:  # noqa: BLE001 - a source that will not read is not sampled
        return False


def _raster_has_data(layer) -> bool | None:
    """The bounded band-1 sample Processing's raster check takes (tools/postconditions.py)."""
    try:
        from ..tools.postconditions import _has_any_data

        return _has_any_data(layer)
    except Exception:  # noqa: BLE001 - no sample is not an empty raster
        return None


def _added_layer(project, raw: dict, orphans: set, view, hidden: frozenset = frozenset(),
                 spoken: frozenset = frozenset()) -> tuple[dict | None, list, list]:
    """One ``layers_added`` entry with the warnings and empty outputs it earned."""







    lid = str(raw.get("id") or "")
    name = str(raw.get("name") or lid or "?")
    layer = project.mapLayer(lid)
    if layer is None:
        return None, [], []
    item: dict = {"id": lid, "name": name, "kind": _layer_kind(layer), "crs": _crs_name(layer)}
    warnings: list[str] = []
    empty: list[str] = []

    extent = None
    try:
        extent = layer.extent()
        if extent is not None and extent.isEmpty():
            extent = None
    except Exception:  # noqa: BLE001 - an extent that will not read is left out, not guessed
        extent = None

    count = _feature_count(layer) if isinstance(layer, QgsVectorLayer) else None
    if count is not None:
        item["features"] = count
        item["empty"] = count == 0
        if count == 0:
            warnings.append(f"{name!r} was added with 0 features.")
            empty.append(name)
    if item["crs"] == "":
        warnings.append(f"{name!r} has no valid CRS.")

    sampled = None
    try:
        from ..tools.postconditions import _invalid_geometries

        sampled = _invalid_geometries(layer)
    except Exception:  # noqa: BLE001 - a missing sample is not a reason to lose the layer
        sampled = None
    if sampled is not None:
        item["invalid_geometries"] = sampled
        if sampled.get("invalid"):
            warnings.append(
                f"{sampled['invalid']} of {sampled['sampled']} sampled geometries of {name!r} are invalid.")

    if view is not None:
        seen = _in_view(layer, extent, view)
        if seen is not None:
            item["in_view"] = seen
            if not seen:
                warnings.append(f"{name!r} is outside the current map view.")

    if count is not None and count > 0 and extent is None:
        warnings.append(f"{name!r} has features but no extent, so nothing of it can be drawn.")

    if lid not in spoken and _local_raster(layer) and _raster_has_data(layer) is False:
        item["empty"] = True
        warnings.append(f"{name!r} was added with no data: every sampled pixel is nodata.")
        empty.append(name)

    node = None if lid in orphans else project.layerTreeRoot().findLayer(lid)
    item["in_tree"] = node is not None
    if node is None:
        warnings.append(f"{name!r} is not in the layer tree, so the user cannot see it.")
    else:
        visible = _switched_on(node)
        if visible is not None:
            item["visible"] = visible
            if not visible and lid not in hidden:
                warnings.append(f"{name!r} is switched off in the layer tree, so the map does not draw it.")

    path = layer_file_path(layer)
    if path:
        item["file"] = path
        try:
            item["size_bytes"] = int(os.path.getsize(path))
        except OSError:  # noqa: BLE001 - the file can vanish between the path and its size
            pass
    return item, warnings, empty


def _changed_layers(diff: dict) -> tuple[list, list, list]:
    """The merged ``layers_changed`` entries, their warnings and emptied names."""




    merged: dict[str, dict] = {}
    order: list[str] = []
    warnings: list[str] = []
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
            warnings.append(f"{item['name']!r} went from {before} features to 0.")
            empty.append(item["name"])
    for raw in _listed(diff.get("crs_changes")):
        if not isinstance(raw, dict):
            continue
        item = item_for(raw)
        if "before" in raw:
            item["crs_before"] = raw["before"]
        if "after" in raw:
            item["crs_after"] = raw["after"]
    return [merged[lid] for lid in order], warnings, empty


def _stopped_output():
    """``path -> bool``: whether a task Stop cancelled was writing ``path``."""







    try:
        from ..tools.processing_tools import canceled_output
    except Exception:  # noqa: BLE001 - no Processing (a stripped test tree): no task was cancelled
        return lambda _path: False
    return canceled_output


def _list_files(paths: list) -> list:
    """The file entries, deduped by real path and capped at MAX_FILES."""




    entries: list = []
    seen: set = set()
    for path in paths:
        if len(entries) >= MAX_FILES:
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
    """The call warnings that still describe the project, as sentences."""





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
        out.append(entry["text"])
    return out


def _changed_view(project, items: list, view, hidden: frozenset) -> list[str]:
    """In view and switched on, for the layers the run changed: facts on each entry, and warnings."""



    warnings: list[str] = []
    root = project.layerTreeRoot()
    for item in items[:MAX_LAYERS]:
        lid, name = str(item.get("id") or ""), item.get("name")
        layer = project.mapLayer(lid) if lid else None
        if layer is None:
            continue
        try:
            if view is not None:
                seen = _in_view(layer, layer.extent(), view)
                if seen is not None:
                    item["in_view"] = seen
                    if not seen:
                        warnings.append(f"{name!r} changed but is outside the current map view.")
            node = root.findLayer(lid)
            visible = _switched_on(node) if node is not None else None
            if visible is not None:
                item["visible"] = visible
                if not visible and lid not in hidden:
                    warnings.append(
                        f"{name!r} changed but is switched off in the layer tree, so the map does not draw it.")
        except Exception:  # noqa: BLE001  # nosec B112 - a layer that will not read keeps its counts, without these facts
            continue
    return warnings


def _working_copies(entries) -> tuple[list, list]:
    """The working copies still in the project, as report entries and one warning line each."""
    project = QgsProject.instance()
    copies: list = []
    lines: list[str] = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        lid = str(entry.get("layer_id") or entry.get("id") or "")
        layer = project.mapLayer(lid) if lid else None
        if layer is None:
            continue
        name = str(layer.name() or entry.get("name") or lid)
        copies.append({"id": lid, "name": name})
        lines.append(f"{name!r} only fed a later step and is still in the project: "
                     "remove it unless the user asked to keep it.")
    return copies, lines


def _joined_warnings(own: list, calls: list, copies: list = ()) -> list:
    """The report's own facts first, then what the calls said, then the working copies, each sentence once."""
    return list(dict.fromkeys([*own, *calls, *copies]))[:MAX_WARNINGS]


def _build_report(snapshot, written, view, call_warnings=(), diff=None, working=(), hidden=()) -> dict:
    if view is None:

        view = _view()
    report = _empty_report()
    warnings: list[str] = []
    empty_outputs: list[str] = []
    copies, copy_lines = _working_copies(working)
    report["working_copies"] = copies

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
        for raw in _listed(diff.get("layers_added"))[:MAX_LAYERS]:
            if not isinstance(raw, dict):
                continue
            try:
                item, layer_warnings, layer_empty = _added_layer(project, raw, orphans, view, switched_off, spoken)
            except Exception:  # noqa: BLE001  # nosec B112 - one unreadable layer is skipped, the rest reported
                continue
            if item is None:
                continue
            report["layers_added"].append(item)
            warnings.extend(layer_warnings)
            empty_outputs.extend(layer_empty)
        changed_items, changed_warnings, changed_empty = _changed_layers(diff)
        report["layers_changed"] = changed_items
        warnings.extend(changed_warnings)
        warnings.extend(_changed_view(project, changed_items, view, switched_off))
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
    for entry in report["files_written"]:
        if not entry["exists"]:
            warnings.append(f"{os.path.basename(entry['path'])} was not written: the file does not exist.")
        elif entry.get("size_bytes") == 0:
            warnings.append(f"{os.path.basename(entry['path'])} is empty (0 bytes).")
    if diff is None:


        report["warnings"] = _joined_warnings(warnings, _standing_call_warnings(call_warnings, report), copy_lines)
        report["changed"] = any(entry["exists"] for entry in report["files_written"])
        report["counts"] = _counts(report)
        return report

    report["empty_outputs"] = list(dict.fromkeys(empty_outputs))
    report["warnings"] = _joined_warnings(warnings, _standing_call_warnings(call_warnings, report), copy_lines)
    report["changed"] = bool(diff_changed(diff) or any(entry["exists"] for entry in report["files_written"]))
    report["counts"] = _counts(report)
    return report


def written_paths(name: str, args, result) -> list[str]:
    """The local files a successful call wrote, in first-seen order."""










    try:
        candidates: list = []
        declared: tuple = ()
        try:
            from ..tools import guards

            declared = tuple(guards.WRITE_PATH_ARGS.get(name, ()))
            try:
                candidates = list(guards._write_targets(name, args))
            except Exception:  # noqa: BLE001 - the table names the same write-path arguments
                candidates = [args.get(key) for key in declared]
        except Exception:  # noqa: BLE001 - no guards in a stripped test tree: no names at all
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
    except Exception:  # noqa: BLE001 - a call whose arguments cannot be read wrote nothing nameable
        return []


def _sentence(text) -> str:
    """One warning on one line, starting with a capital: the checks write fragments."""
    line = " ".join(str(text or "").split())
    return line[:1].upper() + line[1:]


def _named_layers(result: dict) -> tuple[list[str], list[str]]:
    """The ids of the layers a result names (its own and its Processing outputs), and their names."""
    ids: list[str] = []
    names: list[str] = []
    entries = [result]
    outputs = result.get("outputs")
    if isinstance(outputs, dict):
        entries.extend(outputs.values())
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
    """A ``verified`` warning about the column the call wrote rather than about the layer."""
    return verified.get("field_present") is False or bool(verified.get("sampled") and not verified.get("non_null"))


def _warnings_of(name: str, result, found: list) -> None:
    if not isinstance(result, dict) or "_error" in result:
        return
    ids, names = _named_layers(result)
    subject = repr(names[0]) if len(names) == 1 else str(result.get("algorithm") or name)

    def add(text, about: str = "", lead: bool = True) -> None:
        sentence = _sentence(text)
        if sentence:
            found.append({"text": f"{subject}: {sentence}" if lead else sentence,
                          "layers": list(ids), "about": about})

    checks = result.get("checks")
    if isinstance(checks, dict) and isinstance(checks.get("warnings"), (list, tuple)):
        for text in checks["warnings"]:
            add(text)
    verified = result.get("verified")
    if isinstance(verified, dict) and verified.get("warning"):


        restated = verified.get("present") is True and "removed" not in verified and not _field_fault(verified)
        add(verified["warning"], "empty" if restated else "", lead=False)
    if name in _WARNING_TOOLS and result.get("warning"):
        add(result["warning"])
    if name == _BATCH_TOOL:
        for item in result.get("results") or []:
            if isinstance(item, dict):
                _warnings_of(str(item.get("name") or ""), item.get("result"), found)


def call_warnings(name: str, result) -> list[dict]:
    """The post-condition warnings one finished call reported, for the run's report."""











    found: list[dict] = []
    try:
        _warnings_of(str(name or ""), result, found)
    except Exception:  # noqa: BLE001  # nosec B110 - a warning that cannot be read does not cost the call its others
        return found
    return found


def build_report(snapshot: RunSnapshot | None, written: Iterable[str] = (), view=None,
                 call_warnings: Iterable[dict] = (), diff: dict | None = None,
                 working_copies: Iterable[dict] = (), hidden: Iterable[str] = ()) -> dict:
    """What the run left in the project, as plain dicts and numbers."""











    try:
        return _build_report(snapshot, written, view, tuple(call_warnings or ()), diff,
                             tuple(working_copies or ()), tuple(hidden or ()))
    except Exception as exc:  # noqa: BLE001 - the report is never the reason a run fails
        report = _empty_report()
        report["error"] = f"{type(exc).__name__}: {exc}"[:200]
        return report


def _outside_a_run(args: dict) -> dict:
    """The answer when the call did not come from the executor inside a run."""
    return tool_error(
        "verify_run is answered by the executor inside a run.",
        "INVALID_ARGS",
        "Do not call it yourself; the server asks for it before the final answer.",
    )


def register_verify_run_tool(registry: ToolRegistry) -> None:
    """Register the read the server sends itself once before the final answer."""





    registry.register(Tool(
        name=VERIFY_RUN,
        input_schema={"type": "object", "properties": {}},
        handler=_outside_a_run,
        description=("Read what the current run left in the project: layers added and changed with "
                     "feature counts, CRS, a geometry validity sample, whether each new layer is in "
                     "the map view, and files written with their size."),
        danger="read",
    ))

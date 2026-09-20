# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import os
import re

from qgis.core import QgsTask

from ..core import layer_order, tuning
from ..core.host_platform import remove_quietly
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from . import kml_description



MERGE_EXTENSIONS = (".kml", ".kmz", ".shp", ".geojson", ".json", ".gml", ".gpx", ".fgb", ".tab", ".mif")


LIST_AT_MOST = 10

_FILES_PER_COMMIT = 200


_MAX_DEPTH = 4

_MAX_SEEN = 200_000
_FAMILIES = ("points", "lines", "polygons")


def _max_depth() -> int:
    return tuning.ceiling("vector_merge_max_depth", _MAX_DEPTH, 1)
_DESCRIPTION = "description"

_KML_RENDERING = frozenset({"altitudemode", "tessellate", "extrude", "visibility", "draworder", "icon"})
_SAMPLE = 20



_RESERVED = frozenset({"fid", "geom"})


def _ogr():
    from osgeo import gdal, ogr, osr

    return gdal, ogr, osr


def folder_files(folder: str) -> tuple[dict, int]:





    found: dict[str, list] = {}
    seen = 0
    stack = [(folder, 0)]
    max_seen = tuning.ceiling("vector_merge_max_seen", _MAX_SEEN, 10_000)
    max_depth = _max_depth()
    while stack and seen < max_seen:
        current, depth = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda e: e.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith((".", "__MACOSX")):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue
            if is_dir:
                if depth + 1 <= max_depth and not entry.name.lower().endswith(".gdb"):
                    stack.append((entry.path, depth + 1))
                continue
            seen += 1
            ext = os.path.splitext(entry.name)[1].lower()
            if ext in MERGE_EXTENSIONS:
                found.setdefault(ext, []).append(entry.path)
    for paths in found.values():
        paths.sort(key=lambda p: (p.count(os.sep), p.lower()))
    return found, seen


def _family(ogr, geometry_type: int) -> str | None:
    flat = ogr.GT_Flatten(geometry_type)
    if flat in (ogr.wkbPoint, ogr.wkbMultiPoint):
        return "points"
    if flat in (ogr.wkbLineString, ogr.wkbMultiLineString, ogr.wkbCircularString, ogr.wkbCompoundCurve,
                ogr.wkbMultiCurve):
        return "lines"
    if flat in (ogr.wkbPolygon, ogr.wkbMultiPolygon, ogr.wkbCurvePolygon, ogr.wkbMultiSurface):
        return "polygons"
    return None


def _parts(ogr, geometry) -> dict:

    flat = ogr.GT_Flatten(geometry.GetGeometryType())
    family = _family(ogr, flat)
    if family is not None:
        return {family: geometry}
    if flat != ogr.wkbGeometryCollection:
        return {}
    split: dict = {}
    for index in range(geometry.GetGeometryCount()):
        for name, part in _parts(ogr, geometry.GetGeometryRef(index)).items():
            split.setdefault(name, []).append(part)
    out = {}
    for name, parts in split.items():
        collected = ogr.Geometry(ogr.wkbGeometryCollection)
        for part in parts:
            collected.AddGeometry(part)
        out[name] = collected
    return out


def _as_multi(ogr, family: str, geometry):
    force = {"points": ogr.ForceToMultiPoint, "lines": ogr.ForceToMultiLineString,
             "polygons": ogr.ForceToMultiPolygon}[family]
    return force(geometry.GetLinearGeometry() if geometry.HasCurveGeometry() else geometry)


class _Family:






    def __init__(self, layer, is_3d: bool):
        self.layer = layer
        self.is_3d = is_3d

        self.fields: dict[str, list] = {}
        self.order: list[str] = []
        self.taken: set[str] = set(_RESERVED)


        self.reserved: set[str] = set()
        self.columns = kml_description.Columns(self.taken)
        self.parsed: set[str] = set()
        self.count = 0

    def _create(self, ogr, name: str, field_type: int, width: int = 0) -> None:
        definition = ogr.FieldDefn(name, field_type)
        if width and field_type == ogr.OFTString:
            definition.SetWidth(width)
        self.layer.CreateField(definition)
        self.order.append(name)

    def own(self, ogr, name: str) -> None:

        self.taken.add(name.casefold())
        self._create(ogr, name, ogr.OFTString)

    def reserve(self, name: str) -> None:
        key = name.casefold()
        if key not in self.fields and key not in self.taken:
            self.taken.add(key)
            self.reserved.add(key)

    def field(self, ogr, name: str, field_type: int, width: int = 0) -> str:

        key = name.casefold()
        if key in self.fields:
            return self.fields[key][0]
        if key in self.reserved:
            self.reserved.discard(key)
            column = name
        else:
            column, number = name, 2
            while column.casefold() in self.taken:
                column, number = f"{name}_{number}", number + 1
            self.taken.add(column.casefold())
        self._create(ogr, column, field_type, width)
        self.fields[key] = [column, field_type]
        return column

    def parsed_column(self, ogr, name: str) -> str:

        if name not in self.parsed:
            self._create(ogr, name, ogr.OFTString)
            self.parsed.add(name)
        return name

    def widen(self, ogr, key: str, field_type: int) -> bool:






        column, current = self.fields[key]
        integers = (ogr.OFTInteger, ogr.OFTInteger64)
        if current in integers and field_type in integers:
            wider = ogr.OFTInteger64
        elif current in integers + (ogr.OFTReal,) and field_type in integers + (ogr.OFTReal,):
            wider = ogr.OFTReal
        else:
            wider = ogr.OFTString
        if wider != current:
            index = self.layer.GetLayerDefn().GetFieldIndex(column)
            self.layer.AlterFieldDefn(index, ogr.FieldDefn(column, wider), ogr.ALTER_TYPE_FLAG)
            self.fields[key][1] = wider
        return wider == ogr.OFTString


def write(sources: list, gpkg: str, *, provenance: bool, root: str = "",
          is_canceled=lambda: False, progress=lambda _value: None) -> dict:











    gdal, ogr, osr = _ogr()
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        return _write(gdal, ogr, osr, sources, gpkg, provenance, root, is_canceled, progress)
    finally:
        gdal.PopErrorHandler()


def _write(gdal, ogr, osr, sources, gpkg, provenance, root, is_canceled, progress) -> dict:
    stage = os.path.splitext(gpkg)[0] + "-stage.gpkg"
    try:
        out = ogr.GetDriverByName("GPKG").CreateDataSource(stage)
    except RuntimeError as exc:
        out, reason = None, str(exc)
    else:
        reason = gdal.GetLastErrorMsg()
    if out is None:
        return {"_error": f"Could not create {stage}: {reason or 'GDAL refused it'}"}
    state = {"stem": os.path.splitext(os.path.basename(gpkg))[0], "families": {}, "target": None,
             "no_geometry": 0, "conflicts": set(), "provenance": provenance, "written": []}
    families: dict[str, _Family] = state["families"]
    unreadable: list[str] = []
    read = 0
    out.StartTransaction()
    for number, (path, wanted) in enumerate(sources, 1):
        if is_canceled():
            out.CommitTransaction()
            out = None
            remove_quietly(stage)
            return {"canceled": True}
        relative = os.path.relpath(path, root) if root else os.path.basename(path)
        state["written"] = []
        try:


            _read_file(ogr, osr, out, path, wanted, relative, state)
            read += 1
        except Exception:  # noqa: BLE001

            for family, fid in state["written"]:
                try:
                    family.layer.DeleteFeature(fid)
                    family.count -= 1
                except Exception:  # noqa: BLE001  # nosec B110
                    pass
            unreadable.append(path)
        if number % _FILES_PER_COMMIT == 0:
            out.CommitTransaction()
            out.StartTransaction()
        progress(number * 90.0 / max(1, len(sources)))
    out.CommitTransaction()
    tables = [(name, families[name]) for name in _FAMILIES if name in families and families[name].count]
    out = None
    if not tables:
        remove_quietly(stage)
        return {"layers": [], "files": len(sources), "read": read, "unreadable": unreadable,
                "no_geometry": state["no_geometry"], "description_fields": [],
                "type_conflicts": sorted(state["conflicts"])}
    added = _finalise(gdal, ogr, stage, gpkg, tables)
    remove_quietly(stage)
    if isinstance(added, dict):
        return added
    progress(100.0)
    description_fields = []
    for _name, family in tables:
        for column in family.columns.names.values():
            if column in family.parsed and column not in description_fields:
                description_fields.append(column)
    return {"layers": added, "files": len(sources), "read": read, "unreadable": unreadable,
            "no_geometry": state["no_geometry"], "description_fields": description_fields,
            "description_kept": any(f.columns.keeps_raw() for _n, f in tables if f.parsed),
            "type_conflicts": sorted(state["conflicts"])}


def _read_file(ogr, osr, out, path: str, wanted, relative: str, state: dict) -> None:

    families = state["families"]
    kml = path.lower().endswith((".kml", ".kmz"))
    source = ogr.Open(path)
    if source is None:
        raise RuntimeError(f"cannot open {path}")
    layers = [source.GetLayerByName(wanted)] if wanted else [source.GetLayer(i)
                                                             for i in range(source.GetLayerCount())]
    for layer in layers:
        if layer is None:
            continue
        srs = layer.GetSpatialRef()
        if kml:
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(4326)
        if srs is not None:
            srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if state["target"] is None and srs is not None:
            state["target"] = srs.Clone()
        target = state["target"]
        transform = None
        if srs is not None and target is not None and not srs.IsSame(target):
            transform = osr.CoordinateTransformation(srs, target)
        definition = layer.GetLayerDefn()
        names = [definition.GetFieldDefn(i) for i in range(definition.GetFieldCount())]
        description = next((i for i, f in enumerate(names) if f.GetName().lower() == _DESCRIPTION), None)
        layer.ResetReading()
        for feature in layer:
            geometry = feature.GetGeometryRef()
            if geometry is None or geometry.IsEmpty():
                state["no_geometry"] += 1
                continue
            geometry = geometry.Clone()
            if transform is not None:
                try:
                    moved = geometry.Transform(transform) == 0
                except RuntimeError:
                    moved = False
                if not moved:
                    state["no_geometry"] += 1
                    continue
            for family_name, part in _parts(ogr, geometry).items():
                family = families.get(family_name)
                if family is None:
                    kind = {"points": ogr.wkbMultiPoint, "lines": ogr.wkbMultiLineString,
                            "polygons": ogr.wkbMultiPolygon}[family_name]
                    is_3d = part.Is3D()
                    created = out.CreateLayer(f"{state['stem']}_{family_name}", target,
                                              ogr.GT_SetZ(kind) if is_3d else kind,
                                              ["GEOMETRY_NAME=geom", "SPATIAL_INDEX=YES"])
                    family = families[family_name] = _Family(created, is_3d)
                    if state["provenance"]:
                        family.own(ogr, "source_file")
                        family.own(ogr, "source_layer")
                _write_feature(ogr, family, family_name, part, feature, names, description if kml else None,
                               state, relative if state["provenance"] else None, layer.GetName(), kml)


def _write_feature(ogr, family, family_name, geometry, feature, names, description, state, relative, layer_name,
                   kml):
    geometry = _as_multi(ogr, family_name, geometry)
    if family.is_3d and not geometry.Is3D():
        geometry.Set3D(True)
    elif not family.is_3d and geometry.Is3D():
        geometry.FlattenTo2D()
    if geometry.IsMeasured():
        geometry.SetMeasured(False)


    values = [("source_file", relative), ("source_layer", layer_name)] if relative is not None else []
    lists = (ogr.OFTIntegerList, ogr.OFTInteger64List, ogr.OFTRealList, ogr.OFTStringList)
    if kml:
        for definition in names:
            if definition.GetName().lower() not in _KML_RENDERING:
                family.reserve(definition.GetName())
    for index, definition in enumerate(names):
        name = definition.GetName()
        if kml and name.lower() in _KML_RENDERING:
            continue
        field_type = definition.GetType()
        if field_type == ogr.OFTBinary:
            continue
        if field_type in lists:
            field_type = ogr.OFTString
        if not feature.IsFieldSetAndNotNull(index):


            if index != description and not kml:
                family.field(ogr, name, field_type, definition.GetWidth())
            continue
        column = family.field(ogr, name, field_type, definition.GetWidth())
        key = name.casefold()
        if family.fields[key][1] != field_type and family.widen(ogr, key, field_type):
            state["conflicts"].add(column)
        if definition.GetType() in lists:
            values.append((column, ", ".join(str(item) for item in feature.GetField(index) or [])))
        elif family.fields[key][1] == ogr.OFTString:
            values.append((column, feature.GetFieldAsString(index)))
        else:
            values.append((column, feature.GetField(index)))
    if description is not None and feature.IsFieldSetAndNotNull(description):
        for column, value in family.columns.read(feature.GetField(description)).items():
            family.parsed_column(ogr, column)
            if value is not None:
                values.append((column, value))
    row = ogr.Feature(family.layer.GetLayerDefn())
    row.SetGeometry(geometry)
    for name, value in values:
        row.SetField(name, value)
    family.layer.CreateFeature(row)
    state["written"].append((family, row.GetFID()))
    row = None
    family.count += 1


def _finalise(gdal, ogr, stage: str, gpkg: str, tables: list):

    added = []
    for position, (family_name, family) in enumerate(tables):
        table = f"{os.path.splitext(os.path.basename(gpkg))[0]}_{family_name}"
        parsed = family.parsed
        raw = (family.fields.get(_DESCRIPTION) or [None])[0]
        drop_raw = bool(parsed) and not family.columns.keeps_raw()
        columns = []
        for name in family.order:
            if drop_raw and name == raw:
                continue
            quoted = '"' + name.replace('"', '""') + '"'
            kind = family.columns.kind(name) if name in parsed else "text"
            if kind == "int":
                columns.append(f"CAST({quoted} AS INTEGER) AS {quoted}")
            elif kind == "float":
                columns.append(f"CAST({quoted} AS REAL) AS {quoted}")
            else:
                columns.append(quoted)
# nosec B608
        sql = f'SELECT geom{"".join(", " + c for c in columns)} FROM "{table}"'  # nosec B608
        dimension = "XYZ" if family.is_3d else "XY"
        geometry_type = {"points": "MULTIPOINT", "lines": "MULTILINESTRING", "polygons": "MULTIPOLYGON"}[family_name]
        try:
            options = gdal.VectorTranslateOptions(
                format="GPKG", SQLStatement=sql, SQLDialect="SQLITE", layerName=table,
                geometryType=geometry_type, dim=dimension, accessMode="update" if position else None)
            written = gdal.VectorTranslate(gpkg, stage, options=options)
        except Exception as exc:  # noqa: BLE001
            return {"_error": f"Could not write {os.path.basename(gpkg)}: {exc}"}
        if written is None:
            return {"_error": f"Could not write {os.path.basename(gpkg)}: {gdal.GetLastErrorMsg()}"}
        written = None
        added.append({"family": family_name, "table": table, "count": family.count})
    return added





def _safe_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "merged"))[:60].strip("._-") or "merged"
    return f"merged_{stem}" if stem.upper() in {"CON", "PRN", "AUX", "NUL"} else stem


class FolderMergeTask(QgsTask):


    def __init__(self, folder: str, paths: list, name: str):
        super().__init__(f"AI Agent: merge {len(paths)} files", enum_member(QgsTask, "Flag", "CanCancel"))
        self.folder = folder
        self.paths = paths
        self.name = name
        self.gpkg = os.path.join(create_managed_temp_dir("merged"), _safe_stem(name) + ".gpkg")
        self.outcome: dict = {}
        self.entry: dict | None = None
        self.run_token = layer_order.current_run()

    def run(self) -> bool:
        try:
            self.outcome = write([(path, None) for path in self.paths], self.gpkg, provenance=True,
                                 root=self.folder, is_canceled=self.isCanceled, progress=self.setProgress)
        except Exception as exc:  # noqa: BLE001
            self.outcome = {"_error": f"The merge stopped: {exc}"}
        return not self.outcome.get("canceled")

    def finished(self, ok: bool) -> None:
        entry = self.entry
        if entry is None or entry.get("status") == "canceled":
            return
        if not ok or self.outcome.get("canceled"):
            entry["status"] = "canceled"
            remove_quietly(self.gpkg)
            return
        if self.outcome.get("_error"):
            entry.update({"status": "error", "error": self.outcome["_error"], "code": "EXECUTION_FAILED",
                          "suggestion": "Pass one file of the folder to add_data to see why it does not read."})
            return
        try:
            with layer_order.adopted(self.run_token):
                entry.update(merge_report(self.outcome, self.gpkg, self.name, self.folder))
        except Exception as exc:  # noqa: BLE001
            log_warning(f"folder merge: {exc}")
            entry.update({"status": "error", "error": str(exc), "code": "EXECUTION_FAILED"})


def merge_report(outcome: dict, gpkg: str, name: str, folder: str) -> dict:

    from .processing_tools import _process_outputs

    layers = outcome.get("layers") or []
    if not layers:
        return {"status": "error", "code": "INVALID_ARGS",
                "error": (f"None of the {outcome.get('files', 0)} files gave a feature with a geometry "
                          f"({len(outcome.get('unreadable') or [])} could not be read).")}
    uris = {entry["family"]: f"{gpkg}|layername={entry['table']}" for entry in layers}
    outputs = _process_outputs(uris if len(uris) > 1 else {"OUTPUT": next(iter(uris.values()))}, output_name=name)
    unreadable = outcome.get("unreadable") or []
    merged = {"files": outcome.get("files", 0), "files_read": outcome.get("read", 0),
              "features": sum(entry["count"] for entry in layers), "folder": folder,
              "row_origin_fields": ["source_file", "source_layer"]}
    if unreadable:
        merged["unreadable_count"] = len(unreadable)
        merged["unreadable"] = [os.path.relpath(p, folder) for p in unreadable[:_SAMPLE]]
    if outcome.get("no_geometry"):
        merged["features_without_geometry"] = outcome["no_geometry"]
    if outcome.get("description_fields"):
        merged["description_fields"] = outcome["description_fields"]
        merged["description_kept"] = bool(outcome.get("description_kept"))
    if outcome.get("type_conflicts"):
        merged["type_conflicts"] = outcome["type_conflicts"]
    outputs["merge"] = merged
    report = {"status": "complete", "progress": 100, "outputs": outputs,
              "feature_count": merged["features"], "files_written": [gpkg]}
    if unreadable:
        report["warning"] = (f"{len(unreadable)} of {merged['files']} files could not be read and are not in "
                             "the merged layer; outputs.merge.unreadable names them.")
    return report


def start_folder_merge(folder: str, paths: list, name: str) -> dict:
    from .processing_tools import register_task

    task = FolderMergeTask(folder, paths, name)

    def connect(_task_id: str, entry: dict) -> None:
        task.entry = entry

    task_id, _entry = register_task(task, "add_data (folder merge)", connect=connect)
    return {
        "task_id": task_id,
        "status": "running",
        "algorithm": "add_data (folder merge)",
        "note": (f"Merging {len(paths)} files into one GeoPackage in the background, one layer per geometry "
                 "type; QGIS stays responsive. Poll get_task_status(task_id)."),
        "poll": {"tool": "get_task_status", "args": {"task_id": task_id}, "interval_s": 2.0,
                 "label": f"Merging {len(paths)} files"},
    }


def split_description(path: str, sublayer: str | None, name: str) -> dict | None:








    _gdal, ogr, _osr = _ogr()
    try:
        source = ogr.Open(path)
    except RuntimeError:
        return None
    if source is None:
        return None
    if sublayer:
        layer = source.GetLayerByName(sublayer)
    else:
        layer = source.GetLayer(0) if source.GetLayerCount() == 1 else None
    index = -1 if layer is None else layer.GetLayerDefn().GetFieldIndex(_DESCRIPTION)
    probe = kml_description.Columns()
    if index >= 0:
        for number, feature in enumerate(layer):
            if feature.IsFieldSetAndNotNull(index):
                probe.read(feature.GetField(index))
            if number >= 49:
                break
    table = layer.GetName() if layer is not None else None
    layer = source = None
    if not probe.worth_it():
        return None
    gpkg = os.path.join(create_managed_temp_dir("kml"), _safe_stem(name) + ".gpkg")
    outcome = write([(path, table)], gpkg, provenance=False)
    if (outcome.get("_error") or outcome.get("unreadable") or outcome.get("read") != 1
            or len(outcome.get("layers") or []) != 1 or not outcome["layers"][0]["count"]
            or not outcome.get("description_fields")):
        remove_quietly(gpkg)
        return None
    return {"uri": f"{gpkg}|layername={outcome['layers'][0]['table']}", "gpkg": gpkg,
            "description_fields": outcome["description_fields"],
            "description_kept": bool(outcome.get("description_kept"))}


def split_note(split: dict) -> str:
    fields = split["description_fields"]
    shown = ", ".join(fields[:12]) + (f" and {len(fields) - 12} more" if len(fields) > 12 else "")
    return (f"The KML kept its attributes as an HTML table inside description; they are now {len(fields)} real "
            f"fields ({shown}) in a GeoPackage copy, numbers typed. The raw description "
            + ("stays, since some descriptions say more than their table." if split["description_kept"]
               else "was dropped.") + " The original file is unchanged.")


def folder_plan(folder: str, wanted: str) -> tuple[str, object]:

    found, seen = folder_files(folder)
    total = sum(len(paths) for paths in found.values())
    counts = {ext.lstrip("."): len(paths) for ext, paths in sorted(found.items(), key=lambda kv: -len(kv[1]))}
    wanted = str(wanted or "").strip()
    if wanted:
        ext = "." + wanted.lower().lstrip("*.")
        if ext in found:
            paths = found[ext]
            return ("load", paths[0]) if len(paths) == 1 else ("merge", paths)
        every = [path for paths in found.values() for path in paths]

        named = [path for path in every
                 if wanted.casefold() in (os.path.relpath(path, folder).casefold(), os.path.basename(path).casefold(),
                                          os.path.splitext(os.path.basename(path))[0].casefold())]
        if named:
            return "load", named[0]
        return "answer", {"_error": f"Nothing in {os.path.basename(folder)} matches layer={wanted!r}.",
                          "code": "INVALID_ARGS", "formats": counts,
                          "suggestion": "Pass layer=<extension> to merge every file of that format, or a file name."}
    if total == 0:
        return "answer", {"_error": (f"{os.path.basename(folder) or folder} holds no vector file add_data reads "
                                     f"({seen} files looked at, {_max_depth()} folder levels deep)."),
                          "code": "INVALID_ARGS",
                          "suggestion": ("A GeoPackage, a CAD drawing or a raster in it loads by its own path; "
                                         "find_local_data searches by name.")}
    if total == 1:
        return "load", next(iter(found.values()))[0]
    if len(found) == 1 and total > LIST_AT_MOST:
        return "merge", next(iter(found.values()))
    listed = [os.path.relpath(path, folder) for paths in found.values() for path in paths][:50]
    return "answer", {
        "path": folder, "formats": counts, "files": listed,
        "_note": (f"{os.path.basename(folder) or folder} holds {total} vector files, none added yet. Call add_data "
                  "with layer=<file> for one of them, or layer=<extension> (for example layer='kml') to merge every "
                  "file of that format into one layer, each row keeping the file it came from."),
    }

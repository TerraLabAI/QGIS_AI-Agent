# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





from __future__ import annotations

import os
import re
import time
import unicodedata
import uuid

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsWkbTypes,
)

from ..core import ground, limits
from ..core.background import (
    delete_here,
    hand_over,
    on_main_thread,
    run_on_main_thread,
    still_awaited,
    take,
)
from ..core.crs_ref import crs_ref
from ..core.host_platform import IS_WINDOWS, release_pooled_handles, remove_quietly, retry_file_op
from ..core.layer_order import feature_count_of, is_remote_vector
from ..core.logger import log_warning
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from ..core.tool_registry import coded_fact, tool_error
from . import vector_write
from ._compat import FIELD_TYPES
from .csv_loader import CSV_EXTENSIONS, add_csv
from .data_common import built_here, worker_options
from .layer_lookup import _find_layer, _layer_not_found_error
from .missing_file import missing_file

_CONTAINER_EXTENSIONS = (".gpkg", ".sqlite", ".gdb", ".kml", ".kmz", ".gml", ".gpx", ".dxf", ".vrt")











_NOT_AWAITED = {"_error": "The call this layer was read for had already ended; the layer was not added.",
                "code": "TIMEOUT"}


def _checked_path(path: str) -> str | None:

    return run_on_main_thread(validate_path, path, False, timeout=60)


def _on_main_with(built, build_on_main, add) -> dict:







    def _main():
        layer = take(built)
        if layer is None:
            layer = build_on_main()
        try:
            return add(layer)
        finally:
            if QgsProject.instance().mapLayer(layer.id()) is not layer:
                delete_here(layer)

    try:
        return run_on_main_thread(_main, timeout=60)
    finally:
        if built is not None:
            built.release()


def _virtual_source_path(path: str) -> str | None:

    if not path.startswith("/vsizip/"):
        return None
    match = re.match(r"^/vsizip/(.+?\.zip)(?:/.*)?$", path, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _source_exists(path: str) -> bool:





    if not path.lower().startswith("/vsi"):
        return os.path.exists(path)
    try:
        from osgeo import gdal

        return gdal.VSIStatL(path) is not None
    except Exception:  # noqa: BLE001
        return True


def _sublayer_names(path: str) -> list:

    try:
        from qgis.core import QgsProviderRegistry
        details = QgsProviderRegistry.instance().querySublayers(path)
        names = [d.name() for d in details if d.providerKey() == "ogr" and d.name()]
        return list(dict.fromkeys(names))
    except Exception:  # noqa: BLE001
        return []


def container_layers(source: str) -> list | None:






    from .data_inspect import _SINGLE_LAYER_EXTENSIONS

    low = source.lower()
    if "|" in source:
        return None
    if low.endswith(_CONTAINER_EXTENSIONS):
        return _sublayer_names(source)
    if low.endswith(_SINGLE_LAYER_EXTENSIONS):
        return None
    names = _sublayer_names(source)
    return names if len(names) > 1 else None


def _zip_names(path: str) -> tuple[list, str]:

    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            return archive.namelist(), ""
    except (OSError, zipfile.BadZipFile) as exc:
        return [], str(exc) or type(exc).__name__


def _is_sidecar(name: str, lowered: set) -> bool:

    stem = name.lower()
    while True:
        stem, ext = os.path.splitext(stem)
        if not ext:
            return False
        if stem in lowered:
            return True



_SAME_FORMAT_PROOF = 20


def _vector_driver(gdal, path: str) -> str:

    try:
        driver = gdal.IdentifyDriverEx(path, gdal.OF_VECTOR)
        if driver is None:
            return ""
        if gdal.IdentifyDriverEx(path, gdal.OF_RASTER) is not None:
            opened = gdal.OpenEx(path, gdal.OF_VECTOR)
            if opened is None or not opened.GetLayerCount():
                return ""
        return str(driver.ShortName)
    except Exception:  # noqa: BLE001
        return ""


def archive_datasets(names: list, gdal_path) -> tuple[list, list]:















    try:
        from osgeo import gdal
    except ImportError:
        return [], []
    files = [name for name in names
             if not name.endswith("/") and not name.startswith("__MACOSX/")
             and not os.path.basename(name).startswith("._")
             and not any(part.lower().endswith(".gdb") for part in name.split("/")[:-1])]
    lowered = {name.lower() for name in files}
    found: dict = {}
    verdicts: dict = {}

    readdir = gdal.GetThreadLocalConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", None)
    gdal.SetThreadLocalConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
    gdal.PushErrorHandler("CPLQuietErrorHandler")
    try:
        for name in files:
            if _is_sidecar(name, lowered):
                continue
            ext = os.path.splitext(name)[1].lower()
            seen = verdicts.get(ext, [])
            if len(seen) >= _SAME_FORMAT_PROOF and len(set(seen)) == 1:


                driver_name = seen[0]
            else:
                driver_name = _vector_driver(gdal, gdal_path(name))
                verdicts.setdefault(ext, []).append(driver_name)
            if driver_name:
                found.setdefault((os.path.splitext(name)[0].lower(), driver_name), []).append(name)
    finally:
        gdal.PopErrorHandler()
        gdal.SetThreadLocalConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", readdir)
    vectors, tables = [], []
    for (_stem, driver_name), members in found.items():
        own = "." + str(gdal.GetDriverByName(driver_name).GetMetadataItem("DMD_EXTENSION") or "").lower()
        member = next((m for m in members if m.lower().endswith(own)), min(members))
        (tables if driver_name == "CSV" else vectors).append(member)

    def order(name: str) -> tuple:
        return name.count("/"), name.lower()

    return sorted(vectors, key=order), sorted(tables, key=order)


def _zip_member_uri(path: str, member: str) -> str:
    return f"/vsizip/{path.replace(chr(92), '/')}/{member}"


def gdb_folders(names: list) -> list:





    found = []
    for name in names:
        parts = re.split(r"[\\/]", name)
        for index, part in enumerate(parts[:-1]):
            if part.lower().endswith(".gdb"):
                folder = "/".join(parts[:index + 1])
                if folder not in found:
                    found.append(folder)
                break
    return found



_FORMAT_WORD = re.compile(r"\*?\.?([a-z0-9]{2,8})")


def members_matching(names: list, wanted: str) -> list:




    wanted = str(wanted or "").strip()
    if not wanted:
        return []

    def keys(member: str) -> tuple:
        base = os.path.basename(member.rstrip("/\\"))
        return member, base, os.path.splitext(base)[0]

    folded = wanted.casefold()


    exact = ([member for member in names if wanted in keys(member)]
             or [member for member in names if folded in [key.casefold() for key in keys(member)]])
    if exact:
        return exact[:1]
    word = _FORMAT_WORD.fullmatch(folded)
    if word:
        by_format = [member for member in names if member.casefold().endswith("." + word.group(1))]
        if by_format:
            return by_format
    if any(char in wanted for char in "*?["):
        import fnmatch

        return [member for member in names
                if any(fnmatch.fnmatchcase(key.casefold(), folded) for key in keys(member))]
    return []



MERGE_OFFER = ("layer=<extension> (for example layer='shp') or a pattern (layer='zones/*.shp') merges the files "
               "it names into one layer per geometry type in the background, attributes kept and each row "
               "keeping its source_file.")


def describe_sublayers(path: str, names: list) -> list:





    options = QgsVectorLayer.LayerOptions() if on_main_thread() else worker_options()
    out = []
    for name in names:
        probe = QgsVectorLayer(f"{path}|layername={name}", name, "ogr", options)
        if not probe.isValid():
            out.append({"name": name, "valid": False})
        else:
            out.append({
                "name": name,
                "geometry": QgsWkbTypes.displayString(probe.wkbType()),
                "feature_count": probe.featureCount(),
                "crs": crs_ref(probe.crs()),
            })
        delete_here(probe)
    return out


_REMOTE_VECTOR_PREFIXES = ("http://", "https://", "/vsicurl/")


_CAD_EXTENSIONS = (".dwg", ".dxf")


def _safe_cad_stem(value: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "cad"))[:80].strip("._-") or "cad"
    if stem.upper() in {"CON", "PRN", "AUX", "NUL"}:
        stem = f"cad_{stem}"
    return stem


def _cad_crs_question(path: str, name: str) -> dict:





    from .crs_at_load import _VERDICT_FACT, verdict

    def refusal(probe) -> dict:
        found = verdict(probe, QgsCoordinateReferenceSystem(), declared=False) if probe.isValid() else {}
        found = found or {"verdict": "missing", "crs": "", "candidates": []}
        candidate_crs = [authid for authid, _why in found["candidates"]]
        return {"_error": ("CAD import needs the CRS the drawing was made in, and a drawing does not say it: "
                           "nothing was imported." + (f" {found['detail']}" if found.get("detail") else "")),
                "code": "INVALID_ARGS",
                "candidate_crs": candidate_crs,


                "variant": "candidates" if candidate_crs else "none", "layer": name, "cad": True,
                "declared": found.get("crs") or "", "candidates": candidate_crs,
                **_VERDICT_FACT.get(found["verdict"], {})}

    if on_main_thread():
        probe = QgsVectorLayer(path, name, "ogr")
        try:
            return refusal(probe)
        finally:
            delete_here(probe)
    probe = QgsVectorLayer(path, name, "ogr", worker_options())
    if probe.isValid():
        probe.extent()
    held = hand_over(probe)
    del probe

    def judge() -> dict:
        probe = held.take()
        try:
            return refusal(probe)
        finally:
            delete_here(probe)

    try:
        return run_on_main_thread(judge, timeout=60)
    finally:
        held.release()


def _add_cad_to_gpkg(path: str, name: str, crs: str | None) -> dict:








    if not str(crs or "").strip():



        return _cad_crs_question(path, name)
    target = QgsCoordinateReferenceSystem(str(crs).strip())
    if not target.isValid():
        return {"_error": f"The CRS {crs!r} is not known to QGIS; the CAD file was not imported.",
                "code": "INVALID_ARGS",
                **coded_fact(hint="cad_bad_crs", crs=str(crs))}
    try:
        from osgeo import gdal, ogr
        driver_names = ("CAD", "DWG") if path.lower().endswith(".dwg") else ("DXF", "CAD")
        driver_name = next((candidate for candidate in driver_names
                            if ogr.GetDriverByName(candidate) is not None), driver_names[0])
        driver = ogr.GetDriverByName(driver_name)
    except Exception as exc:  # noqa: BLE001
        return {
            "_error": f"GDAL CAD support is unavailable ({exc}).",
            "code": "EXECUTION_FAILED",
            **coded_fact(hint="cad_no_driver"),
        }
    if driver is None:
        return {
            "_error": f"This QGIS/GDAL build has no {driver_name} driver, so {os.path.basename(path)} cannot be read.",
            "code": "EXECUTION_FAILED",
            **coded_fact(hint="cad_no_driver", driver=driver_name),
        }
    directory = create_managed_temp_dir("cad")
    gpkg = os.path.join(directory, _safe_cad_stem(name) + ".gpkg")
    try:
        gdal.UseExceptions()




        assigned_crs = target.authid() or target.toWkt()
        translated = gdal.VectorTranslate(gpkg, path, format="GPKG", srcSRS=assigned_crs,
                                           dstSRS=assigned_crs, layerName=_safe_cad_stem(name))
        if translated is None:
            raise RuntimeError("GDAL returned no output dataset")
        del translated
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"Could not convert {os.path.basename(path)} to GeoPackage: {exc}",
                "code": "EXECUTION_FAILED",
                **coded_fact(hint="cad_convert_failed")}
    table = _safe_cad_stem(name)
    parts = _geometry_parts(gpkg, table) or [(f"{gpkg}|layername={table}", "")]


    built: list = []
    for uri, word in parts:
        facts: dict = {}
        label = f"{name} ({word})" if word else name

        def make(uri=uri, label=label, facts=facts):
            layer = QgsVectorLayer(uri, label, "ogr", worker_options())
            if layer.isValid():
                facts["count"] = layer.featureCount()
                facts["by_layer"] = layer.fields().lookupField("Layer") >= 0
            return layer

        built.append({"handle": built_here(make), "uri": uri, "label": label, "word": word, "facts": facts})
    colours = (_cad_colours(path) if any(p["handle"] is None or p["facts"].get("by_layer") for p in built)
               else None)

    def add() -> dict:
        taken = []
        try:
            layers = []
            for part in built:
                layer = take(part["handle"])
                if layer is None:
                    layer = QgsVectorLayer(part["uri"], part["label"], "ogr")
                    part["facts"].clear()
                taken.append(layer)
                if not layer.isValid():
                    return {"_error": f"GDAL created {gpkg}, but QGIS could not open its GeoPackage layer.",
                            "code": "EXECUTION_FAILED",
                            **coded_fact(hint="cad_open_failed")}
                layers.append((layer, part["word"], _cad_layer_style(layer, colours), part["facts"].get("count")))
            if not still_awaited():
                return dict(_NOT_AWAITED)
            for layer, _word, _styled, _count in layers:
                QgsProject.instance().addMapLayer(layer)

            def count(layer, known):
                return known if known is not None else layer.featureCount()

            layer, _word, styled, known = layers[0]
            out = {"name": layer.name(), "layer_id": layer.id(), "feature_count": count(layer, known),
                   "crs": crs_ref(layer.crs()), "path": gpkg, "gpkg_path": gpkg,
                   "source_format": os.path.splitext(path)[1].lower().lstrip("."), "crs_assigned": target.authid()}
            if styled:
                out["styled"] = styled
            if len(layers) > 1:
                out["layers"] = [{"name": each.name(), "layer_id": each.id(), "geometry": word,
                                  "feature_count": count(each, known)} for each, word, _styled, known in layers]
            return out
        finally:
            for layer in taken:
                if QgsProject.instance().mapLayer(layer.id()) is not layer:
                    delete_here(layer)

    try:
        return run_on_main_thread(add, timeout=60)
    finally:
        for part in built:
            if part["handle"] is not None:
                part["handle"].release()


def _split_entries(entries) -> tuple:






    collection = enum_member(QgsWkbTypes, "Type", "GeometryCollection")
    unknown = enum_member(QgsWkbTypes, "Type", "Unknown")
    parts, left_out = [], 0
    for uri, wkb, count in entries:
        if count == 0:
            continue
        if QgsWkbTypes.flatType(wkb) in (collection, unknown):
            left_out += max(count, 0)
            continue
        parts.append((uri, wkb, count))
    if not parts or (len(parts) < 2 and not left_out):
        return [], 0
    parts.sort(key=lambda part: -part[2])
    return [(uri, QgsWkbTypes.geometryDisplayString(QgsWkbTypes.geometryType(wkb))) for uri, wkb, _n in parts], left_out


def _geometry_parts(gpkg: str, table: str | None) -> list:







    try:
        from qgis.core import Qgis, QgsProviderRegistry

        details = QgsProviderRegistry.instance().querySublayers(gpkg, Qgis.SublayerQueryFlag.ResolveGeometryType)
    except Exception:  # noqa: BLE001
        return []
    return _split_entries([(d.uri(), d.wkbType(), d.featureCount()) for d in details
                           if table is None or d.name() == table])[0]


def geometry_split(uri: str) -> tuple:









    source, _sep, table = uri.partition("|layername=")
    if "|" in source or source.lower().startswith(("/vsicurl", "http://", "https://")):
        return [], 0
    try:
        from qgis.core import Qgis, QgsProviderRegistry

        details = QgsProviderRegistry.instance().querySublayers(source, Qgis.SublayerQueryFlag.ResolveGeometryType)
    except Exception:  # noqa: BLE001
        return [], 0
    table = table.split("|", 1)[0]
    if not table and len({d.name() for d in details}) != 1:
        return [], 0
    return _split_entries([(d.uri(), d.wkbType(), d.featureCount()) for d in details
                           if not table or d.name() == table])





_SPLIT_POOL = None


def split_alongside(uri: str):

    global _SPLIT_POOL
    from ..core.background import KeptThreadPool

    try:
        if _SPLIT_POOL is None:
            _SPLIT_POOL = KeptThreadPool(2, name="ai-agent-geometry-split")
        future = _SPLIT_POOL.submit(geometry_split, uri)
    except RuntimeError:
        return lambda: geometry_split(uri)

    def result():
        try:
            return future.result()
        except Exception:  # noqa: BLE001
            return [], 0
    return result


def split_left_out_note(left_out: int) -> str:

    return (f"{left_out} feature(s) of the file are geometry collections, which a QGIS layer of one "
            "geometry type does not hold; they are not loaded.")





_CAD_COLOUR_RE = re.compile(r"\b(?:fc|c):#([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})?")
_CAD_COLOUR_READ_MAX = 500_000


def _cad_colours(path: str) -> tuple | None:


    from collections import Counter

    try:
        from osgeo import ogr

        source = ogr.Open(path)
    except Exception:  # noqa: BLE001
        return None
    if source is None:
        return None
    colours: dict[str, Counter] = {}
    hidden: dict[str, int] = {}
    read = 0
    for index in range(source.GetLayerCount()):
        if read >= _CAD_COLOUR_READ_MAX:
            break
        cad = source.GetLayer(index)
        field = cad.GetLayerDefn().GetFieldIndex("Layer")
        for feature in cad:
            read += 1
            if read > _CAD_COLOUR_READ_MAX:
                break
            name = feature.GetFieldAsString(field) if field >= 0 else cad.GetName()
            match = _CAD_COLOUR_RE.search(feature.GetStyleString() or "")
            counter = colours.setdefault(name, Counter())
            if match is None:
                continue
            if (match.group(2) or "ff").lower() == "00":
                hidden[name] = hidden.get(name, 0) + 1
                continue
            counter[match.group(1).lower()] += 1
    source = None
    return colours, hidden


def _cad_layer_style(layer, read: tuple | None) -> str:









    from qgis.core import QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol
    from qgis.PyQt.QtGui import QColor

    if read is None or layer.fields().lookupField("Layer") < 0:
        return ""
    colours, hidden = read
    if not colours:
        return ""
    categories = []
    for name in sorted(colours):
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        if symbol is None:
            return ""
        common = colours[name].most_common(1)
        hexa = common[0][0] if common else "000000"
        symbol.setColor(QColor("#000000" if hexa == "ffffff" else f"#{hexa}"))
        shown = hidden.get(name, 0) <= sum(colours[name].values())
        categories.append(QgsRendererCategory(name, symbol, name, shown))

    others = QgsSymbol.defaultSymbol(layer.geometryType())
    if others is not None:
        others.setColor(QColor("#808080"))
        categories.append(QgsRendererCategory("", others, "Other CAD layers", True))
    layer.setRenderer(QgsCategorizedSymbolRenderer("Layer", categories))
    off = [c.label() for c in categories if not c.renderState()]
    return (f"by CAD layer, {len(colours)} layers in the drawing's own colours"
            + (f"; switched off in the drawing, so unticked: {', '.join(off[:10])}" if off else ""))


def _add_vector_layer(args: dict) -> dict:





    path = args["path"]
    name = args.get("name") or os.path.splitext(os.path.basename(path))[0]

    if path.startswith(_REMOTE_VECTOR_PREFIXES):
        from .data_tools import _add_vector_from_url
        url = path.replace("/vsicurl/", "") if path.startswith("/vsicurl/") else path
        return _add_vector_from_url({"url": url, "layer_name": name, "crs": args.get("crs")})



    file_part, _bar, options = path.partition("|")
    if options and not args.get("name"):
        from qgis.core import QgsProviderRegistry

        name = QgsProviderRegistry.instance().decodeUri("ogr", path).get("layerName") or name
    real_path = _virtual_source_path(file_part) or file_part
    path_error = _checked_path(real_path)
    if path_error:
        return {"_error": path_error}

    if not os.path.exists(real_path):
        return missing_file(real_path, f"File not found: {real_path}")

    if os.path.isdir(real_path) and not real_path.rstrip("/\\").lower().endswith(".gdb"):


        from .vector_merge import folder_plan, start_folder_merge

        action, value = folder_plan(real_path, args.get("layer"))
        if action == "load":
            return _add_vector_layer({"path": value, "crs": args.get("crs"),
                                      "name": args.get("name") or os.path.splitext(os.path.basename(value))[0]})
        if action == "merge":

            return run_on_main_thread(lambda: start_folder_merge(real_path, value, name), timeout=60)
        return value

    if real_path.lower().endswith(_CAD_EXTENSIONS):
        return _add_cad_to_gpkg(real_path, name, args.get("crs"))

    if path.lower().endswith(CSV_EXTENSIONS):
        return add_csv(path, name, args.get("crs"))

    uri = path
    wanted = str(args.get("layer") or "").strip()
    archive_members: list = []
    if path.lower().endswith(".zip"):
        names, problem = _zip_names(path)
        if problem:
            return tool_error(f"Could not read the archive {os.path.basename(path)}: {problem}",
                              hint="archive_unreadable")


        members, tables = archive_datasets(names, lambda name: _zip_member_uri(path, name))
        gdbs = gdb_folders(names)
        if gdbs and not members and len(gdbs) == 1:


            listed = _add_vector_layer({**args, "path": _zip_member_uri(path, gdbs[0])})
            if isinstance(listed, dict) and listed.get("layers") and not listed.get("layer_id"):
                listed["path"] = path
            return listed
        members = members + gdbs



        if wanted:
            table = [n for n in tables if wanted.casefold() in (n.casefold(), os.path.basename(n).casefold())][:1]
        else:
            table = tables if not members and len(tables) == 1 else []
        if table:
            import shutil
            import zipfile

            local = os.path.join(create_managed_temp_dir("unzipped"), os.path.basename(table[0]))
            with zipfile.ZipFile(path) as archive, archive.open(table[0]) as source, open(local, "wb") as sink:
                shutil.copyfileobj(source, sink)
            return add_csv(local, args.get("name") or os.path.splitext(os.path.basename(table[0]))[0],
                           args.get("crs"))
        sublayer = ""
        if wanted:
            picked = members_matching(members, wanted)

            if (not picked and len(members) == 1
                    and container_layers(_zip_member_uri(path, members[0])) is not None):
                picked, sublayer = members, wanted
            if not picked:
                return tool_error(f"No file named {wanted!r} in {os.path.basename(path)}.",
                                  hint="archive_file_not_found", files=(members + tables or names)[:50])
            members = picked
        if not members:
            return tool_error(f"{os.path.basename(path)} holds no vector file QGIS reads.",
                              hint="archive_no_vector_file", files=names[:50])
        if len(members) > 1 and wanted:

            from . import vector_merge

            folder, sources = _zip_member_uri(path, "").rstrip("/"), [_zip_member_uri(path, m) for m in members]
            return run_on_main_thread(lambda: vector_merge.start_folder_merge(folder, sources, name), timeout=60)
        if len(members) > 1:

            return {
                "path": path,
                "layers": members[:50],
                "_note": f"{os.path.basename(path)} holds {len(members)} vector files, none added yet.",
                **coded_fact(hint="archive_files_listed", archive=os.path.basename(path), count=len(members)),
            }
        if members[0] in gdbs:
            return _add_vector_layer({**args, "layer": None, "path": _zip_member_uri(path, members[0])})
        uri = _zip_member_uri(path, members[0])
        if not args.get("name"):
            name = os.path.splitext(os.path.basename(members[0]))[0]
        wanted = sublayer
    elif path.startswith("/vsizip/"):
        archive_members = archive_datasets(_zip_names(real_path)[0],
                                           lambda name: _zip_member_uri(real_path, name))[0]
    source = uri
    names = container_layers(source)
    if names is not None:
        if wanted:
            if names and wanted not in names:
                return {"_error": f"No layer named {wanted!r} in {os.path.basename(source)}.",
                        "sublayers": names, "suggestion": "layer names one of the listed layers."}
            uri = f"{source}|layername={wanted}"
            if not args.get("name"):
                name = wanted
        elif len(names) > 1:

            return {
                "path": path,
                "layers": describe_sublayers(source, names),
                "_note": f"{os.path.basename(source)} holds {len(names)} layers, none added yet.",
                **coded_fact(hint="container_layers_listed", source=os.path.basename(source), count=len(names)),
            }
        elif len(names) == 1:
            uri = f"{source}|layername={names[0]}"
            if not args.get("name"):
                name = names[0]

    facts: dict = {}


    siblings: list = []

    def split_parts(layer, options, pending=None):
        if not layer.isValid():
            return layer
        source = layer.source()


        parts, facts["left_out"] = pending() if pending is not None and source == uri else geometry_split(source)
        if not parts:
            return layer
        delete_here(layer)
        for part_uri, word in parts[1:]:
            label = f"{name} ({word})"
            siblings.append((built_here(lambda u=part_uri, n=label: QgsVectorLayer(u, n, "ogr", worker_options())),
                             part_uri, label))
        label = f"{name} ({parts[0][1]})" if len(parts) > 1 else name
        return QgsVectorLayer(parts[0][0], label, "ogr", options)

    def make():
        pending = split_alongside(uri)
        layer, facts["split"] = _open_vector(uri, name, real_path, worker_options())
        layer = split_parts(layer, worker_options(), pending)
        if layer.isValid():
            facts["count"] = layer.featureCount()
        return layer

    built = built_here(make)
    on_main: dict = {}

    def build_on_main():
        layer, on_main["split"] = _open_vector(uri, name, real_path, QgsVectorLayer.LayerOptions())
        siblings.clear()
        return split_parts(layer, QgsVectorLayer.LayerOptions())

    def add(layer) -> dict:
        if not layer.isValid():
            if archive_members:
                return tool_error(f"Failed to load vector layer from: {uri}", hint="archive_member_load_failed",
                                  layers=archive_members[:50])
            return {"_error": f"Failed to load vector layer from: {uri}"}
        split = facts.get("split") if built is not None else on_main.get("split")
        crs_outcome = _apply_requested_crs(layer, args.get("crs"))
        if not still_awaited():
            return dict(_NOT_AWAITED)
        QgsProject.instance().addMapLayer(layer)
        added = [layer]
        for handle, part_uri, label in siblings:
            sibling = take(handle)
            sibling = sibling if sibling is not None else QgsVectorLayer(part_uri, label, "ogr")
            if sibling.isValid():
                _apply_requested_crs(sibling, args.get("crs"))
                QgsProject.instance().addMapLayer(sibling)
                added.append(sibling)
            else:
                delete_here(sibling)
        out = {
            "name": layer.name(),
            "layer_id": layer.id(),
            "feature_count": facts["count"] if built is not None and "count" in facts else layer.featureCount(),
            "crs": crs_ref(layer.crs()),
        }
        if len(added) > 1:
            out["layers"] = [{"name": each.name(), "layer_id": each.id(),
                              "geometry": QgsWkbTypes.geometryDisplayString(each.geometryType()),
                              "feature_count": each.featureCount()} for each in added]
        if facts.get("left_out"):
            out["not_loaded"] = split_left_out_note(facts["left_out"])
        out.update(crs_outcome)
        if split is not None:
            from .vector_merge import split_note

            out.update({"path": split["gpkg"], "source_format": os.path.splitext(real_path)[1].lstrip(".").lower(),
                        "description_fields": split["description_fields"], "_note": split_note(split)})
        return out

    return _on_main_with(built, build_on_main, add)


def _open_vector(uri: str, name: str, real_path: str, options) -> tuple:






    if real_path.lower().endswith((".kml", ".kmz")):

        from .vector_merge import split_description

        source, _sep, sublayer = uri.partition("|layername=")
        split = split_description(source, sublayer or None, name)
        if split is not None:
            rebuilt = QgsVectorLayer(split["uri"], name, "ogr", options)
            if rebuilt.isValid():
                return rebuilt, split
            delete_here(rebuilt)
    return QgsVectorLayer(uri, name, "ogr", options), None




NO_CRS_WARNING = "The file declares no CRS, so QGIS cannot place it."


def _apply_requested_crs(layer, wanted) -> dict:








    provider = layer.dataProvider()
    declared = provider.crs() if provider is not None else layer.crs()
    wanted = str(wanted or "").strip()
    if not wanted:
        if declared.isValid() or layer.crs().isValid():
            return {}
        return {"warning": NO_CRS_WARNING}
    target = QgsCoordinateReferenceSystem(wanted)
    if not target.isValid():
        return {"warning": (f"crs {wanted!r} is not a CRS QGIS knows, so it was not applied."
                            + ("" if declared.isValid() else " The file declares none either: QGIS cannot place it."))}
    if not declared.isValid():
        layer.setCrs(target)
        return {"crs_assigned": target.authid() or wanted}
    if declared == target:
        return {}
    named = declared.authid() or declared.description() or "a custom CRS"
    return {"warning": f"The file declares {named}, so crs {wanted} was not applied.",
            **coded_fact(hint="crs_request_not_applied", declared=named, requested=wanted)}


def _add_raster_layer(args: dict) -> dict:
    path = args["path"]
    name = args.get("name") or os.path.splitext(os.path.basename(path))[0]

    if path.startswith(("http://", "https://", "/vsicurl/", "ftp://")):
        return tool_error("add_raster_layer loads LOCAL raster files only.", hint="raster_remote_source")

    path_error = _checked_path(path)
    if path_error:
        return {"_error": path_error}

    if not _source_exists(path):
        return missing_file(path, f"File not found: {path}")

    source = path
    subdataset = str(args.get("layer") or "").strip()
    if path.lower().endswith((".nc", ".hdf")) and subdataset:
        source = f'NETCDF:"{path}":{subdataset}'
    elif path.lower().endswith(".zip") and subdataset:



        names, problem = _zip_names(path)
        picked = members_matching([n for n in names if not n.endswith("/")], subdataset)
        if problem or len(picked) != 1:
            return {"_error": (f"{path} could not be read: {problem}" if problem else
                               f"layer '{subdataset}' names {len(picked)} files of {os.path.basename(path)}, "
                               "and a raster loads from one."),
                    "files": [n for n in names if not n.endswith("/")][:50]}
        source = _zip_member_uri(path, picked[0])
        if not args.get("name"):
            name = os.path.splitext(os.path.basename(picked[0]))[0]
    from .elevation_style import apply_elevation_style, elevation_stretch
    from .stac_tools import normalise_crs



    stretch = elevation_stretch(source, name)
    built = built_here(lambda: QgsRasterLayer(source, name, "gdal", worker_options(QgsRasterLayer)))

    def add(layer) -> dict:
        if not layer.isValid():
            return {"_error": f"Failed to load raster layer from: {source}"}
        normalise_crs(layer)
        styled = apply_elevation_style(layer, stretch)
        if not still_awaited():
            return dict(_NOT_AWAITED)
        QgsProject.instance().addMapLayer(layer)
        return {
            "name": layer.name(),
            "layer_id": layer.id(),
            "crs": crs_ref(layer.crs()),
            "width": layer.width(),
            "height": layer.height(),
            **({"styled": styled} if styled else {}),
        }

    return _on_main_with(built, lambda: QgsRasterLayer(source, name), add)













_REMOTE_POINT_CLOUD_PROVIDERS = ("copc", "ept", "vpc")


def point_cloud_provider(source: str) -> str:

    lower = source.lower().split("?", 1)[0].rstrip("/")
    if lower.endswith("ept.json"):
        return "ept"
    if lower.endswith(".vpc"):
        return "vpc"
    if lower.endswith(".copc.laz"):
        return "copc"
    if lower.endswith((".las", ".laz")):
        return "pdal"
    return ""


def _provider_available(key: str) -> bool:







    try:
        from qgis.core import QgsProviderRegistry
        return key in QgsProviderRegistry.instance().providerList()
    except Exception:  # noqa: BLE001
        return True


def _add_point_cloud_layer(args: dict) -> dict:

    source = str(args.get("path") or args.get("source") or "").strip()
    if not source:
        return {"_error": "A point cloud needs a file path or a URL."}

    provider = point_cloud_provider(source)
    if not provider:
        return {"_error": f"Not a point cloud source: {source}"}

    remote = source.lower().startswith(("http://", "https://", "ftp://", "/vsicurl/"))
    if remote:
        source = source.replace("/vsicurl/", "", 1)
        if provider not in _REMOTE_POINT_CLOUD_PROVIDERS:
            return tool_error(f"A remote {os.path.splitext(source)[1]} has no index, so it can only be read "
                              "once downloaded in full.", hint="point_cloud_remote_unindexed")
    else:
        path_error = validate_path(source, write=False)
        if path_error:
            return {"_error": path_error}
        if not os.path.exists(source):
            return {"_error": f"File not found: {source}"}

    if not _provider_available(provider):
        return tool_error(f"This QGIS build has no {provider} provider, so it cannot open point clouds.",
                          hint="point_cloud_provider_missing", provider=provider)

    try:
        from qgis.core import QgsPointCloudLayer
    except ImportError:
        return {"_error": "This QGIS version has no point cloud support.",
                "suggestion": "Point clouds need QGIS 3.26 or newer."}

    name = args.get("name") or os.path.splitext(os.path.basename(source.split("?", 1)[0]))[0] or "point cloud"
    layer = QgsPointCloudLayer(source, name, provider)
    if not layer.isValid():
        return tool_error(f"Failed to load point cloud from: {source}", hint="point_cloud_load_failed")

    QgsProject.instance().addMapLayer(layer)
    out = {
        "name": layer.name(),
        "layer_id": layer.id(),
        "provider": provider,
        "crs": crs_ref(layer.crs()),
    }
    try:
        out["point_count"] = int(layer.pointCount())
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    try:
        stats = layer.attributes()
        out["attributes"] = [stats.at(i).name() for i in range(stats.count())]
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    if remote:
        out["_note"] = ("Read in place over HTTP range requests: nothing was downloaded, and only the octree "
                        "nodes the view needs are fetched.")
    return out


def _same_file(a: str, b: str) -> bool:

    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return False


def _release_layers_at_path(path: str, skip_ids=None, delete_existing: bool = False) -> list:











    if not os.path.exists(path):
        return []
    from .processing_run import remove_layers

    removed = []
    skip_ids = set(skip_ids or ())
    project = QgsProject.instance()
    for layer_id, layer in list(project.mapLayers().items()):
        if layer_id in skip_ids:
            continue
        source = layer.source() or ""

        file_part = source.split("|", 1)[0]
        if not file_part or not _same_file(file_part, path):
            continue



        release_pooled_handles(layer)
        removed += remove_layers([layer_id])

    if not delete_existing:
        return removed







    still_loaded = any(
        (lyr.source() or "").split("|", 1)[0]
        and _same_file((lyr.source() or "").split("|", 1)[0], path)
        for lyr in project.mapLayers().values()
    )
    if not still_loaded:
        _delete_with_retry(path)
    return removed


def _staging_path(path: str) -> str:






    directory = os.path.dirname(path) or "."
    stem, ext = os.path.splitext(os.path.basename(path))
    tag = uuid.uuid4().hex[:8]
    staging = os.path.join(directory, f".{stem}.ai-agent-{tag}.partial{ext}")
    from ..core import security





    if len(staging) + 8 >= security._MAX_PATH and not security._long_paths_ok():
        staging = os.path.join(directory, f".ai-{tag}.partial{ext}")
    return staging


def _staged_files(staging: str) -> list:

    directory = os.path.dirname(staging) or "."
    stem = os.path.splitext(os.path.basename(staging))[0]
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names
            if os.path.splitext(name)[0] == stem and os.path.isfile(os.path.join(directory, name))]


def _raster_staged_files(staging: str) -> list:






    directory = os.path.dirname(staging) or "."
    stem = os.path.splitext(os.path.basename(staging))[0]
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names
            if (name == stem or name.startswith(stem + ".")) and os.path.isfile(os.path.join(directory, name))]


def _publish_staged_write(staging: str, path: str) -> str:






    produced = _staged_files(staging)
    if not os.path.isfile(staging):
        return f"the writer produced no file at {os.path.basename(staging)}"
    target_stem = os.path.splitext(path)[0]
    try:


        for produced_path in sorted(produced, key=lambda f: f == staging):
            extension = os.path.splitext(produced_path)[1]




            retry_file_op(os.replace, produced_path, f"{target_stem}{extension}")
    except OSError as exc:
        return str(exc)
    return ""


def _detach_vector_layers_at_path(path: str) -> tuple[list[dict], dict]:








    from qgis.core import QgsMapLayerStyle

    matches = []
    for layer in QgsProject.instance().mapLayers().values():
        source = layer.source() or ""
        file_part = source.split("|", 1)[0]
        if not file_part or not _same_file(file_part, path):
            continue
        if not isinstance(layer, QgsVectorLayer):
            return [], tool_error(f"A non-vector layer named '{layer.name()}' is using {os.path.basename(path)}.",
                                  hint="export_target_non_vector_layer", layer=layer.name(),
                                  file=os.path.basename(path))
        if layer.isEditable() or layer.isModified():
            return [], tool_error(f"Layer '{layer.name()}' has unsaved edits in {os.path.basename(path)}.",
                                  hint="export_target_unsaved_edits", layer=layer.name(),
                                  file=os.path.basename(path))
        style = QgsMapLayerStyle()
        style.readFromLayer(layer)
        matches.append({
            "layer": layer,
            "source": source,
            "name": layer.name(),
            "provider": layer.providerType(),
            "style": style,
            "selection": list(layer.selectedFeatureIds()),
            "subset": layer.subsetString(),
        })

    detached = []
    for state in matches:
        layer = state["layer"]
        geometry = QgsWkbTypes.displayString(layer.wkbType()) or "None"
        crs = layer.crs().authid()
        temporary = geometry + (f"?crs={crs}" if crs else "")
        detached.append(state)


        release_pooled_handles(layer)
        layer.setDataSource(temporary, state["name"], "memory")
        if not layer.isValid() or layer.providerType() != "memory":
            _restore_detached_layers(detached)
            return [], tool_error(f"Could not release layer '{state['name']}' before replacing "
                                  f"{os.path.basename(path)}.")
    return detached, {}


def _restore_detached_layers(states: list[dict]) -> list[str]:

    failed = []
    for state in states:
        layer = state["layer"]
        layer.setDataSource(state["source"], state["name"], state["provider"])
        if not layer.isValid():
            failed.append(state["name"])
            continue
        if state["subset"]:
            layer.setSubsetString(state["subset"])
        state["style"].writeToLayer(layer)
        if state["selection"]:
            layer.selectByIds(state["selection"])
        layer.triggerRepaint()
    return failed


def _style_into_export(layer, path: str, driver: str, table: str) -> dict:









    if driver != "GPKG" or not isinstance(layer, QgsVectorLayer):
        return {}
    renderer = layer.renderer()
    if renderer is None or (renderer.type() == "singleSymbol" and not layer.labelsEnabled()):
        return {}
    from .style_defaults import carry_style

    written = QgsVectorLayer(f"{path}|layername={table}", table, "ogr")
    try:
        if not written.isValid() or not carry_style(layer, written, "export_layer", replace=True):
            return {}
        saver = getattr(written, "saveStyleToDatabaseV2", None) or written.saveStyleToDatabase
        saver(table, "", True, "")
        listed = written.listStylesInDatabase()
        names = listed[2] if isinstance(listed, tuple) and len(listed) > 2 else []
        if table not in list(names or []):
            return {}
    except Exception as exc:  # noqa: BLE001
        log_warning(f"export_layer: style of {layer.name()} not stored in {os.path.basename(path)}: {exc}")
        return {}
    finally:
        release_pooled_handles(written)
        del written
    return {"style_in_gpkg": True, "renderer": renderer.type(),
            "note": "the table opens with this layer's style in any QGIS project (its default style)"}


def _publish_export(staging: str, path: str, driver: str) -> tuple[dict, list[str]]:

    if driver != "GPKG" or not os.path.exists(path):
        written = _publish_staged_write(staging, path)
        return (_export_failure(path, written, lead="") if written else {}), []
    states, detach_error = _detach_vector_layers_at_path(path)
    if detach_error:
        return detach_error, []
    try:
        written = _publish_staged_write(staging, path)
    finally:
        failed = _restore_detached_layers(states)
    if failed:
        return tool_error(f"The export was written, but these layers could not be refreshed: {', '.join(failed)}"), []
    return (_export_failure(path, written, lead="") if written else {}), [state["name"] for state in states]


def _discard_staged_write(staging: str) -> None:

    for produced_path in _staged_files(staging):
        remove_quietly(produced_path)








_EXPORT_ERROR_LINES_KEPT = 3



_NON_FINITE_PHRASE = "non-finite values"


def _collapse_repeats(error_msg: str) -> tuple[str, int]:





    lines = [line.strip() for line in (error_msg or "").splitlines() if line.strip()]
    counts: dict[str, int] = {}
    order: list[str] = []
    for line in lines:
        if line not in counts:
            counts[line] = 0
            order.append(line)
        counts[line] += 1
    if not order or len(order) == len(lines):
        return (error_msg or ""), 0
    kept = [f"{line} (x{counts[line]})" if counts[line] > 1 else line
            for line in order[:_EXPORT_ERROR_LINES_KEPT]]
    if len(order) > _EXPORT_ERROR_LINES_KEPT:
        kept.append(f"and {len(order) - _EXPORT_ERROR_LINES_KEPT} other messages")
    return " ".join(kept), max(counts.values())


def _export_failure(path: str, error_msg: str, lead: str = "Export failed: ") -> dict:







    if IS_WINDOWS and os.path.exists(path) and "already exists" in (error_msg or "").lower():
        return tool_error(f"{lead}{error_msg}", hint="export_file_held", file=os.path.basename(path))
    collapsed, repeats = _collapse_repeats(error_msg)
    if _NON_FINITE_PHRASE in (error_msg or ""):
        many = f"{repeats} features hold" if repeats > 1 else "A feature holds"
        return tool_error(f"{lead}{collapsed} {many} a coordinate that is NaN or infinite, which no OGR "
                          "format can write; the rest of the layer was not written either.",
                          hint="export_non_finite_coordinates", features=repeats)
    return tool_error(f"{lead}{collapsed}")











SHAPEFILE_FIELD_NAME_MAX = 10


def _shapefile_field_names(layer) -> tuple[list[str], dict[str, str]]:






    used = set()
    names = []
    mapping = {}
    for field in layer.fields():
        original = str(field.name())
        ascii_name = unicodedata.normalize("NFKD", original).encode("ascii", "ignore").decode("ascii")
        ascii_name = re.sub(r"[^A-Za-z0-9_]", "_", ascii_name)
        ascii_name = ascii_name.strip("_") or "field"
        base = ascii_name[:SHAPEFILE_FIELD_NAME_MAX]
        candidate = base
        suffix = 2
        while candidate.casefold() in used:
            tail = str(suffix)
            candidate = f"{base[:SHAPEFILE_FIELD_NAME_MAX - len(tail)]}{tail}"
            suffix += 1
        used.add(candidate.casefold())
        names.append(candidate)
        mapping[original] = candidate
    return names, mapping


def _is_shapefile(layer) -> bool:

    try:
        if "esri shapefile" in str(layer.dataProvider().storageType() or "").casefold():
            return True
    except Exception as exc:  # noqa: BLE001
        log_warning(f"_is_shapefile: storageType() failed: {exc}")
    try:
        source = str(layer.source() or "").split("|", 1)[0]
    except Exception:  # noqa: BLE001
        return False
    return source.casefold().endswith(".shp")


def shapefile_field_name_error(layer, field_name: str) -> dict | None:

    name = str(field_name or "")
    if len(name) <= SHAPEFILE_FIELD_NAME_MAX or not _is_shapefile(layer):
        return None
    short = name[:SHAPEFILE_FIELD_NAME_MAX]
    try:
        taken = {field.name().casefold() for field in layer.fields()}
    except Exception:  # noqa: BLE001
        taken = set()
    if short.casefold() in taken:
        stem = short[:SHAPEFILE_FIELD_NAME_MAX - 1]
        suffix = 2
        while f"{stem}{suffix}".casefold() in taken and suffix < 10:
            suffix += 1
        short = f"{stem}{suffix}"
    return tool_error(
        f"{name!r} is {len(name)} characters and {layer.name()!r} is an ESRI Shapefile, which stores "
        f"field names in a dBASE header of {SHAPEFILE_FIELD_NAME_MAX} characters. The provider would "
        f"write {short!r} instead and QGIS would then refuse the whole edit.",
        code="INVALID_ARGS", hint="shapefile_field_name", short_name=short,
    )






_FIELD_MISMATCH_MARKERS = ("ESRI Shapefile", "typeName")


def commit_failure_error(layer, errors: list, suggestion: str = "", hint: str = "") -> dict:

    joined = "; ".join(str(line) for line in (errors or []))
    collapsed, _ = _collapse_repeats(joined)
    if all(marker in joined for marker in _FIELD_MISMATCH_MARKERS) and _is_shapefile(layer):
        return tool_error(
            f"Commit failed: {collapsed}",
            code="EXECUTION_FAILED", hint="shapefile_field_stored_short", max_chars=SHAPEFILE_FIELD_NAME_MAX,
        )
    if hint:

        return tool_error(f"Commit failed: {collapsed}", code="EXECUTION_FAILED", hint=hint)
    return tool_error(f"Commit failed: {collapsed}", code="EXECUTION_FAILED",
                      suggestion=suggestion or "The layer is unchanged; read the message and change the approach.")


def _field_kind_name(field) -> str:

    try:
        actual = field.type()
    except Exception:  # noqa: BLE001
        return ""
    for name, qtype in FIELD_TYPES.items():
        if qtype == actual:
            return name
    return ""


def _delete_with_retry(path: str, attempts: int = 8, pause: float = 0.02) -> bool:



















    max_pause = 0.15


    try:
        from osgeo import gdal
    except Exception:
        gdal = None

    for attempt in range(attempts):
        if not os.path.exists(path):
            return True
        if gdal is not None:
            try:
                gdal.Unlink(path)
            except Exception:  # nosec B110
                pass
            if not os.path.exists(path):
                return True
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(min(pause * (2 ** attempt), max_pause))
    return False


def _staging_failure(path: str, exc: Exception) -> dict:
    return {"_error": f"Could not prepare the export beside {os.path.basename(path)}: {exc}",
            "_code": "EXECUTION_FAILED",
            "suggestion": "The volume needs room, and the folder needs write access."}


def _copy_into_staging(_task, source: str, staging: str) -> bool:

    from ..core.snapshot_files import _sqlite_copy

    _sqlite_copy(source, staging)
    return True


def _export_in_background(layer, path: str, staging: str, driver: str, options, count: int, released: list,
                          extra: dict | None = None, stage_from: str | None = None):








    try:
        from qgis.core import QgsTask, QgsVectorFileWriterTask

        from .processing_run import register_task
    except ImportError:
        return None
    try:


        task = QgsVectorFileWriterTask(layer, staging, options)
    except (TypeError, ValueError):
        _discard_staged_write(staging)
        return None
    copy = None
    if stage_from:
        try:
            copy = QgsTask.fromFunction(f"Copy {os.path.basename(stage_from)} before the export",
                                        _copy_into_staging, stage_from, staging)
            task.addSubTask(copy, [], enum_member(QgsTask, "SubTaskDependency", "ParentDependsOnSubTask"))
        except (AttributeError, TypeError, ValueError):
            _discard_staged_write(staging)
            return None
    done = {"exported": path, "format": driver, "feature_count": count,
            "selected_only": options.onlySelectedFeatures}
    done.update(extra or {})
    if released:
        done["replaced_layers"] = released

    def wire(_task_id: str, entry: dict) -> None:



        entry["partial_file"] = staging
        entry["staged_for"] = path




        def complete(_name: str) -> None:




            if entry.get("status") != "running":
                _discard_staged_write(staging)
                entry.pop("partial_file", None)
                entry["note"] = (f"The export was stopped. {os.path.basename(path)} was not touched: the "
                                 f"write went to a file of its own, which has been removed.")
                return
            publish_error, refreshed = _publish_export(staging, path, driver)
            if publish_error:
                _discard_staged_write(staging)
                entry["status"] = "error"
                entry["error"] = (f"The export was written but could not be put in place at {path}: "
                                  f"{publish_error}. The file that was there is unchanged.")
                return
            if refreshed:
                done["refreshed_layers"] = refreshed
            try:
                done.update(_style_into_export(layer, path, driver, options.layerName))
            except RuntimeError:
                pass
            entry.pop("partial_file", None)
            entry.pop("staged_for", None)
            entry["status"] = "complete"
            entry["progress"] = 100
            entry["outputs"] = done

        def failed(_code, message) -> None:
            _discard_staged_write(staging)
            entry.pop("partial_file", None)
            if entry.get("status") != "running":
                return
            entry["status"] = "error"
            entry["error"] = (f"Export failed: {message}. {os.path.basename(path)} is unchanged: the write "
                              f"went to a file of its own.")

        def stopped() -> None:



            _discard_staged_write(staging)
            entry.pop("partial_file", None)
            failed_copy = getattr(copy, "exception", None) if copy is not None else None
            if failed_copy is not None and entry.get("status") == "running":
                entry["status"] = "error"
                entry["error"] = (f"Could not prepare the export beside {os.path.basename(path)}: {failed_copy}. "
                                  f"{os.path.basename(path)} is unchanged.")
                return
            entry.setdefault("note", (f"The export was stopped. {os.path.basename(path)} was not touched: "
                                      f"the write went to a file of its own, which has been removed."))

        task.writeComplete.connect(complete)
        task.errorOccurred.connect(failed)
        try:
            task.taskTerminated.connect(stopped)
        except (AttributeError, TypeError):  # noqa: BLE001
            pass

    try:
        task_id, _entry = register_task(task, f"export_layer {driver}", connect=wire)
    except Exception:  # noqa: BLE001
        _discard_staged_write(staging)
        return None
    return {"task_id": task_id, "status": "running", "exporting": path, "feature_count": count,
            **coded_fact(hint="export_running", task_id=task_id),
            "poll": {"tool": "get_task_status", "args": {"task_id": task_id},
                     "interval_s": 0.4, "label": f"Writing {os.path.basename(path)}"}}


def _folder_letters(folder: str) -> str:

    return re.sub(r"[\W_]+", "", folder.casefold())


def _misspelt_folder(path: str) -> dict | None:











    folder = os.path.dirname(os.path.abspath(os.path.expanduser(path)))
    if not folder or os.path.isdir(folder):
        return None
    basename = os.path.basename(path).lower()
    wanted = _folder_letters(folder)
    for layer in QgsProject.instance().mapLayers().values():
        try:
            source = layer.source().split("|", 1)[0]
        except Exception:  # nosec B112
            continue
        if (os.path.basename(source).lower() == basename and os.path.isfile(source)
                and _folder_letters(os.path.dirname(os.path.abspath(source))) == wanted):
            return tool_error(
                f"The folder {folder} does not exist, and the project already reads {os.path.basename(source)} "
                f"from {source} (layer {layer.name()!r}). Nothing was written.",
                "INVALID_ARGS", hint="export_folder_misspelt", existing_path=source,
            )
    return None


_RASTER_DRIVERS = {".tif": "GTiff", ".tiff": "GTiff", ".vrt": "VRT", ".img": "HFA", ".asc": "AAIGrid"}


def _export_raster(layer, path: str, args: dict) -> dict:

    ext = os.path.splitext(path)[1].lower()
    driver = _RASTER_DRIVERS.get(ext)
    if not driver:
        return tool_error(f"Unsupported raster format '{ext}'.", "INVALID_ARGS",
                          ".tif works for a raster layer.")
    if args.get("crs") and args["crs"] != layer.crs().authid():
        return tool_error(
            f"export_layer copies a raster on its own grid and does not reproject it ({layer.crs().authid()} "
            f"to {args['crs']}).", "INVALID_ARGS", hint="export_raster_reproject", path=path)
    if os.path.exists(path) and not args.get("overwrite"):
        return tool_error(f"{path} already exists.", "INVALID_ARGS", hint="export_raster_exists", path=path)
    source = layer.source().split("|", 1)[0]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    staging = _staging_path(path)
    try:
        if layer.providerType() == "gdal" and os.path.isfile(source):
            from osgeo import gdal

            copied = gdal.Translate(staging, source, format=driver)
            if copied is None:
                raise RuntimeError(gdal.GetLastErrorMsg() or "GDAL could not write the copy")
            del copied
        else:
            from qgis.core import QgsRasterFileWriter, QgsRasterPipe

            provider = layer.dataProvider()
            pipe = QgsRasterPipe()
            if not pipe.set(provider.clone()):
                raise RuntimeError("the raster pipe refused the layer's provider")
            writer = QgsRasterFileWriter(staging)
            writer.setOutputFormat(driver)
            code = writer.writeRaster(pipe, layer.width(), layer.height(), layer.extent(), layer.crs(),
                                      QgsProject.instance().transformContext())
            if int(code) != 0:
                raise RuntimeError(f"QgsRasterFileWriter returned error {int(code)}")
        released = _release_layers_at_path(path, skip_ids={layer.id()})




        staged_stem = os.path.splitext(staging)[0]
        target_stem = os.path.splitext(path)[0]
        for produced in sorted(_raster_staged_files(staging), key=lambda f: f == staging):
            retry_file_op(os.replace, produced, target_stem + produced[len(staged_stem):])
    except Exception as exc:  # noqa: BLE001
        for produced in _raster_staged_files(staging):
            remove_quietly(produced)
        return tool_error(f"Could not write {path}: {exc}", "EXECUTION_FAILED",
                          "The folder needs write access, or another folder works.")
    written = QgsRasterLayer(path, "check")
    out = {"exported": path, "format": driver, "kind": "raster",
           "width": written.width(), "height": written.height(), "band_count": written.bandCount(),
           "crs": crs_ref(written.crs())}
    if written.isValid() and written.bandCount():
        provider = written.dataProvider()
        out["nodata"] = provider.sourceNoDataValue(1) if provider.sourceHasNoDataValue(1) else None
    if released:
        out["replaced_layers"] = released
    return out


def _csv_geometry(layer) -> tuple[str, list[str]] | dict:










    wkb = layer.wkbType()
    by_name = {field.name().casefold(): field.name() for field in layer.fields()}
    if (QgsWkbTypes.flatType(wkb) == enum_member(QgsWkbTypes, "Type", "Point")
            and not QgsWkbTypes.hasM(wkb)):
        has_z = QgsWkbTypes.hasZ(wkb)
        columns = ["X", "Y", "Z"] if has_z else ["X", "Y"]
        if not any(column.casefold() in by_name for column in columns):
            return ("GEOMETRY=AS_XYZ" if has_z else "GEOMETRY=AS_XY", columns)
    if "wkt" not in by_name:
        return ("GEOMETRY=AS_WKT", ["WKT"])
    return tool_error(
        f"Layer '{layer.name()}' already has a field named {by_name['wkt']}, the name the CSV geometry column "
        f"would take, so the file could not be read back. Nothing was written.",
        "INVALID_ARGS", hint="csv_geometry_column_clash", field=by_name["wkt"], layer=layer.name(),
    )


def _export_layer(args: dict) -> dict:
    from qgis.core import QgsCoordinateReferenceSystem, QgsVectorFileWriter

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])

    path = args["path"]
    path_error = validate_path(path, write=True)
    if path_error:
        return {"_error": path_error}


    path = os.path.abspath(os.path.expanduser(path))
    misspelt = _misspelt_folder(path)
    if misspelt:
        return misspelt
    if isinstance(layer, QgsRasterLayer):
        return _export_raster(layer, path, args)
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is neither a vector nor a raster layer"}

    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":


        from .cad_export import export_dxf

        return export_dxf(layer, path, args)
    if ext == ".dwg":
        return tool_error("QGIS cannot write DWG: GDAL reads it but has no DWG writer.", "INVALID_ARGS",
                          hint="dwg_not_writable")
    driver_map = {
        ".gpkg": "GPKG",
        ".geojson": "GeoJSON",
        ".json": "GeoJSON",
        ".shp": "ESRI Shapefile",
        ".csv": "CSV",
        ".kml": "KML",
        ".xlsx": "XLSX",
    }
    driver = driver_map.get(ext)
    if not driver:
        return {"_error": f"Unsupported format '{ext}'. Formats: .gpkg, .geojson, .shp, .csv, .kml, .xlsx, .dxf"}

    crs = QgsCoordinateReferenceSystem(args["crs"]) if args.get("crs") else layer.crs()

    if not crs.isValid() and (layer.isSpatial() or args.get("crs")):
        return {"_error": f"Invalid CRS: {args.get('crs')}"}

    geometry_written = None
    if driver == "CSV" and layer.isSpatial() and not args.get("geometryless"):
        csv_geometry = _csv_geometry(layer)
        if isinstance(csv_geometry, dict):
            return csv_geometry
        csv_option, columns = csv_geometry
        geometry_written = {"columns": columns, "crs": crs.authid() or crs.description()}

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)





    released = [] if driver == "GPKG" else _release_layers_at_path(path, skip_ids={layer.id()})

    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = driver
    options.fileEncoding = "UTF-8"
    options.onlySelectedFeatures = bool(args.get("selected_only", False))





    options.layerName = str(args.get("layer_name_in_file") or layer.name())


    options.saveMetadata = True
    options.layerMetadata = layer.metadata()
    shapefile_mapping = None
    if driver == "ESRI Shapefile":
        safe_names, shapefile_mapping = _shapefile_field_names(layer)
        options.attributesExportNames = safe_names
        options.fieldNameSource = enum_member(QgsVectorFileWriter, "FieldNameSource", "Original")






    replaced_layer_in_place = False
    stage_from = None




    staging = _staging_path(path)
    if driver == "GPKG":





        field_names = {field.name().casefold() for field in layer.fields()}
        if "fid" in field_names:
            primary_key = "_export_fid"
            suffix = 2
            while primary_key.casefold() in field_names:
                primary_key = f"_export_fid_{suffix}"
                suffix += 1
            options.layerOptions = [f"FID={primary_key}"]
        if os.path.exists(path):







            import sqlite3

            from ..core.snapshot import INLINE_BACKUP_BYTES
            from ..core.snapshot_files import _backup_size, _sqlite_copy

            try:
                if _backup_size(path) > INLINE_BACKUP_BYTES:
                    stage_from = path
                else:
                    _sqlite_copy(path, staging)
            except (OSError, sqlite3.Error) as exc:
                _discard_staged_write(staging)
                return _staging_failure(path, exc)
            options.actionOnExistingFile = enum_member(
                QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteLayer")
            replaced_layer_in_place = True
    if geometry_written:
        options.layerOptions = [csv_option]
    if driver == "CSV" and IS_WINDOWS:



        options.layerOptions = list(options.layerOptions or []) + ["WRITE_BOM=YES"]
    if args.get("geometryless"):
        options.overrideGeometryType = enum_member(QgsWkbTypes, "Type", "NoGeometry")
    if layer.isSpatial() and crs != layer.crs():
        options.ct = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance())

    remote = not options.onlySelectedFeatures and is_remote_vector(layer)
    if options.onlySelectedFeatures:
        count = layer.selectedFeatureCount()
    elif remote:




        count = feature_count_of(layer)
    else:
        count = layer.featureCount()




    if remote or stage_from or count > limits.current("SYNC_FEATURE_LOOP_MAX"):
        started = _export_in_background(layer, path, staging, driver, options, count, released,
                                        extra={"geometry_written": geometry_written} if geometry_written else None,
                                        stage_from=stage_from)
        if started is not None:
            return started
    if stage_from:

        import sqlite3

        from ..core.snapshot_files import _sqlite_copy

        try:
            _sqlite_copy(stage_from, staging)
        except (OSError, sqlite3.Error) as exc:
            _discard_staged_write(staging)
            return _staging_failure(path, exc)

    error_code, error_msg, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, staging, QgsProject.instance().transformContext(), options
    )

    if error_code != enum_member(QgsVectorFileWriter, "WriterError", "NoError"):
        _discard_staged_write(staging)
        return _export_failure(path, error_msg)

    publish_failure, refreshed = _publish_export(staging, path, driver)
    if publish_failure:
        _discard_staged_write(staging)
        return {**publish_failure,
                "_error": f"The export was written but could not be put in place at {path}: "
                          f"{publish_failure['_error']}"}

    if shapefile_mapping:
        expected_names = [shapefile_mapping[field.name()] for field in layer.fields()]
        written = QgsVectorLayer(path, "shapefile export verification", "ogr")
        actual_names = written.fields().names() if written.isValid() else []
        if actual_names != expected_names:
            return {"_error": (f"The shapefile was written, but its field names were not preserved: expected "
                               f"{expected_names!r}, got {actual_names!r}. The export is not reported as successful.")}

    out = {"exported": path, "format": driver, "feature_count": count, "selected_only": options.onlySelectedFeatures}
    if geometry_written:
        out["geometry_written"] = geometry_written
    if shapefile_mapping and any(old != new for old, new in shapefile_mapping.items()):
        out["field_name_mapping"] = shapefile_mapping
        out["warning"] = ("The export used the explicit mapping in field_name_mapping; the source layer was "
                          "not changed.")
        out.update(coded_fact(hint="shapefile_field_names_mapped", max_chars=SHAPEFILE_FIELD_NAME_MAX))
    if driver == "GPKG":
        out["layer_name_in_file"] = options.layerName
        out.update(_style_into_export(layer, path, driver, options.layerName))
        if replaced_layer_in_place:
            out["kept_other_tables"] = True
        if refreshed:
            out["refreshed_layers"] = refreshed
    if released:
        out["replaced_layers"] = released
    return out



_MEASURE_FUNCTIONS = frozenset({"$area", "$perimeter", "$length"})


def _add_field(args: dict) -> dict:
    from qgis.core import (
        QgsExpression,
        QgsExpressionContext,
        QgsExpressionContextUtils,
        QgsFeatureRequest,
        QgsField,
    )

    layer = _find_layer(args["layer_name"])
    if not layer:
        return _layer_not_found_error(args["layer_name"])
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is not a vector layer"}

    field_name = args["field_name"]
    kind = str(args.get("field_type") or "string").strip().lower()
    expression_str = args.get("expression")




    qtype = FIELD_TYPES.get(kind)
    if qtype is None:
        return {
            "_error": f"field_type {args.get('field_type')!r} is not a field kind this tool builds.",
            "code": "INVALID_ARGS",
            "suggestion": f"One of: {', '.join(sorted(FIELD_TYPES))}.",
        }



    measurement_mode = args.get("measurement_mode", "ground")
    if not isinstance(measurement_mode, str) or measurement_mode not in {"ground", "planar_project"}:
        return {"_error": "measurement_mode must be ground or planar_project", "_code": "INVALID_ARGS"}
    expr = None
    measured_on = ""
    planar_transform = None
    planar_crs = None
    if expression_str:
        expr = QgsExpression(expression_str)





        if _MEASURE_FUNCTIONS.intersection(expr.referencedFunctions()):
            if measurement_mode == "ground":
                measured_on = ground.measure_on_ellipsoid(expr, layer)
            else:
                from qgis.core import QgsDistanceArea, QgsUnitTypes

                project = QgsProject.instance()
                planar_crs = project.crs()
                if not planar_crs.isValid() or not layer.crs().isValid():
                    return {"_error": "Planar measurement requires valid layer and project CRS",
                            "_code": "INVALID_ARGS"}
                planar_transform = QgsCoordinateTransform(layer.crs(), planar_crs, project)
                calculator = QgsDistanceArea()
                calculator.setSourceCrs(planar_crs, project.transformContext())
                calculator.setEllipsoid("NONE")
                expr.setGeomCalculator(calculator)
                expr.setDistanceUnits(planar_crs.mapUnits())
                expr.setAreaUnits(QgsUnitTypes.distanceToAreaUnit(planar_crs.mapUnits()))
        if expr.hasParserError():
            return {"_error": f"Invalid expression: {expr.parserErrorString()}", "_code": "INVALID_ARGS"}








        ceiling = limits.current("SYNC_FEATURE_LOOP_MAX")
        total = feature_count_of(layer)
        if total is None or total > ceiling:
            has = (f"has more than {ceiling} features" if total is not None
                   else "gives its feature count only over the network")
            return tool_error(
                f"Layer '{layer.name()}' {has}; "
                "populating a new field with an expression would freeze QGIS.",
                code="INVALID_ARGS", hint="field_expression_loop_ceiling",
            )



    if layer.fields().indexOf(field_name) < 0:
        too_long = shapefile_field_name_error(layer, field_name)
        if too_long:
            return too_long

    if layer.isEditCommandActive():
        return tool_error(
            f"Layer {layer.name()!r} has an active QGIS edit command.",
            code="INVALID_ARGS", hint="edit_command_active",
        )







    started_here, cannot_edit = vector_write.open_edit(layer, "add fields")
    if cannot_edit:
        return cannot_edit
    was_editing = not started_here




    finished_command = False
    layer.beginEditCommand(f"Add field {field_name}")
    try:
        type_plan: dict = {}
        existing_index = layer.fields().indexOf(field_name)
        reused = existing_index >= 0
        existing_kind = ""
        if reused:



            existing_field = layer.fields().at(existing_index)
            existing_kind = _field_kind_name(existing_field)


            try:
                same_type = existing_field.type() == qtype
            except Exception:  # noqa: BLE001
                same_type = True
            if existing_kind and not same_type:
                return tool_error(
                    f"Layer {layer.name()!r} already has a field named {field_name!r}, and it is "
                    f"{existing_kind}, not {kind}.", "INVALID_ARGS",
                    hint="field_name_taken_other_type", existing_type=existing_kind,
                )
        else:




            type_plan = vector_write.plan_field_type(layer, qtype)
            if type_plan.get("type_name"):
                new_field = QgsField(field_name, type_plan["type"], type_plan["type_name"],
                                     type_plan["length"], type_plan["precision"])
            else:
                new_field = QgsField(field_name, type_plan.get("type", qtype))
            layer.addAttribute(new_field)
            layer.updateFields()



        idx = layer.fields().indexOf(field_name)
        if idx < 0:
            return tool_error(
                f"The provider refused to add field {field_name!r} to {layer.name()!r}.",
                code="EXECUTION_FAILED", hint="field_refused",
            )

        populated_features = 0
        if expr is not None:
            context = QgsExpressionContext()
            context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            expr.prepare(context)




            columns = list(expr.referencedColumns())
            request = QgsFeatureRequest()
            if "*" not in columns:
                request.setSubsetOfAttributes(columns, layer.fields())
            if not (expr.needsGeometry() or planar_transform is not None):
                request.setFlags(enum_member(QgsFeatureRequest, "Flag", "NoGeometry"))
            for feat in layer.getFeatures(request):
                evaluated_feature = feat
                if planar_transform is not None and feat.hasGeometry():
                    from qgis.core import QgsCsException, QgsFeature, QgsGeometry

                    geometry = QgsGeometry(feat.geometry())
                    try:
                        geometry.transform(planar_transform)
                    except QgsCsException as exc:
                        return {"_error": f"Planar geometry transformation failed: {exc}",
                                "_code": "INVALID_ARGS"}
                    evaluated_feature = QgsFeature(feat)
                    evaluated_feature.setGeometry(geometry)
                context.setFeature(evaluated_feature)
                value = expr.evaluate(context)
                if expr.hasEvalError():
                    return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
                if not layer.changeAttributeValue(feat.id(), idx, value):
                    return tool_error(
                        f"QGIS refused the calculated value for feature {feat.id()} in {field_name!r}.",
                        code="EXECUTION_FAILED",
                    )
                populated_features += 1

        layer.endEditCommand()
        finished_command = True
    finally:
        if not finished_command:
            layer.destroyEditCommand()
            if started_here:
                vector_write.force_out_of_edit(layer)

    if not was_editing:
        if not layer.commitChanges():
            errors = layer.commitErrors()



            vector_write.force_out_of_edit(layer)
            return commit_failure_error(layer, errors)

    result = {
        "field_added": field_name,

        "type": existing_kind or kind,  # noqa: E501
        "reused": reused,


        "layer": layer.name(),
        "populated": bool(expression_str),
        "populated_features": populated_features,
        "committed": not was_editing,
    }
    if type_plan.get("note"):
        result["type_note"] = type_plan["note"]
    if type_plan.get("type_name"):
        result["provider_type"] = type_plan["type_name"]
    if was_editing:
        result["note"] = "added to the open edit session, not committed"
        result.update(coded_fact(hint="field_in_open_edit_session"))

    if expr is not None and _MEASURE_FUNCTIONS.intersection(expr.referencedFunctions()):
        from qgis.core import QgsUnitTypes



        result["measure_units"] = {"area": QgsUnitTypes.toString(expr.areaUnits()),
                                   "length": QgsUnitTypes.toString(expr.distanceUnits())}
    if planar_crs is not None:
        result["measurement_mode"] = "planar_project"
        result["measurement_crs"] = planar_crs.authid() or planar_crs.description()
        result["measure_note"] = (
            "Geometry measurements use the project CRS plane and its map units "
            "(squared for area), without ellipsoidal correction. Source geometries are unchanged."
        )
    if measured_on:
        result["measure_note"] = (
            f"This project measures planar (Project Properties > General, ellipsoid NONE), so $area and "
            f"$length would have answered in {layer.crs().authid()} units. They were measured on the "
            f"{measured_on} ellipsoid instead, so '{field_name}' holds ground square metres or metres. "
            f"No reprojection was needed and '{layer.name()}' itself carries the field."
        )
    return result

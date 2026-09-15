# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Loading, exporting and field-writing handlers, plus the file-release helpers processing_tools reuses when an output path is already open in."""




from __future__ import annotations

import os
import re
import shutil
import time
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
from ..core.host_platform import retry_file_op
from ..core.qt_compat import enum_member
from ..core.security import validate_path
from ..core.tool_registry import tool_error
from ._compat import FIELD_TYPES
from .csv_loader import CSV_EXTENSIONS
from .layer_lookup import _find_layer, _layer_not_found_error

_CONTAINER_EXTENSIONS = (".gpkg", ".sqlite", ".gdb", ".kml", ".kmz", ".gml", ".gpx", ".dxf")


def _virtual_source_path(path: str) -> str | None:
    """Return the real archive path for a GDAL /vsizip/ source."""
    if not path.startswith("/vsizip/"):
        return None
    match = re.match(r"^/vsizip/(.+?\.zip)(?:/.*)?$", path, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _sublayer_names(path: str) -> list:
    """The vector layers inside a container file, in file order."""
    try:
        from qgis.core import QgsProviderRegistry
        details = QgsProviderRegistry.instance().querySublayers(path)
        names = [d.name() for d in details if d.providerKey() == "ogr" and d.name()]
        return list(dict.fromkeys(names))
    except Exception:  # noqa: BLE001 - older QGIS or an odd file: one layer
        return []




_ZIP_VECTOR_EXTENSIONS = (".shp", ".gpkg", ".geojson", ".kml", ".kmz", ".gml", ".gpx", ".fgb", ".tab",
                          ".mif", ".dxf", ".sqlite")


def _zip_listing(path: str) -> tuple[list, list, str]:
    """``(vector members, every name, problem)`` for a zip archive, folders included."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        return [], [], str(exc) or type(exc).__name__
    members = [entry for entry in names
               if entry.lower().endswith(_ZIP_VECTOR_EXTENSIONS)
               and not entry.startswith("__MACOSX/") and not os.path.basename(entry).startswith("._")]
    return members, names, ""


def _zip_member_uri(path: str, member: str) -> str:
    return f"/vsizip/{path.replace(chr(92), '/')}/{member}"


def describe_sublayers(path: str, names: list) -> list:
    """[{name, geometry, feature_count, crs}] read without adding anything to the project."""
    out = []
    for name in names:
        probe = QgsVectorLayer(f"{path}|layername={name}", name, "ogr")
        if not probe.isValid():
            out.append({"name": name, "valid": False})
            continue
        out.append({
            "name": name,
            "geometry": QgsWkbTypes.displayString(probe.wkbType()),
            "feature_count": probe.featureCount(),
            "crs": probe.crs().authid(),
        })
    return out


_REMOTE_VECTOR_PREFIXES = ("http://", "https://", "/vsicurl/")


def _add_vector_layer_is_remote(args: dict) -> bool:
    """Whether this add_vector_layer call may run off the main thread."""





    return str(args.get("path") or "").startswith(_REMOTE_VECTOR_PREFIXES)


def _add_vector_layer(args: dict) -> dict:
    path = args["path"]
    name = args.get("name") or os.path.splitext(os.path.basename(path))[0]

    if path.startswith(_REMOTE_VECTOR_PREFIXES):
        from .data_tools import _add_vector_from_url
        url = path.replace("/vsicurl/", "") if path.startswith("/vsicurl/") else path
        return _add_vector_from_url({"url": url, "layer_name": name})

    real_path = _virtual_source_path(path) or path
    path_error = validate_path(real_path, write=False)
    if path_error:
        return {"_error": path_error}

    if not os.path.exists(real_path):
        return {"_error": f"File not found: {real_path}"}

    if path.lower().endswith(CSV_EXTENSIONS):
        from .csv_loader import load_csv
        return load_csv(path, name, args.get("crs"))

    uri = path
    wanted = str(args.get("layer") or "").strip()
    archive_members: list = []
    if path.lower().endswith(".zip"):
        members, names, problem = _zip_listing(path)
        if problem:
            return {"_error": f"Could not read the archive {os.path.basename(path)}: {problem}",
                    "suggestion": ("A OneDrive or Dropbox file kept online only must be made available offline "
                                   "first; otherwise ask the user for the extracted files.")}
        if wanted:
            picked = [m for m in members
                      if wanted in (m, os.path.basename(m), os.path.splitext(os.path.basename(m))[0])]
            if not picked:
                return {"_error": f"No vector file named {wanted!r} in {os.path.basename(path)}.",
                        "layers": members[:50], "suggestion": "Call again with layer set to one of the listed files."}
            members = picked[:1]
        if not members:
            return {"_error": f"{os.path.basename(path)} holds no vector file QGIS reads.", "files": names[:50],
                    "suggestion": "Say what the archive holds instead; a raster inside it loads with kind='raster'."}
        if len(members) > 1:

            return {
                "path": path,
                "layers": members[:50],
                "_note": (f"{os.path.basename(path)} holds {len(members)} vector files, none added yet. Call add_data "
                          "again with layer=<file> for each one wanted, or answer from this list."),
            }
        uri = _zip_member_uri(path, members[0])
        if not args.get("name"):
            name = os.path.splitext(os.path.basename(members[0]))[0]
    elif path.startswith("/vsizip/"):
        archive_members = _zip_listing(real_path)[0]
    if path.lower().endswith(_CONTAINER_EXTENSIONS):
        names = _sublayer_names(path)
        if wanted:
            if names and wanted not in names:
                return {"_error": f"No layer named {wanted!r} in {os.path.basename(path)}.",
                        "sublayers": names, "suggestion": "Call again with one of the listed layer names."}
            uri = f"{path}|layername={wanted}"
            if not args.get("name"):
                name = wanted
        elif len(names) > 1:

            return {
                "path": path,
                "layers": describe_sublayers(path, names),
                "_note": (f"{os.path.basename(path)} holds {len(names)} layers, none added yet. Call add_data "
                          "again with layer=<name> for each one wanted, or answer from this list."),
            }
        elif len(names) == 1:
            uri = f"{path}|layername={names[0]}"
            if not args.get("name"):
                name = names[0]

    layer = QgsVectorLayer(uri, name, "ogr")
    if not layer.isValid():
        if archive_members:
            return {"_error": f"Failed to load vector layer from: {uri}", "layers": archive_members[:50],
                    "suggestion": "Pass the zip itself with layer set to one of the listed files."}
        return {"_error": f"Failed to load vector layer from: {uri}"}

    crs_outcome = _apply_requested_crs(layer, args.get("crs"))
    QgsProject.instance().addMapLayer(layer)
    out = {
        "name": layer.name(),
        "layer_id": layer.id(),
        "feature_count": layer.featureCount(),
        "crs": layer.crs().authid(),
    }
    out.update(crs_outcome)
    return out


def _apply_requested_crs(layer, wanted) -> dict:
    """What ``crs`` did to a local vector layer, as result keys."""







    provider = layer.dataProvider()
    declared = provider.crs() if provider is not None else layer.crs()
    wanted = str(wanted or "").strip()
    if not wanted:
        if declared.isValid() or layer.crs().isValid():
            return {}
        return {"warning": ("The file declares no CRS, so QGIS cannot place it. Ask the user which CRS it was "
                            "drawn in, then call set_layer_crs on this layer.")}
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
    return {"warning": (f"The file declares {named}, so crs {wanted} was not applied: that would move the data "
                        "without reprojecting it. Reproject with native:reprojectlayer, or call set_layer_crs if "
                        "the file's own CRS is wrong.")}


def _add_raster_layer(args: dict) -> dict:
    path = args["path"]
    name = args.get("name") or os.path.splitext(os.path.basename(path))[0]

    if path.startswith(("http://", "https://", "/vsicurl/", "ftp://")):
        return {"_error": "add_raster_layer loads LOCAL raster files only. For remote imagery use "
                          "add_xyz_layer (tile services) or add_wms_layer (WMS)."}

    path_error = validate_path(path, write=False)
    if path_error:
        return {"_error": path_error}

    if not os.path.exists(path):
        return {"_error": f"File not found: {path}"}

    source = path
    subdataset = str(args.get("layer") or "").strip()
    if path.lower().endswith((".nc", ".hdf")) and subdataset:
        source = f'NETCDF:"{path}":{subdataset}'
    layer = QgsRasterLayer(source, name)
    if not layer.isValid():
        return {"_error": f"Failed to load raster layer from: {source}"}

    from .elevation_style import apply_elevation_style, elevation_stretch
    from .stac_tools import normalise_crs
    normalise_crs(layer)
    styled = apply_elevation_style(layer, elevation_stretch(source, name))
    QgsProject.instance().addMapLayer(layer)
    return {
        "name": layer.name(),
        "layer_id": layer.id(),
        "crs": layer.crs().authid(),
        "width": layer.width(),
        "height": layer.height(),
        **({"styled": styled} if styled else {}),
    }














POINT_CLOUD_EXTENSIONS = (".copc.laz", ".laz", ".las", ".vpc", "ept.json")
_REMOTE_POINT_CLOUD_PROVIDERS = ("copc", "ept", "vpc")


def point_cloud_provider(source: str) -> str:
    """The QGIS provider key for this point cloud source, or "" when it is not one."""
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
    """True when this QGIS build ships the provider."""






    try:
        from qgis.core import QgsProviderRegistry
        return key in QgsProviderRegistry.instance().providerList()
    except Exception:  # noqa: BLE001 - a registry that will not answer is not a refusal
        return True


def _add_point_cloud_layer(args: dict) -> dict:
    """Open a LAS, LAZ, COPC or EPT point cloud, locally or over range requests."""
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
            return {"_error": f"A remote {os.path.splitext(source)[1]} has no index, so it can only be read "
                              "once downloaded in full.",
                    "suggestion": "Ask the provider for the COPC or EPT distribution of the same tiles, which "
                                  "reads in place, or download the file first and pass the local path."}
    else:
        path_error = validate_path(source, write=False)
        if path_error:
            return {"_error": path_error}
        if not os.path.exists(source):
            return {"_error": f"File not found: {source}"}

    if not _provider_available(provider):
        return {"_error": f"This QGIS build has no {provider} provider, so it cannot open point clouds.",
                "suggestion": "Install QGIS from the official packages, which carry PDAL, then reload."}

    try:
        from qgis.core import QgsPointCloudLayer
    except ImportError:
        return {"_error": "This QGIS version has no point cloud support.",
                "suggestion": "Point clouds need QGIS 3.26 or newer."}

    name = args.get("name") or os.path.splitext(os.path.basename(source.split("?", 1)[0]))[0] or "point cloud"
    layer = QgsPointCloudLayer(source, name, provider)
    if not layer.isValid():
        return {"_error": f"Failed to load point cloud from: {source}",
                "suggestion": "Check the file opens in QGIS by hand. A LAZ written before LAS 1.4 sometimes "
                              "needs converting first."}

    QgsProject.instance().addMapLayer(layer)
    out = {
        "name": layer.name(),
        "layer_id": layer.id(),
        "provider": provider,
        "crs": layer.crs().authid(),
    }
    try:
        out["point_count"] = int(layer.pointCount())
    except Exception:  # noqa: BLE001  # nosec B110 - the count is a courtesy, not the result
        pass
    try:
        stats = layer.attributes()
        out["attributes"] = [stats.at(i).name() for i in range(stats.count())]
    except Exception:  # noqa: BLE001  # nosec B110 - same
        pass
    if remote:
        out["_note"] = ("Read in place over HTTP range requests: nothing was downloaded, and only the octree "
                        "nodes the view needs are fetched.")
    return out


def _same_file(a: str, b: str) -> bool:
    """True when two path strings name the same file. Case-insensitive on Windows."""
    try:
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))
    except Exception:
        return False


def _release_layers_at_path(path: str, skip_ids=None, delete_existing: bool = False) -> list:
    """Remove project layers backed by `path` so the file can be overwritten."""










    if not os.path.exists(path):
        return []

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
        removed.append(layer.name())
        project.removeMapLayer(layer_id)

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




_EXPORT_SIDECARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".qmd", ".sbn", ".sbx", ".fix",
                    ".gpkg", ".gpkg-wal", ".gpkg-shm", ".geojson", ".json", ".csv", ".csvt", ".kml",
                    ".xlsx", ".vrt")


def _staging_path(path: str) -> str:
    """A sibling of *path* to write to first, in the same directory."""





    directory = os.path.dirname(path) or "."
    stem, ext = os.path.splitext(os.path.basename(path))
    return os.path.join(directory, f".{stem}.ai-agent-{uuid.uuid4().hex[:8]}.partial{ext}")


def _staged_files(staging: str) -> list:
    """Every file the writer produced for this staging name, sidecars included."""
    directory = os.path.dirname(staging) or "."
    stem = os.path.splitext(os.path.basename(staging))[0]
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names
            if os.path.splitext(name)[0] == stem and os.path.isfile(os.path.join(directory, name))]


def _raster_staged_files(staging: str) -> list:
    """Every file a raster driver wrote for this staging name, double extensions included."""





    directory = os.path.dirname(staging) or "."
    stem = os.path.splitext(os.path.basename(staging))[0]
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return [os.path.join(directory, name) for name in names
            if (name == stem or name.startswith(stem + ".")) and os.path.isfile(os.path.join(directory, name))]


def _publish_staged_write(staging: str, path: str) -> str:
    """Move the staged export onto *path*."""





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


def _detach_vector_layers_at_path(path: str) -> tuple[list[dict], str]:
    """Temporarily release clean vector providers while preserving layer objects."""







    from qgis.core import QgsMapLayerStyle

    matches = []
    for layer in QgsProject.instance().mapLayers().values():
        source = layer.source() or ""
        file_part = source.split("|", 1)[0]
        if not file_part or not _same_file(file_part, path):
            continue
        if not isinstance(layer, QgsVectorLayer):
            return [], (f"A non-vector layer named '{layer.name()}' is using {os.path.basename(path)}. "
                        "Close it or export to a different file.")
        if layer.isEditable() or layer.isModified():
            return [], (f"Layer '{layer.name()}' has unsaved edits in {os.path.basename(path)}. "
                        "Save or roll them back, or export to a different file.")
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
        layer.setDataSource(temporary, state["name"], "memory")
        if not layer.isValid() or layer.providerType() != "memory":
            _restore_detached_layers(detached)
            return [], f"Could not release layer '{state['name']}' before replacing {os.path.basename(path)}."
    return detached, ""


def _restore_detached_layers(states: list[dict]) -> list[str]:
    """Restore providers and presentation after `_detach_vector_layers_at_path`."""
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


def _publish_export(staging: str, path: str, driver: str) -> tuple[str, list[str]]:
    """Publish an export and keep open GeoPackage layers in the project."""
    if driver != "GPKG" or not os.path.exists(path):
        return _publish_staged_write(staging, path), []
    states, detach_error = _detach_vector_layers_at_path(path)
    if detach_error:
        return detach_error, []
    try:
        publish_error = _publish_staged_write(staging, path)
    finally:
        failed = _restore_detached_layers(states)
    if failed:
        return (f"The export was written, but these layers could not be refreshed: {', '.join(failed)}", [])
    return publish_error, [state["name"] for state in states]


def _discard_staged_write(staging: str) -> None:
    """Remove the staged export, whatever state it reached. The destination is untouched."""
    for produced_path in _staged_files(staging):
        try:
            os.remove(produced_path)
        except OSError:  # nosec B110 - a leftover .partial is not worth an error of its own
            continue


def _export_failure_message(path: str, error_msg: str) -> str:
    """GDAL's words, plus the real reason when Windows is holding the file."""






    if os.name == "nt" and os.path.exists(path) and "already exists" in (error_msg or "").lower():
        return (f"Export failed: {error_msg} On Windows a file that a loaded layer still holds "
                f"cannot be replaced. Remove the layer reading {os.path.basename(path)} from the "
                f"project first, or export to a different file name.")
    return f"Export failed: {error_msg}"


def _field_kind_name(field) -> str:
    """The FIELD_TYPES key that matches a field already on the layer, or ""."""
    try:
        actual = field.type()
    except Exception:  # noqa: BLE001 - a provider that cannot say keeps the caller's word
        return ""
    for name, qtype in FIELD_TYPES.items():
        if qtype == actual:
            return name
    return ""


def _delete_with_retry(path: str, attempts: int = 8, pause: float = 0.02) -> bool:
    """Delete `path` so the next GDAL writer sees a clean slate."""


















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
            except Exception:  # nosec B110 - GDAL cleanup is best effort
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


def _export_in_background(layer, path: str, staging: str, driver: str, options, count: int, released: list,
                          extra: dict | None = None):
    """Start the write as a ``QgsVectorFileWriterTask``, or None when it cannot."""






    try:
        from qgis.core import QgsVectorFileWriterTask

        from .processing_tools import register_task
    except ImportError:
        return None
    try:


        task = QgsVectorFileWriterTask(layer, staging, options)
    except (TypeError, ValueError):
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
            entry.setdefault("note", (f"The export was stopped. {os.path.basename(path)} was not touched: "
                                      f"the write went to a file of its own, which has been removed."))

        task.writeComplete.connect(complete)
        task.errorOccurred.connect(failed)
        try:
            task.taskTerminated.connect(stopped)
        except (AttributeError, TypeError):  # noqa: BLE001 - the entry still names the staged file
            pass

    try:
        task_id, _entry = register_task(task, f"export_layer {driver}", connect=wire)
    except Exception:  # noqa: BLE001 - no task manager: write it here
        _discard_staged_write(staging)
        return None
    return {"task_id": task_id, "status": "running", "exporting": path, "feature_count": count,
            "note": "Writing in the background, QGIS stays responsive. Poll get_task_status(task_id).",
            "poll": {"tool": "get_task_status", "args": {"task_id": task_id},
                     "interval_s": 0.4, "label": f"Writing {os.path.basename(path)}"}}


def _folder_letters(folder: str) -> str:
    """A folder path reduced to its letters and digits, folded: the spelling a mistyped copy keeps."""
    return re.sub(r"[\W_]+", "", folder.casefold())


def _misspelt_folder(path: str) -> dict | None:
    """Refuse a destination whose folder does not exist when the project already reads that file elsewhere."""










    folder = os.path.dirname(os.path.abspath(os.path.expanduser(path)))
    if not folder or os.path.isdir(folder):
        return None
    basename = os.path.basename(path).lower()
    wanted = _folder_letters(folder)
    for layer in QgsProject.instance().mapLayers().values():
        try:
            source = layer.source().split("|", 1)[0]
        except Exception:  # nosec B112 - a layer without a readable source is not a candidate
            continue
        if (os.path.basename(source).lower() == basename and os.path.isfile(source)
                and _folder_letters(os.path.dirname(os.path.abspath(source))) == wanted):
            return tool_error(
                f"The folder {folder} does not exist, and the project already reads {os.path.basename(source)} "
                f"from {source} (layer {layer.name()!r}). Nothing was written.",
                "INVALID_ARGS",
                f"To write into that file, call again with path {source!r}, copied exactly.",
            )
    return None


_RASTER_DRIVERS = {".tif": "GTiff", ".tiff": "GTiff", ".vrt": "VRT", ".img": "HFA", ".asc": "AAIGrid"}


def _export_raster(layer, path: str, args: dict) -> dict:
    """Write a raster layer to a file, cell for cell: same grid, same NoData, same CRS."""
    ext = os.path.splitext(path)[1].lower()
    driver = _RASTER_DRIVERS.get(ext)
    if not driver:
        return tool_error(f"Unsupported raster format '{ext}'.", "INVALID_ARGS",
                          "Use a .tif path for a raster layer.")
    if args.get("crs") and args["crs"] != layer.crs().authid():
        return tool_error(
            f"export_layer copies a raster on its own grid and does not reproject it ({layer.crs().authid()} "
            f"to {args['crs']}).", "INVALID_ARGS",
            "Reproject with run_processing gdal:warpreproject and pass this path as OUTPUT.")
    if os.path.exists(path) and not args.get("overwrite"):
        return tool_error(f"{path} already exists.", "INVALID_ARGS",
                          "Pass overwrite true only if the user agreed to replace it, or choose another path.")
    source = layer.source().split("|", 1)[0]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    staging = _staging_path(path)
    try:
        if layer.providerType() == "gdal" and os.path.isfile(source):
            from osgeo import gdal

            copied = gdal.Translate(staging, source, format=driver)
            if copied is None:
                raise RuntimeError(gdal.GetLastErrorMsg() or "GDAL could not write the copy")
            copied = None  # noqa: F841 - closing the dataset flushes it to disk
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
    except Exception as exc:  # noqa: BLE001 - the message is the answer, the staged file is ours to remove
        for produced in _raster_staged_files(staging):
            try:
                os.remove(produced)
            except OSError:  # nosec B112 - a leftover .partial is not worth an error of its own
                continue
        return tool_error(f"Could not write {path}: {exc}", "EXECUTION_FAILED",
                          "Check the folder is writable, or write to another folder.")
    written = QgsRasterLayer(path, "check")
    out = {"exported": path, "format": driver, "kind": "raster",
           "width": written.width(), "height": written.height(), "band_count": written.bandCount(),
           "crs": written.crs().authid()}
    if written.isValid() and written.bandCount():
        provider = written.dataProvider()
        out["nodata"] = provider.sourceNoDataValue(1) if provider.sourceHasNoDataValue(1) else None
    if released:
        out["replaced_layers"] = released
    return out


def _csv_geometry(layer) -> tuple[str, list[str]] | dict:
    """Pick the CSV layer option that carries the geometry, or refuse a name clash."""








    wkb = layer.wkbType()
    by_name = {field.name().casefold(): field.name() for field in layer.fields()}
    if QgsWkbTypes.flatType(wkb) == enum_member(QgsWkbTypes, "Type", "Point"):
        has_z = QgsWkbTypes.hasZ(wkb)
        columns = ["X", "Y", "Z"] if has_z else ["X", "Y"]
        if not any(column.casefold() in by_name for column in columns):
            return ("GEOMETRY=AS_XYZ" if has_z else "GEOMETRY=AS_XY", columns)
    if "wkt" not in by_name:
        return ("GEOMETRY=AS_WKT", ["WKT"])
    return tool_error(
        f"Layer '{layer.name()}' already has a field named {by_name['wkt']}, the name the CSV geometry column "
        f"would take, so the file could not be read back. Nothing was written.",
        "INVALID_ARGS",
        "Rename that field with rename_field first, export to .gpkg or .geojson, or pass geometryless true "
        "to write the attributes only.",
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
    misspelt = _misspelt_folder(path)
    if misspelt:
        return misspelt
    if isinstance(layer, QgsRasterLayer):
        return _export_raster(layer, path, args)
    if not isinstance(layer, QgsVectorLayer):
        return {"_error": f"Layer '{args['layer_name']}' is neither a vector nor a raster layer"}

    ext = os.path.splitext(path)[1].lower()
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
        return {"_error": f"Unsupported format '{ext}'. Use: .gpkg, .geojson, .shp, .csv, .kml, .xlsx"}

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






    replaced_layer_in_place = False




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



        options.layerName = str(args.get("layer_name_in_file") or layer.name())
        if os.path.exists(path):


            try:
                shutil.copy2(path, staging)
            except OSError as exc:
                return {"_error": f"Could not prepare the export beside {os.path.basename(path)}: {exc}",
                        "_code": "EXECUTION_FAILED",
                        "suggestion": "Check there is room on the volume and that the folder is writable."}
            options.actionOnExistingFile = enum_member(
                QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteLayer")
            replaced_layer_in_place = True
    if geometry_written:
        options.layerOptions = [csv_option]
    if args.get("geometryless"):
        options.overrideGeometryType = enum_member(QgsWkbTypes, "Type", "NoGeometry")
    if layer.isSpatial() and crs != layer.crs():
        options.ct = QgsCoordinateTransform(layer.crs(), crs, QgsProject.instance())

    count = layer.selectedFeatureCount() if options.onlySelectedFeatures else layer.featureCount()




    if count > limits.current("SYNC_FEATURE_LOOP_MAX"):
        started = _export_in_background(layer, path, staging, driver, options, count, released,
                                        extra={"geometry_written": geometry_written} if geometry_written else None)
        if started is not None:
            return started

    error_code, error_msg, *_ = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, staging, QgsProject.instance().transformContext(), options
    )

    if error_code != enum_member(QgsVectorFileWriter, "WriterError", "NoError"):
        _discard_staged_write(staging)
        return {"_error": _export_failure_message(path, error_msg)}

    publish_error, refreshed = _publish_export(staging, path, driver)
    if publish_error:
        _discard_staged_write(staging)
        return {"_error": f"The export was written but could not be put in place at {path}: {publish_error}",
                "_code": "EXECUTION_FAILED",
                "suggestion": _export_failure_message(path, publish_error)}

    out = {"exported": path, "format": driver, "feature_count": count, "selected_only": options.onlySelectedFeatures}
    if geometry_written:
        out["geometry_written"] = geometry_written
    if driver == "GPKG":
        out["layer_name_in_file"] = options.layerName
        if replaced_layer_in_place:
            out["kept_other_tables"] = True
        if refreshed:
            out["refreshed_layers"] = refreshed
    if released:
        out["replaced_layers"] = released
    return out



_MEASURE_FUNCTIONS = frozenset({"$area", "$perimeter", "$length"})


def _add_field(args: dict) -> dict:
    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils, QgsField

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
            "suggestion": f"Use one of: {', '.join(sorted(FIELD_TYPES))}.",
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
        if layer.featureCount() > ceiling:
            return tool_error(
                f"Layer '{layer.name()}' has more than {ceiling} features; "
                "populating a new field with an expression would freeze QGIS.",
                code="INVALID_ARGS",
                suggestion=(
                    "Run the 'qgis:fieldcalculator' processing algorithm through run_processing "
                    "instead, or call add_field without an expression and populate it separately."
                ),
            )



    was_editing = layer.isEditable()
    if not was_editing:
        if not layer.startEditing():
            return {"_error": f"Cannot start editing on layer '{layer.name()}'", "_code": "INVALID_ARGS"}

    existing_index = layer.fields().indexOf(field_name)
    reused = existing_index >= 0
    existing_kind = ""
    if reused:



        existing_field = layer.fields().at(existing_index)
        existing_kind = _field_kind_name(existing_field)


        try:
            same_type = existing_field.type() == qtype
        except Exception:  # noqa: BLE001 - a provider that cannot say is taken at its word
            same_type = True
        if existing_kind and not same_type:
            if not was_editing:
                layer.rollBack()
            return {
                "_error": (f"Layer {layer.name()!r} already has a field named {field_name!r}, and it is "
                           f"{existing_kind}, not {kind}."),
                "_code": "INVALID_ARGS",
                "existing_type": existing_kind,
                "suggestion": (f"Use another field_name, or pass field_type='{existing_kind}' to write into "
                               f"the field as it stands."),
            }
    else:
        layer.addAttribute(QgsField(field_name, qtype))
        layer.updateFields()



    idx = layer.fields().indexOf(field_name)
    if idx < 0:
        if not was_editing:
            layer.rollBack()
        return {
            "_error": f"Provider refused to add field '{field_name}' (read-only source?).",
            "_code": "INVALID_ARGS",
        }

    populated_features = 0
    if expr is not None:
        context = QgsExpressionContext()
        context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
        expr.prepare(context)
        for feat in layer.getFeatures():
            evaluated_feature = feat
            if planar_transform is not None and feat.hasGeometry():
                from qgis.core import QgsCsException, QgsFeature, QgsGeometry

                geometry = QgsGeometry(feat.geometry())
                try:
                    geometry.transform(planar_transform)
                except QgsCsException as exc:
                    if not was_editing:
                        layer.rollBack()
                    return {"_error": f"Planar geometry transformation failed: {exc}",
                            "_code": "INVALID_ARGS"}
                evaluated_feature = QgsFeature(feat)
                evaluated_feature.setGeometry(geometry)
            context.setFeature(evaluated_feature)
            value = expr.evaluate(context)
            if expr.hasEvalError():
                if not was_editing:
                    layer.rollBack()
                return {"_error": f"Expression evaluation error: {expr.evalErrorString()}", "_code": "INVALID_ARGS"}
            if layer.changeAttributeValue(feat.id(), idx, value):
                populated_features += 1

    if not was_editing:
        if not layer.commitChanges():
            errors = layer.commitErrors()
            layer.rollBack()
            return {"_error": f"Commit failed: {'; '.join(errors)}"}

    result = {
        "field_added": field_name,

        "type": existing_kind or kind,  # noqa: E501
        "reused": reused,


        "layer": layer.name(),
        "populated": bool(expression_str),
        "populated_features": populated_features,
        "committed": not was_editing,
    }
    if was_editing:
        result["note"] = "added to the open edit session (not committed, commit or discard it yourself)"

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

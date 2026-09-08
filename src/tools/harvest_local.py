# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""find_local_data: a background index of the home folder so the model can find the user's own geodata by name and by place."""















from __future__ import annotations

import os
import re
import sqlite3
import time
import uuid

from qgis.core import QgsTask

from ..core.logger import log, log_warning
from ..core.settings import state_dir
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import regions
from . import stac_tools as _stac

INDEX_FILE = "local_index.sqlite"
_VECTOR_EXT = {".shp", ".gpkg", ".fgb", ".geojson", ".json", ".kml"}
_RASTER_EXT = {".tif", ".tiff", ".vrt", ".jp2", ".nc"}
_EXTENSIONS = _VECTOR_EXT | _RASTER_EXT
_SKIP_DIRS = {
    "library", "node_modules", ".venv", "venv", "__pycache__", "site-packages",
    "caches", "cache", ".cache", "trash", ".trash", "applications",
}
_BUNDLE_SUFFIXES = (".app", ".photoslibrary", ".bundle", ".framework", ".xcodeproj", ".git")
_MAX_DEPTH = 6
_MAX_AGE_SECONDS = 24 * 3600
_SNIFF_BYTES = 4096
_MAX_PARSE_BYTES = 256 * 1024 * 1024
_FORCE_EXTENT_BYTES = 64 * 1024 * 1024
_COMMIT_EVERY = 200
_DEFAULT_LIMIT = 12
_MAX_LIMIT = 100
_GEOJSON_RE = re.compile(rb'"type"\s*:\s*"Feature(Collection)?"')
_COLUMNS = "path, name, ext, kind, size, mtime, atime, minx, miny, maxx, maxy, crs, detail"

_INDEX_TASK = None


def register_harvest_local_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="find_local_data",
        input_schema={
            "type": "object",
            "properties": {
                "description": {"type": "string", "maxLength": 80},
                "query": {
                    "type": "string",
                },
                "within_canvas": {
                    "type": "boolean",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_LIMIT,
                },
                "refresh": {
                    "type": "boolean",
                },
            },
        },
        handler=_find_local_data,
    ))





def index_path() -> str:
    return os.path.join(state_dir(), INDEX_FILE)


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema(conn: sqlite3.Connection):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS files ("
        "path TEXT PRIMARY KEY, name TEXT, ext TEXT, kind TEXT, size INTEGER, mtime REAL, atime REAL, "
        "minx REAL, miny REAL, maxx REAL, maxy REAL, crs TEXT, detail TEXT, run_id TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS files_atime ON files(atime DESC)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()


def _set_meta(conn: sqlite3.Connection, **values):
    conn.executemany(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        [(key, "" if value is None else str(value)) for key, value in values.items()],
    )
    conn.commit()


def _read_meta(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        conn = sqlite3.connect(path, timeout=5)
        try:
            rows = conn.execute("SELECT key, value FROM meta").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return {}
    return dict(rows)





def _skip_dir(name: str) -> bool:
    lower = name.lower()
    if lower.startswith("."):
        return True
    if lower in _SKIP_DIRS or "cache" in lower:
        return True
    return lower.endswith(_BUNDLE_SUFFIXES)


def _walk(root: str, is_canceled):
    """Yield (path, size, mtime, atime, ext) for every candidate file under root."""
    stack = [(root, 0)]
    while stack:
        folder, depth = stack.pop()
        if is_canceled():
            return
        try:
            iterator = os.scandir(folder)
        except OSError:
            continue
        with iterator:
            for entry in iterator:
                if is_canceled():
                    return
                name = entry.name
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if depth < _MAX_DEPTH and not _skip_dir(name):
                            stack.append((entry.path, depth + 1))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                if name.startswith("."):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext not in _EXTENSIONS:
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                yield entry.path, stat.st_size, stat.st_mtime, stat.st_atime, ext


def _looks_like_geojson(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            head = handle.read(_SNIFF_BYTES)
    except OSError:
        return False
    return bool(_GEOJSON_RE.search(head))


def _finite(values) -> bool:
    return all(isinstance(v, (int, float)) and v == v and abs(v) != float("inf") for v in values)


def _to_4326(minx, miny, maxx, maxy, srs):
    """A [minx, miny, maxx, maxy] in EPSG:4326 for a box in ``srs``, or None."""
    from osgeo import osr

    if not _finite((minx, miny, maxx, maxy)):
        return None
    if srs is None:
        if abs(minx) <= 180 and abs(maxx) <= 180 and abs(miny) <= 90 and abs(maxy) <= 90:
            return [minx, miny, maxx, maxy]
        return None
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    source = srs.Clone()
    source.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if source.IsSame(target):
        return [max(minx, -180.0), max(miny, -90.0), min(maxx, 180.0), min(maxy, 90.0)]
    steps = 8
    points = []
    for i in range(steps + 1):
        t = i / steps
        x = minx + (maxx - minx) * t
        y = miny + (maxy - miny) * t
        points.extend([(x, miny), (x, maxy), (minx, y), (maxx, y)])
    transform = osr.CoordinateTransformation(source, target)
    try:
        out = transform.TransformPoints(points)
    except Exception:
        return None
    inside = [p for p in out if _finite(p[:2]) and abs(p[0]) <= 180.0 and abs(p[1]) <= 90.0]
    if not inside:
        return None
    xs = [p[0] for p in inside]
    ys = [p[1] for p in inside]
    return [min(xs), min(ys), max(xs), max(ys)]


def _crs_label(srs) -> str | None:
    if srs is None:
        return None
    try:
        authority = srs.GetAuthorityName(None)
        code = srs.GetAuthorityCode(None)
        if authority and code:
            return f"{authority}:{code}"
        return srs.GetName() or None
    except Exception:
        return None


def _layer_extent(layer, size: int):
    """The OGR layer extent (minx, maxx, miny, maxy), forcing a scan only on small files."""
    try:
        extent = layer.GetExtent(0)
    except Exception:
        extent = None
    if extent is None and size <= _FORCE_EXTENT_BYTES:
        try:
            extent = layer.GetExtent(1)
        except Exception:
            extent = None
    if not extent or not _finite(extent) or extent == (0.0, 0.0, 0.0, 0.0):
        return None
    return extent


def _vector_facts(path: str, size: int):
    """(bbox, crs, detail) read with OGR. bbox is None when unknown."""
    from osgeo import ogr

    try:
        dataset = ogr.Open(path)
    except Exception:
        dataset = None
    if dataset is None:
        return None, None, "unreadable"
    try:
        layer_count = dataset.GetLayerCount()
        boxes = []
        crs = None
        geometry_types = []
        for index in range(min(layer_count, 50)):
            layer = dataset.GetLayer(index)
            if layer is None:
                continue
            try:
                geometry_types.append(ogr.GeometryTypeToName(layer.GetGeomType()))
            except Exception:  # nosec B110 - file metadata is optional
                pass
            extent = _layer_extent(layer, size)
            if extent is None:
                continue
            srs = layer.GetSpatialRef()
            if crs is None:
                crs = _crs_label(srs)
            box = _to_4326(extent[0], extent[2], extent[1], extent[3], srs)
            if box:
                boxes.append(box)
        bbox = None
        if boxes:
            bbox = [
                min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes),
            ]
        kinds = sorted({g for g in geometry_types if g})
        detail = ", ".join(kinds[:4]) if kinds else ""
        if layer_count > 1:
            detail = f"{layer_count} layers" + (f": {detail}" if detail else "")
        return bbox, crs, detail or None
    finally:
        dataset = None


def _raster_facts(path: str):
    """(bbox, crs, detail) read with GDAL. Falls back to the first subdataset (NetCDF)."""
    from osgeo import gdal

    try:
        dataset = gdal.Open(path, gdal.GA_ReadOnly)
    except Exception:
        dataset = None
    if dataset is None:
        return None, None, "unreadable"
    try:
        transform = dataset.GetGeoTransform(can_return_null=True)
        if transform is None:
            subdatasets = dataset.GetSubDatasets() or []
            if subdatasets:
                try:
                    inner = gdal.Open(subdatasets[0][0], gdal.GA_ReadOnly)
                except Exception:  # nosec B110 - file metadata is optional
                    inner = None
                if inner is not None:
                    dataset = inner
                    transform = dataset.GetGeoTransform(can_return_null=True)
        width, height = dataset.RasterXSize, dataset.RasterYSize
        detail = f"{dataset.RasterCount} band(s), {width}x{height}"
        srs = dataset.GetSpatialRef()
        crs = _crs_label(srs)
        if transform is None:
            return None, crs, detail
        xs, ys = [], []
        for col, row in ((0, 0), (width, 0), (0, height), (width, height)):
            xs.append(transform[0] + col * transform[1] + row * transform[2])
            ys.append(transform[3] + col * transform[4] + row * transform[5])
        bbox = _to_4326(min(xs), min(ys), max(xs), max(ys), srs)
        return bbox, crs, detail
    finally:
        dataset = None


def _describe_file(path: str, ext: str, size: int):
    """(kind, bbox, crs, detail) for one file, never raising."""
    kind = "raster" if ext in _RASTER_EXT else "vector"
    try:
        if kind == "raster":
            bbox, crs, detail = _raster_facts(path)
        elif ext in (".geojson", ".json", ".kml") and size > _MAX_PARSE_BYTES:
            bbox, crs, detail = None, None, "too large to read the extent"
        else:
            bbox, crs, detail = _vector_facts(path, size)
    except Exception as e:
        bbox, crs, detail = None, None, f"{type(e).__name__}: {e}"[:120]
    return kind, bbox, crs, detail


class _IndexTask(QgsTask):
    """Walk the home folder and write the index; no QGIS object is touched in run()."""

    def __init__(self, root: str, db_path: str):
        super().__init__("AI Agent: indexing local geodata")
        self.root = root
        self.db_path = db_path
        self.run_id = uuid.uuid4().hex[:12]
        self.count = 0
        self.error = ""

    def run(self) -> bool:
        try:
            from osgeo import gdal

            gdal.PushErrorHandler("CPLQuietErrorHandler")
        except Exception:
            gdal = None
        try:
            return self._run()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            return False
        finally:
            if gdal is not None:
                try:
                    gdal.PopErrorHandler()
                except Exception:  # nosec B110 - GDAL handler cleanup is best effort
                    pass

    def _run(self) -> bool:
        conn = _connect(self.db_path)
        try:
            _init_schema(conn)
            known = {
                row[0]: row[1:]
                for row in conn.execute(
                    "SELECT path, mtime, size, kind, minx, miny, maxx, maxy, crs, detail FROM files"
                )
            }
            _set_meta(conn, status="running", root=self.root, run_id=self.run_id, started_at=time.time(), error="")
            batch = []
            for path, size, mtime, atime, ext in _walk(self.root, self.isCanceled):
                if ext == ".json" and not _looks_like_geojson(path):
                    continue
                previous = known.get(path)
                if previous and previous[0] == mtime and previous[1] == size:
                    kind, minx, miny, maxx, maxy, crs, detail = previous[2:]
                else:
                    kind, bbox, crs, detail = _describe_file(path, ext, size)
                    minx, miny, maxx, maxy = bbox or (None, None, None, None)
                batch.append((
                    path, os.path.basename(path), ext, kind, size, mtime, atime,
                    minx, miny, maxx, maxy, crs, detail, self.run_id,
                ))
                self.count += 1
                if len(batch) >= _COMMIT_EVERY:
                    self._flush(conn, batch)
                    batch = []
            self._flush(conn, batch)
            if self.isCanceled():
                _set_meta(conn, status="canceled", file_count=self.count)
                return False
            conn.execute("DELETE FROM files WHERE run_id IS NULL OR run_id != ?", (self.run_id,))
            _set_meta(conn, status="complete", finished_at=time.time(), file_count=self.count)
            return True
        finally:
            conn.close()

    @staticmethod
    def _flush(conn: sqlite3.Connection, batch: list):
        if not batch:
            return
        conn.executemany(
            f"INSERT OR REPLACE INTO files({_COLUMNS}, run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch,
        )
        conn.commit()

    def finished(self, result: bool):
        global _INDEX_TASK
        if result:
            log(f"Local geodata index complete: {self.count} files under {self.root}")
        elif self.error:
            log_warning(f"Local geodata index failed: {self.error}")
            try:
                conn = _connect(self.db_path)
                try:
                    _init_schema(conn)
                    _set_meta(conn, status="error", error=self.error, file_count=self.count)
                finally:
                    conn.close()
            except sqlite3.Error:
                pass
        if _INDEX_TASK is self:
            _INDEX_TASK = None


def _start_index(root: str, db_path: str):
    global _INDEX_TASK
    from qgis.core import QgsApplication

    task = _IndexTask(root, db_path)
    _INDEX_TASK = task
    QgsApplication.taskManager().addTask(task)
    return task





def _iso(timestamp) -> str | None:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(timestamp)))
    except (TypeError, ValueError, OSError):
        return None


def _row_dict(row: tuple) -> dict:
    path, name, ext, kind, size, mtime, atime, minx, miny, maxx, maxy, crs, detail = row
    out = {
        "path": path,
        "name": name,
        "kind": kind,
        "ext": ext,
        "size_mb": round((size or 0) / (1024 * 1024), 2),
        "modified": _iso(mtime),
        "last_accessed": _iso(atime),
        "bbox_4326": None,
    }
    if minx is not None:
        out["bbox_4326"] = [round(minx, 5), round(miny, 5), round(maxx, 5), round(maxy, 5)]




        region = regions.region_of(out["bbox_4326"])
        if region:
            out["region"] = region
    if crs:
        out["crs"] = crs
    if detail:
        out["detail"] = detail
    return out


def _search(db_path: str, words: list, canvas_bbox, limit: int) -> list:
    clauses = []
    params: list = []
    for word in words:
        clauses.append("instr(lower(path), ?) > 0")
        params.append(word)
    if canvas_bbox is not None:
        west, south, east, north = canvas_bbox
        clauses.append("minx IS NOT NULL AND minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ?")
        params.extend([east, west, north, south])
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT {_COLUMNS} FROM files {where} ORDER BY atime DESC, mtime DESC LIMIT ?"  # nosec B608
    params.append(limit)
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [_row_dict(row) for row in rows]


def _find_local_data(args: dict) -> dict:
    query = str(args.get("query") or "")
    words = [w.lower() for w in query.split() if w.strip()]
    try:
        limit = int(args.get("limit") or _DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = _DEFAULT_LIMIT
    limit = max(1, min(limit, _MAX_LIMIT))
    within_canvas = bool(args.get("within_canvas"))
    refresh = bool(args.get("refresh"))

    root = os.path.expanduser("~")
    db_path = index_path()
    meta = _read_meta(db_path)
    task = _INDEX_TASK
    running = task is not None
    complete = meta.get("status") == "complete"
    finished_at = float(meta.get("finished_at") or 0) if complete else 0.0
    age = time.time() - finished_at if finished_at else None
    started = False
    if not running and (not complete or refresh or age is None or age > _MAX_AGE_SECONDS):
        try:
            task = _start_index(root, db_path)
        except Exception as e:
            return tool_error(
                f"Could not start the index task: {e}",
                "INDEX_FAILED",
                "Check get_message_log, then call find_local_data again.",
            )
        running = True
        started = True

    canvas_bbox = None
    if within_canvas:
        canvas_bbox = _stac._canvas_bbox_4326()
        if canvas_bbox is None:
            return tool_error(
                "Could not read the canvas extent in EPSG:4326.",
                "INVALID_ARGS",
                "Call get_canvas_extent; if the canvas is empty, set_canvas_extent first or drop within_canvas.",
            )

    results = []
    if os.path.exists(db_path):
        try:
            results = _search(db_path, words, canvas_bbox, limit)
        except sqlite3.Error as e:
            if not running:
                return tool_error(
                    f"The index is unreadable: {e}", "INDEX_UNREADABLE", "Call find_local_data with refresh=true.",
                )

    if running and not complete:
        status = "indexing"
    elif running:
        status = "refreshing"
    else:
        status = "ok"
    index = {
        "status": status,
        "root": root,
        "files_indexed": task.count if running and task is not None else int(meta.get("file_count") or 0),
        "indexed_at": _iso(finished_at) if finished_at else None,
        "cache": db_path,
    }
    out = {
        "status": status,
        "query": query,
        "words": words,
        "within_canvas": within_canvas,
        "count": len(results),
        "results": results,
        "index": index,
    }
    if canvas_bbox is not None:
        out["canvas_bbox_4326"] = canvas_bbox
    if status == "indexing":
        out["note"] = (
            f"The home folder is being indexed ({index['files_indexed']} files so far"
            f"{', just started' if started else ''}). Results are partial: call again in a few seconds."
        )
    elif status == "refreshing":
        out["note"] = "Results come from the previous index while it refreshes in the background."
    elif not results:
        out["note"] = (
            "No indexed file matches. Try fewer words, drop within_canvas, or pass refresh=true "
            "when the file is new. Files outside the home folder are not indexed."
        )
    return out

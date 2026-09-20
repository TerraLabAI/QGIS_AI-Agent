# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























































from __future__ import annotations

import math
import os
import struct
from array import array

from ..core import net, security




from ..core.background import heartbeat, run_on_main_thread
from ..core.logger import log_warning
from ..core.tool_registry import tool_error




DEFAULT_MAX_CELLS = 250_000


MAX_CELLS_CEILING = 1_000_000

MIN_GRID = 3
FORMATS = ("obj", "stl")
_EXTENSIONS = {".obj": "obj", ".stl": "stl"}

_BASE_FRACTION = 0.05


_CHECK_EVERY_ROWS = 32

_NEIGHBOURS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))
_TRIANGLE = struct.Struct("<12fH")





def _array_code(size: int, is_float: bool, is_signed: bool) -> str:

    if is_float:
        return {4: "f", 8: "d"}.get(size, "")
    codes = {1: "bB", 2: "hH", 4: "iI", 8: "qQ"}.get(size, "")
    if not codes:
        return ""
    code = codes[0] if is_signed else codes[1]
    return code if array(code).itemsize == size else ""


def _read_grid(args: dict) -> dict:






    from qgis.core import Qgis, QgsRasterLayer, QgsRectangle

    from ..core.qt_compat import enum_member
    from .layer_lookup import _find_layer, _layer_not_found_error

    reference = str(args.get("layer_name") or "").strip()
    layer = _find_layer(reference) if reference else None
    if layer is None:
        return {"error": _layer_not_found_error(reference)}
    if not isinstance(layer, QgsRasterLayer):
        return {"error": tool_error(
            f"{layer.name()!r} is not a raster layer, so it carries no elevation to build a mesh from.",
            "INVALID_ARGS",
            "Pass the name of the DEM raster. list_layers shows which layers are rasters.")}
    provider = layer.dataProvider()
    if provider is None or not layer.isValid():
        return {"error": tool_error(
            f"{layer.name()!r} has no readable data source.", "EXECUTION_FAILED",
            "Reload the DEM with add_raster_layer and try again.")}

    band = int(args.get("band") or 1)
    if band < 1 or band > max(1, layer.bandCount()):
        return {"error": tool_error(
            f"band must be between 1 and {layer.bandCount()} for {layer.name()!r}.", "INVALID_ARGS",
            "Pass the elevation band number, usually 1.")}

    full = layer.extent()
    asked = args.get("extent")
    if asked is not None:
        values = asked if isinstance(asked, (list, tuple)) else None
        if values is None or len(values) != 4:
            return {"error": tool_error(
                "extent must be four numbers: [xmin, ymin, xmax, ymax], in the layer's own CRS.",
                "INVALID_ARGS",
                f"Leave extent out for the whole DEM, or pass it in {layer.crs().authid() or 'the layer CRS'}.")}
        try:
            xmin, ymin, xmax, ymax = (float(v) for v in values)
        except (TypeError, ValueError):
            return {"error": tool_error("extent must be four numbers.", "INVALID_ARGS",
                                        "For example [420000, 6500000, 425000, 6505000].")}
        if xmax <= xmin or ymax <= ymin:
            return {"error": tool_error(
                "extent must read [xmin, ymin, xmax, ymax] with the maxima larger.", "INVALID_ARGS",
                "Swap the values so the second pair is the north-east corner.")}
        rect = QgsRectangle(xmin, ymin, xmax, ymax).intersect(full)
        if rect.isEmpty():
            return {"error": tool_error(
                f"That extent does not meet {layer.name()!r}, which covers "
                f"[{full.xMinimum():.6g}, {full.yMinimum():.6g}, {full.xMaximum():.6g}, "
                f"{full.yMaximum():.6g}] in {layer.crs().authid() or 'its own CRS'}.",
                "INVALID_ARGS",
                "Pass the extent in the layer's CRS, or leave it out for the whole DEM.")}
    else:
        rect = QgsRectangle(full)



    cell_x = float(layer.rasterUnitsPerPixelX() or 0.0)
    cell_y = float(layer.rasterUnitsPerPixelY() or 0.0)
    if cell_x <= 0 or cell_y <= 0:
        cell_x = rect.width() / max(1, layer.width())
        cell_y = rect.height() / max(1, layer.height())
    native_x = max(MIN_GRID, int(round(rect.width() / cell_x)) if cell_x > 0 else MIN_GRID)
    native_y = max(MIN_GRID, int(round(rect.height() / cell_y)) if cell_y > 0 else MIN_GRID)

    max_cells = int(args.get("max_cells") or DEFAULT_MAX_CELLS)
    max_cells = max(MIN_GRID * MIN_GRID, min(MAX_CELLS_CEILING, max_cells))
    nx, ny = native_x, native_y
    if nx * ny > max_cells:
        shrink = math.sqrt(max_cells / float(nx * ny))
        nx = max(MIN_GRID, int(nx * shrink))
        ny = max(MIN_GRID, int(ny * shrink))


        while nx * ny > max_cells and max(nx, ny) > MIN_GRID:
            if nx >= ny:
                nx -= 1
            else:
                ny -= 1

    block = provider.block(band, rect, nx, ny)
    if block is None or not block.isValid():
        return {"error": tool_error(
            f"QGIS could not read band {band} of {layer.name()!r} over that extent.", "EXECUTION_FAILED",
            "Check the DEM still opens (get_raster_band_stats) and that the extent covers data.")}

    data_type = provider.dataType(band)
    is_float = data_type in (enum_member(Qgis, "DataType", "Float32", None),
                             enum_member(Qgis, "DataType", "Float64", None))
    unsigned = {enum_member(Qgis, "DataType", name, None) for name in ("Byte", "UInt16", "UInt32")}
    try:
        from qgis.core import QgsRasterBlock

        item_size = int(QgsRasterBlock.typeSize(data_type))
    except Exception:  # noqa: BLE001
        item_size = 0
    code = _array_code(item_size, is_float, data_type not in unsigned)
    values: list = []
    raw = bytes(block.data()) if code else b""
    if code and len(raw) == item_size * nx * ny:
        packed = array(code)
        packed.frombytes(raw)
        values = [float(v) for v in packed]
    else:


        values = [float(block.value(row, col)) for row in range(ny) for col in range(nx)]






    values = [value for row in range(ny - 1, -1, -1)
              for value in values[row * nx:(row + 1) * nx]]

    nodata: list[float] = []
    try:
        if block.hasNoDataValue():
            nodata.append(float(block.noDataValue()))
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    try:
        if provider.sourceHasNoDataValue(band):
            nodata.append(float(provider.sourceNoDataValue(band)))
    except Exception:  # noqa: BLE001  # nosec B110
        pass
    crs = layer.crs()
    return {
        "values": values, "nx": nx, "ny": ny,
        "native_nx": native_x, "native_ny": native_y,
        "max_cells": max_cells,
        "cell_x": rect.width() / max(1, nx - 1), "cell_y": rect.height() / max(1, ny - 1),
        "native_cell_x": cell_x, "native_cell_y": cell_y,
        "extent": [rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()],
        "nodata": nodata,
        "layer_name": layer.name(), "layer_id": layer.id(), "band": band,
        "crs": crs.authid() or crs.description(), "geographic": bool(crs.isGeographic()),
        "units": _unit_name(crs),
    }


def _unit_name(crs) -> str:

    try:
        from qgis.core import QgsUnitTypes

        return str(QgsUnitTypes.toAbbreviatedString(crs.mapUnits()) or "")
    except Exception:  # noqa: BLE001
        return "degrees" if getattr(crs, "isGeographic", lambda: False)() else ""





def _fill_holes(values: list, nx: int, ny: int, nodata: list) -> tuple[int, int]:







    sentinels = {v for v in nodata if not math.isnan(v)}
    missing_idx = []
    for i, value in enumerate(values):
        if math.isnan(value) or value in sentinels or math.isinf(value):
            values[i] = None
            missing_idx.append(i)
    if not missing_idx:
        return 0, 0
    unknown = set(missing_idx)
    frontier = [i for i in range(len(values)) if values[i] is not None]
    filled = 0
    while unknown and frontier:
        heartbeat()
        wave: dict = {}
        for i in frontier:
            value = values[i]
            x, y = i % nx, i // nx
            for dx, dy in _NEIGHBOURS:
                px, py = x + dx, y + dy
                if 0 <= px < nx and 0 <= py < ny:
                    j = py * nx + px
                    if j in unknown:
                        seen = wave.get(j)
                        if seen is None:
                            wave[j] = [value, 1]
                        else:
                            seen[0] += value
                            seen[1] += 1
        if not wave:
            break
        for j, (total, count) in wave.items():
            values[j] = total / count
            unknown.discard(j)
        filled += len(wave)
        frontier = list(wave)
    return len(missing_idx), filled





def _write_obj(handle, values, nx, ny, dx, dy, z_factor, comments, places: int) -> tuple[int, int]:






    cancel = net.current_cancel_check()
    xy = f"%.{places}f"
    line = f"v {xy} {xy} %.4f\n"
    for comment in comments:
        handle.write(f"# {comment}\n")
    out: list = []
    for j in range(ny):
        y = j * dy
        base = j * nx
        out.extend(line % (i * dx, y, values[base + i] * z_factor) for i in range(nx))
        if j % _CHECK_EVERY_ROWS == 0:
            handle.write("".join(out))
            out = []
            heartbeat()
            if cancel is not None and cancel():
                raise InterruptedError("export_3d_model cancelled")
    handle.write("".join(out))

    handle.write("g terrain\n")
    out = []
    for j in range(ny - 1):
        row, above = j * nx + 1, (j + 1) * nx + 1
        for i in range(nx - 1):
            a, b = row + i, row + i + 1
            c, d = above + i + 1, above + i
            out.append(f"f {a} {b} {c}\nf {a} {c} {d}\n")
        if j % _CHECK_EVERY_ROWS == 0:
            handle.write("".join(out))
            out = []
            heartbeat()
            if cancel is not None and cancel():
                raise InterruptedError("export_3d_model cancelled")
    handle.write("".join(out))
    return nx * ny, 2 * (nx - 1) * (ny - 1)


def _fan(apex, ring):

    for k in range(len(ring) - 1):
        yield apex, ring[k], ring[k + 1]


def _solid_triangles(values, nx, ny, dx, dy, z_factor, z_base):





    def vertex(i, j):
        return (i * dx, j * dy, values[j * nx + i] * z_factor)

    for j in range(ny - 1):
        for i in range(nx - 1):
            a, b = vertex(i, j), vertex(i + 1, j)
            c, d = vertex(i + 1, j + 1), vertex(i, j + 1)
            yield a, b, c
            yield a, c, d

    x1, y1 = (nx - 1) * dx, (ny - 1) * dy
    c00 = (0.0, 0.0, z_base)
    c10 = (x1, 0.0, z_base)
    c11 = (x1, y1, z_base)
    c01 = (0.0, y1, z_base)
    yield c00, c11, c10
    yield c00, c01, c11
    yield from _fan(c00, [c10] + [vertex(i, 0) for i in range(nx - 1, -1, -1)])
    yield from _fan(c10, [c11] + [vertex(nx - 1, j) for j in range(ny - 1, -1, -1)])
    yield from _fan(c11, [c01] + [vertex(i, ny - 1) for i in range(nx)])
    yield from _fan(c01, [c00] + [vertex(0, j) for j in range(ny)])


def solid_triangle_count(nx: int, ny: int) -> int:

    return 2 * (nx - 1) * (ny - 1) + 2 + 2 * nx + 2 * ny


def _write_stl(handle, values, nx, ny, dx, dy, z_factor, z_base, title: str) -> int:

    cancel = net.current_cancel_check()
    total = solid_triangle_count(nx, ny)


    handle.write(title.encode("ascii", "replace")[:80].ljust(80, b"\0"))
    handle.write(struct.pack("<I", total))
    pack = _TRIANGLE.pack
    chunk: list = []
    written = 0
    for (ax, ay, az), (bx, by, bz), (cx, cy, cz) in _solid_triangles(
            values, nx, ny, dx, dy, z_factor, z_base):
        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nxn, nyn, nzn = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        length = math.sqrt(nxn * nxn + nyn * nyn + nzn * nzn)
        if length > 0:
            nxn, nyn, nzn = nxn / length, nyn / length, nzn / length
        else:
            nxn = nyn = nzn = 0.0
        chunk.append(pack(nxn, nyn, nzn, ax, ay, az, bx, by, bz, cx, cy, cz, 0))
        written += 1
        if len(chunk) >= 8192:
            handle.write(b"".join(chunk))
            chunk = []
            heartbeat()
            if cancel is not None and cancel():
                raise InterruptedError("export_3d_model cancelled")
    if chunk:
        handle.write(b"".join(chunk))
    return written





def _number(args: dict, key: str, default: float, low: float, high: float):
    value = args.get(key)
    if value is None or value == "":
        return default, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default, tool_error(f"{key} must be a number.", "INVALID_ARGS",
                                   f"Pass a number between {low} and {high}, or leave {key} out.")
    if math.isnan(number) or number < low or number > high:
        return default, tool_error(f"{key} must be between {low} and {high}.", "INVALID_ARGS",
                                   f"Leave {key} out for {default}.")
    return number, None


def _format_for(path: str, asked) -> tuple[str, dict | None]:





    extension = os.path.splitext(path)[1].lower()
    from_path = _EXTENSIONS.get(extension)
    wanted = str(asked or "").strip().lower().lstrip(".")
    if wanted and wanted not in FORMATS:
        return "", tool_error(f"format must be one of {', '.join(FORMATS)}, not {wanted!r}.", "INVALID_ARGS",
                              "Use obj for Blender or SketchUp, stl for a 3D print.")
    if from_path is None:
        return "", tool_error(
            f"path must end in .obj or .stl; {extension or 'no extension'} names no mesh format.",
            "INVALID_ARGS",
            "Write <name>.obj for Blender or SketchUp, <name>.stl for a slicer.")
    if wanted and wanted != from_path:
        return "", tool_error(
            f"format is {wanted!r} but path ends in {extension}.", "INVALID_ARGS",
            f"Either leave format out, or write the path as <name>.{wanted}.")
    return from_path, None


def _export_3d_model(args: dict) -> dict:

    from .layer_io_tools import _discard_staged_write, _publish_staged_write, _staging_path

    path = str(args.get("path") or "").strip()
    if not path:
        return tool_error("path is required.", "INVALID_ARGS",
                          "Name the file to write, for example ~/Desktop/terrain.stl.")
    fmt, error = _format_for(path, args.get("format"))
    if error:
        return error
    path = security.expand_path(path)
    problem = security.validate_path(path, write=True)
    if problem:
        return tool_error(problem, "PERMISSION_DENIED",
                          "Pick a path under the project folder, the home folder or the temp folder.")

    z_factor, error = _number(args, "z_factor", 1.0, 1e-9, 1e9)
    if error:
        return error

    grid = run_on_main_thread(_read_grid, args, timeout=180)
    if grid.get("error"):
        return grid["error"]
    values, nx, ny = grid["values"], grid["nx"], grid["ny"]
    if nx < MIN_GRID or ny < MIN_GRID:
        return tool_error(
            f"That extent samples to {nx} by {ny} cells, too few to build a surface.", "INVALID_ARGS",
            "Ask for a larger extent, or raise max_cells.")

    missing, filled = _fill_holes(values, nx, ny, grid["nodata"])
    if missing and filled < missing:
        return tool_error(
            f"Band {grid['band']} of {grid['layer_name']!r} has no elevation at all over that extent "
            f"({missing} of {nx * ny} cells are NoData).", "INVALID_ARGS",
            "Pick an extent the DEM actually covers, or a band that carries elevation.")

    z_min = min(values) * z_factor
    z_max = max(values) * z_factor
    span = z_max - z_min
    thickness, error = _number(args, "base_thickness", span * _BASE_FRACTION if span > 0 else 1.0,
                               1e-9, 1e12)
    if error:
        return error
    z_base = z_min - thickness
    dx, dy = grid["cell_x"], grid["cell_y"]

    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    staging = _staging_path(path)
    if not security.fits_path(staging, margin=8):
        return tool_error(
            f"The path is too long for this system to write beside: {path}", "INVALID_ARGS",
            "Write into a shorter folder, or shorten the file name.")

    title = f"TerraLab AI Agent for QGIS: {grid['layer_name']} terrain"
    try:
        if fmt == "obj":
            comments = [
                title,
                f"source layer: {grid['layer_name']} band {grid['band']}",
                f"CRS: {grid['crs']}",
                f"vertex (0, 0) is {grid['extent'][0]:.6f} {grid['extent'][1]:.6f} in that CRS",
                f"grid: {nx} x {ny} vertices, {dx:.6g} x {dy:.6g} apart",
                f"z_factor: {z_factor:g}",
            ]
            with open(staging, "w", encoding="utf-8", newline="\n") as handle:
                vertices, faces = _write_obj(handle, values, nx, ny, dx, dy, z_factor, comments,
                                             8 if grid["geographic"] else 4)
            triangles = faces
        else:
            with open(staging, "wb") as handle:
                triangles = _write_stl(handle, values, nx, ny, dx, dy, z_factor, z_base, title)
            vertices, faces = nx * ny + 4, triangles
    except InterruptedError:
        _discard_staged_write(staging)
        return tool_error("The export was stopped before the file was finished.", "CANCELLED",
                          "Run it again, with a smaller max_cells if it was taking too long.")
    except (OSError, MemoryError) as exc:
        _discard_staged_write(staging)
        return tool_error(f"Could not write {path}: {exc}", "EXECUTION_FAILED",
                          "Check the folder is writable and has room, or lower max_cells.")

    size = os.path.getsize(staging) if os.path.isfile(staging) else 0
    publish_error = _publish_staged_write(staging, path)
    if publish_error:
        _discard_staged_write(staging)
        return tool_error(
            f"The model was written but could not be put in place at {path}: {publish_error}",
            "EXECUTION_FAILED",
            "Close the file in Blender, SketchUp or the slicer, or write another name.")

    unit = grid["units"] or ("degrees" if grid["geographic"] else "map units")
    origin_x, origin_y = grid["extent"][0], grid["extent"][1]
    out = {
        "exported": path,
        "format": "OBJ surface" if fmt == "obj" else "STL (binary) solid",
        "source_layer": grid["layer_name"], "source_layer_id": grid["layer_id"], "band": grid["band"],
        "crs": grid["crs"],
        "grid_columns": nx, "grid_rows": ny, "grid_cells": nx * ny,
        "vertices": vertices, "triangles": triangles,
        "cell_size": [round(dx, 6), round(dy, 6)],
        "cell_size_unit": unit,
        "model_size": [round((nx - 1) * dx, 6), round((ny - 1) * dy, 6)],
        "origin": {"x": round(origin_x, 6), "y": round(origin_y, 6), "crs": grid["crs"]},
        "origin_note": (f"Vertex (0, 0, 0) of the file is {origin_x:.6f}, {origin_y:.6f} in "
                        f"{grid['crs']} at elevation 0: add those to the model's X and Y to put it "
                        f"back on the map. The mesh spans the DEM extent exactly, so its bounding "
                        f"box is that extent and its height is the elevation range times z_factor."),
        "z_factor": z_factor,
        "elevation_range": [round(z_min / z_factor, 4), round(z_max / z_factor, 4)],
        "z_range_in_model": [round(z_min, 4), round(z_max, 4)],
        "bytes": size,
    }
    if fmt == "stl":
        out["base_thickness"] = round(thickness, 6)
        out["base_z"] = round(z_base, 4)
        out["watertight"] = True
        out["solid_note"] = ("Closed solid: the height surface, four vertical walls and a flat base "
                             f"{thickness:.4g} below the lowest point. Every edge is shared by exactly "
                             "two triangles, which is what a slicer needs.")
    else:
        out["surface_note"] = ("An open height surface, no sides and no base. Import it into Blender or "
                               "SketchUp as a mesh; for a 3D print ask for the same DEM as .stl, which "
                               "closes it into a solid.")

    native = grid["native_nx"] * grid["native_ny"]
    if nx * ny < native:
        out["reduced"] = True
        out["native_grid"] = [grid["native_nx"], grid["native_ny"]]
        out["reduction_note"] = (
            f"The DEM is {grid['native_nx']} x {grid['native_ny']} cells over that extent "
            f"({native:,} in all), over the {grid['max_cells']:,} cell cap, so it was read at "
            f"{nx} x {ny} cells, about {dx:.6g} x {dy:.6g} {unit} per cell. Raise max_cells "
            f"(up to {MAX_CELLS_CEILING:,}) for more detail, or pass a smaller extent.")
    else:
        out["reduced"] = False

    out["nodata_cells"] = missing
    out["nodata_cells_filled"] = filled
    if missing:
        share = 100.0 * missing / float(nx * ny)
        out["nodata_note"] = (
            f"{missing} of the {nx * ny} sampled cells ({share:.1f}%) had no elevation. Each was "
            "filled with the mean of its known neighbours, spreading in from the valid data, so the "
            "surface has no spikes and the solid stays closed. Nothing was clipped away.")
        if share > 10.0:
            out["warning"] = (f"{share:.1f}% of this extent is NoData: that much of the model is "
                              "interpolated, not measured.")
    if grid["geographic"]:
        out["warning"] = (
            f"The DEM is in {grid['crs']}, so X and Y in the file are degrees while Z is metres: "
            "Blender and a slicer read them as the same unit and the model comes out as a flat sheet. "
            "Reproject the DEM to a metric CRS (run_processing gdal:warpreproject to a UTM zone or the "
            "national grid) and export again.")
    log_warning(f"export_3d_model: {os.path.basename(path)}, {nx}x{ny} cells, "
                f"{triangles} triangles, {size} bytes")
    return out


__all__ = ["DEFAULT_MAX_CELLS", "FORMATS", "MAX_CELLS_CEILING", "_export_3d_model",
           "solid_triangle_count"]

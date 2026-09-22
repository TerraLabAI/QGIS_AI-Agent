# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













from __future__ import annotations

import importlib
import json
import os
import sys
import time
import traceback
import types

_MAX_LAYERS = 200
_MAX_LAYOUTS = 20


def _cap(job: dict, key: str, shipped: int) -> int:

    value = job.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return shipped
    return min(value, shipped)


def _write(path: str, value: dict) -> None:
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=True)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _layout_quality(plugin_root: str):

    packages = {
        "_ai_agent_plugin": [plugin_root],
        "_ai_agent_plugin.src": [os.path.join(plugin_root, "src")],
        "_ai_agent_plugin.src.core": [os.path.join(plugin_root, "src", "core")],
    }
    for name, paths in packages.items():
        if name not in sys.modules:
            module = types.ModuleType(name)
            module.__path__ = paths
            sys.modules[name] = module
    return importlib.import_module("_ai_agent_plugin.src.core.layout_quality")


def _source_path(layer) -> str:

    try:
        source = str(layer.source() or "")
    except (AttributeError, RuntimeError):
        return ""
    for separator in ("|layername=", "|layerid=", "|subset="):
        if separator in source:
            source = source.split(separator, 1)[0]
    if source.startswith("ogr:") or "dbname=" in source:
        start = source.find("dbname=")
        if start >= 0:
            rest = source[start + len("dbname="):].strip()
            quote = rest[:1]
            if quote in ("'", '"'):
                end = rest.find(quote, 1)
                return rest[1:end] if end > 0 else rest[1:]
            return rest.split(" ", 1)[0]
    return source


def _layer_rows(project, limit: int = _MAX_LAYERS) -> tuple:
    rows = []
    broken = []
    for layer in list(project.mapLayers().values())[:limit]:
        row = {"name": "", "valid": False}
        try:
            row["name"] = str(layer.name() or "")
            row["valid"] = bool(layer.isValid())
            row["provider"] = str(layer.providerType() or "")
            source = _source_path(layer)
            row["source"] = source[-200:]
            if source and "://" not in source:
                row["source_found"] = os.path.exists(source)
            if not row["valid"]:
                row["why"] = str(layer.error().summary() or "")[:300] if layer.error() else ""
        except Exception as exc:  # noqa: BLE001
            row["why"] = f"could not be read: {exc}"
        if not row["valid"] or row.get("source_found") is False:
            broken.append(row["name"])
        rows.append(row)
    return rows, broken


def _layout_rows(project, quality, limit: int = _MAX_LAYOUTS) -> list:
    rows = []
    manager = project.layoutManager()
    try:
        layouts = list(manager.printLayouts())[:limit]
    except (AttributeError, RuntimeError):
        return rows
    for layout in layouts:
        row = {"name": ""}
        try:
            row["name"] = str(layout.name() or "")
            facts = quality.assess_layout(layout)
            row["status"] = facts.get("status")
            row["pages_mm"] = facts.get("pages_mm") or []
            row["items"] = [{"item": item.get("item"), "type": item.get("type"),
                             "box_mm": item.get("box_mm"), "inside_page": item.get("inside_page"),
                             "status": item.get("status")}
                            for item in (facts.get("items") or [])]
            row["items_outside_page"] = [item.get("item") for item in (facts.get("items") or [])
                                         if item.get("inside_page") is False]
        except Exception as exc:  # noqa: BLE001
            row["status"] = "unknown"
            row["why"] = str(exc)
        rows.append(row)
    return rows


def _check(job: dict) -> dict:
    from qgis.core import QgsApplication, QgsProject

    application = QgsApplication([], False)
    QgsApplication.initQgis()
    try:
        project = QgsProject.instance()
        started = time.monotonic()



        from qgis.core import QgsSettings

        QgsSettings().setValue("qgis/enableMacros", 0)
        opened = bool(project.read(job["project_path"]))
        elapsed = round(time.monotonic() - started, 2)
        if not opened:
            return {"ok": False, "error": "QGIS could not read the project file.", "read_s": elapsed}
        layers, broken = _layer_rows(project, _cap(job, "max_layers", _MAX_LAYERS))
        layouts = _layout_rows(project, _layout_quality(job["plugin_root"]),
                               _cap(job, "max_layouts", _MAX_LAYOUTS))
        return {"ok": True, "read_s": elapsed,
                "title": str(project.title() or ""),
                "crs": str(project.crs().authid() or ""),
                "layer_count": len(layers), "layers": layers, "broken_layers": broken,
                "layout_count": len(layouts), "layouts": layouts}
    finally:
        try:
            QgsApplication.exitQgis()
        except Exception:  # nosec B110
            pass
        del application


def main() -> int:
    job_path, status_path = sys.argv[1], sys.argv[2]
    try:
        with open(job_path, encoding="utf-8") as handle:
            job = json.load(handle)
    except (OSError, ValueError) as exc:
        _write(status_path, {"ok": False, "error": f"the job could not be read: {exc}"})
        return 1
    try:
        _write(status_path, _check(job))
    except BaseException as exc:  # noqa: BLE001
        _write(status_path, {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                             "traceback": traceback.format_exc()[-2000:]})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

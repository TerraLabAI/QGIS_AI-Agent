# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run Geometric Attributes Centerlines in a disposable QGIS process."""
from __future__ import annotations

import json
import os
import sys
import traceback


def _utm_authid(longitude: float, latitude: float) -> str:
    """Return the local WGS 84 UTM CRS for one longitude/latitude."""
    zone = max(1, min(60, int((float(longitude) + 180.0) // 6.0) + 1))
    base = 32600 if float(latitude) >= 0 else 32700
    return f"EPSG:{base + zone}"


def _write(path: str, value: dict) -> None:
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _source_uri(job: dict) -> str:
    table = str(job.get("source_table") or "")
    return job["source_path"] + (f"|layername={table}" if table else "")


def _run(job: dict) -> dict:
    from qgis.core import (
        QgsApplication,
        QgsProcessingProvider,
        QgsVectorLayer,
    )

    application = QgsApplication([], False)
    application.initQgis()
    qgis_plugins = os.path.join(QgsApplication.pkgDataPath(), "python", "plugins")
    if qgis_plugins not in sys.path:
        sys.path.append(qgis_plugins)
    from processing.core.Processing import Processing

    Processing.initialize()
    import processing

    provider_parent = os.path.dirname(job["provider_dir"])
    if provider_parent not in sys.path:
        sys.path.insert(0, provider_parent)
    from geometric_attributes.Centerlines import Centerlines

    class CenterlineProvider(QgsProcessingProvider):
        def id(self):
            return "Algorithms"

        def name(self):
            return "Geometric Attributes isolated"

        def loadAlgorithms(self):
            self.addAlgorithm(Centerlines())

    provider = CenterlineProvider()
    if not QgsApplication.processingRegistry().addProvider(provider):
        raise RuntimeError("the isolated Geometric Attributes provider could not be loaded")

    original_run = processing.run


    voronoi_id = "native:voronoipolygons"
    if QgsApplication.processingRegistry().algorithmById(voronoi_id) is None:
        voronoi_id = "qgis:voronoipolygons"

    def compatible_run(algorithm_id, parameters, *args, **kwargs):
        if str(algorithm_id).casefold() in {"saga:thiessenpolygons", "sagang:thiessenpolygons"}:
            translated = {
                "INPUT": parameters["POINTS"],
                "BUFFER": parameters.get("FRAME", 10.0),
                "OUTPUT": parameters["POLYGONS"],
            }
            result = original_run(voronoi_id, translated, *args, **kwargs)
            return {"POLYGONS": result["OUTPUT"]}
        return original_run(algorithm_id, parameters, *args, **kwargs)

    processing.run = compatible_run
    layer = QgsVectorLayer(_source_uri(job), "Isolated centerline input", "ogr")
    if not layer.isValid():
        raise RuntimeError("the copied GeoPackage layer could not be opened")
    analysis_layer = layer
    if layer.crs().isGeographic():
        center = layer.extent().center()
        target_crs = _utm_authid(center.x(), center.y())
        metric_path = os.path.join(os.path.dirname(job["output_path"]), "metric-input.gpkg")
        reprojected = original_run(
            "native:reprojectlayer",
            {"INPUT": layer, "TARGET_CRS": target_crs, "OUTPUT": metric_path},
        )
        analysis_layer = QgsVectorLayer(str(reprojected["OUTPUT"]), "Metric centerline input", "ogr")
        if not analysis_layer.isValid():
            raise RuntimeError("the metric working copy could not be opened")
    parameters = {
        "Polygons": analysis_layer,
        "Method": int(job.get("method", 0)),
        "Trim Iterations": float(job.get("trim_iterations", 0)),
        "Simplify": float(job.get("simplify_distance", 0)),
        "Line Spacing": float(job.get("densify_distance", 0)),
        "Centerlines": job["output_path"],
    }
    result = original_run("Algorithms:Centerlines", parameters)
    output = str(result.get("Centerlines") or job["output_path"])
    verified = QgsVectorLayer(output, "Verified centerlines", "ogr")
    if not verified.isValid():
        raise RuntimeError("the provider returned an unreadable centerline layer")
    empty_count = 0
    invalid_count = 0
    for feature in verified.getFeatures():
        geometry = feature.geometry()
        if geometry.isNull() or geometry.isEmpty():
            empty_count += 1
        elif not geometry.isGeosValid():
            invalid_count += 1
    if empty_count or invalid_count:
        raise RuntimeError(
            f"output verification found {empty_count} empty and {invalid_count} invalid line geometries"
        )
    return {
        "ok": True,
        "feature_count": int(verified.featureCount()),
        "output_crs": verified.crs().authid(),
        "empty_count": empty_count,
        "invalid_count": invalid_count,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    job_path, status_path = argv[1:]
    try:
        with open(job_path, encoding="utf-8") as handle:
            job = json.load(handle)
        result = _run(job)
    except BaseException as exc:  # noqa: BLE001 - the parent needs every child failure
        result = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc()[-4_000:],
        }
    _write(status_path, result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    code = main(sys.argv)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)

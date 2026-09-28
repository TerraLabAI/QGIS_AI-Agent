# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later















from __future__ import annotations

import importlib
import json
import os
import sys
import time
import traceback


def _write(path: str, value: dict) -> None:
    temporary = path + ".partial"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=True, default=str)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _plain(value):

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    return str(value)


def _load_providers(job: dict) -> list[str]:

    from qgis.core import QgsApplication

    failures = []
    try:
        from qgis.analysis import QgsNativeAlgorithms

        QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())
    except Exception as exc:  # noqa: BLE001
        failures.append(f"native provider: {exc}")
    try:
        from processing.core.Processing import Processing

        Processing.initialize()
    except Exception as exc:  # noqa: BLE001
        failures.append(f"processing plugin: {exc}")
    keep = []
    registry = QgsApplication.processingRegistry()
    if job.get("provider_id") and registry.providerById(job["provider_id"]) is not None:
        return failures
    for plugin in job.get("plugins") or []:
        package = plugin.get("package")
        try:
            instance = importlib.import_module(package).classFactory(None)
            instance.initProcessing()
            keep.append(instance)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{package}: {exc}")
    _load_providers.kept = keep
    return failures


class _Progress:


    def __init__(self, path: str):
        self.path, self.last = path, 0.0

    def __call__(self, value) -> None:
        now = time.monotonic()
        if now - self.last < 0.5:
            return
        self.last = now
        try:
            with open(self.path + ".partial", "w", encoding="utf-8") as handle:
                handle.write(str(int(value)))
            os.replace(self.path + ".partial", self.path)
        except (OSError, ValueError):
            pass


def _run(job: dict, progress_path: str, status_path: str) -> None:

    application = _start()
    try:
        _write(status_path, _algorithm(job, progress_path))
    finally:
        try:
            from qgis.core import QgsApplication

            QgsApplication.exitQgis()
        except Exception:  # nosec B110
            pass
        del application


def _start():
    from qgis.core import QgsApplication

    application = QgsApplication([], False)
    QgsApplication.initQgis()
    return application


def _algorithm(job: dict, progress_path: str) -> dict:
    started = time.monotonic()
    from qgis.core import (
        QgsApplication,
        QgsCoordinateReferenceSystem,
        QgsProcessingContext,
        QgsProcessingFeedback,
        QgsProject,
        QgsSettings,
    )

    settings = QgsSettings()


    for key, value in (job.get("settings") or {}).items():
        settings.setValue(key, value)
    project = QgsProject.instance()
    crs = (job.get("project") or {}).get("crs")
    if crs:
        project.setCrs(QgsCoordinateReferenceSystem(crs))
    ellipsoid = (job.get("project") or {}).get("ellipsoid")
    if ellipsoid:
        project.setEllipsoid(ellipsoid)
    failures = _load_providers(job)
    ready = time.monotonic()
    algorithm_id = job["algorithm_id"]
    alg = QgsApplication.processingRegistry().createAlgorithmById(algorithm_id)
    if alg is None:
        return {"ok": False, "unavailable": True,
                "error": f"{algorithm_id} is not available in the separate QGIS.",
                "provider_failures": failures, "init_s": round(ready - started, 2)}

    class Feedback(QgsProcessingFeedback):
        def __init__(self):
            super().__init__()
            self.errors, self.warnings, self.console = [], [], []

        def reportError(self, error, fatalError=False):  # noqa: N802
            self.errors.append(str(error))
            super().reportError(error, fatalError)

        def pushWarning(self, warning):  # noqa: N802
            self.warnings.append(str(warning))
            super().pushWarning(warning)

        def pushConsoleInfo(self, info):  # noqa: N802
            self.console.append(str(info))
            del self.console[:-40]
            super().pushConsoleInfo(info)

    feedback = Feedback()
    feedback.progressChanged.connect(_Progress(progress_path))
    context = QgsProcessingContext()
    context.setProject(project)
    if job.get("invalid_geometry_check") is not None:


        from qgis.core import Qgis, QgsFeatureRequest

        holder = getattr(Qgis, "InvalidGeometryCheck", None) or getattr(QgsFeatureRequest, "InvalidGeometryCheck", None)
        context.setInvalidGeometryCheck(holder(int(job["invalid_geometry_check"])))
    parameters = dict(job.get("parameters") or {})
    valid, detail = alg.checkParameterValues(parameters, context)
    if not valid:
        return {"ok": False, "error": str(detail), "init_s": round(ready - started, 2)}
    import processing

    try:
        results = processing.run(alg, parameters, feedback=feedback, context=context)
        ok, error = True, ""
    except Exception as exc:  # noqa: BLE001
        results, ok, error = {}, False, str(exc)
    return {"ok": ok, "error": error, "results": _plain(results or {}),
            "errors": feedback.errors[-10:], "warnings": feedback.warnings[-10:],
            "console": feedback.console[-10:], "provider_failures": failures,
            "init_s": round(ready - started, 2), "run_s": round(time.monotonic() - ready, 2)}


def main() -> int:
    job_path, status_path, progress_path = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        with open(job_path, encoding="utf-8") as handle:
            job = json.load(handle)
    except (OSError, ValueError) as exc:
        _write(status_path, {"ok": False, "error": f"the job could not be read: {exc}"})
        return 1
    try:
        _run(job, progress_path, status_path)
    except BaseException as exc:  # noqa: BLE001
        _write(status_path, {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                             "traceback": traceback.format_exc()[-2000:]})
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

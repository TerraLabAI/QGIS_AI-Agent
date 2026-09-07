# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

"""The choropleth half of the statistics flow: one layer, classified, sourced.

`find_statistic` on the server fetches the values, fetches the boundaries and joins them on the
official code, then hands back one GeoJSON address. This loads it and classifies it in a single
call, so the map a user asked for arrives as a map and not as a layer they must then style.

The join is deliberately not here. It happens once, on the server, where the match rate is counted
and a run below the threshold is refused with the codes that did not match, which is the whole
point of plan 02: a wrong map draws exactly like a right one, so the count has to happen somewhere
a person cannot skip. What this tool must not do is quietly accept a table it cannot join.

The source, the year and the licence arrive with the layer and are written into its metadata and
its attribution, because a choropleth with no source is not evidence of anything.
"""
import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from qgis.core import (  # noqa: E402
    QgsLayerMetadata,
    QgsProject,
    QgsStyle,
    QgsSymbol,
    QgsVectorLayer,
)

from ..core import net, tuning  # noqa: E402
from ..core.policy import create_managed_temp_dir  # noqa: E402
from ..core.tool_registry import Tool, ToolRegistry  # noqa: E402
from .data_tools import _avoid_reserved_name, _run_on_main_thread  # noqa: E402




_FETCH_TIMEOUT_S = 60
_TOTAL_TIMEOUT_FACTOR = 3


_MAX_BYTES = 40 * 1024 * 1024
_MODES = {"quantile": "Quantile", "equal_interval": "EqualInterval", "jenks": "Jenks", "pretty": "Pretty"}
_DEFAULT_RAMPS = ("Viridis", "Blues", "Spectral")


def _slug(text: str) -> str:
    """The stem of the file this statistic is written to."""





    cleaned = re.sub(r"[^a-z0-9]+", "_", str(text or "statistic").lower()).strip("_")[:40] or "statistic"
    return _avoid_reserved_name(cleaned)


def _classify(layer, field: str, classes: int, mode_name: str, ramp_name: str):
    """A graduated renderer on `field`, through the classification registry with the older createRenderer as the fallback, the way style_tools."""

    from qgis.core import QgsApplication, QgsGraduatedSymbolRenderer

    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    ramp = None
    for name in ([ramp_name] if ramp_name else []) + list(_DEFAULT_RAMPS):
        ramp = QgsStyle.defaultStyle().colorRamp(name)
        if ramp is not None:
            break
    try:
        renderer = QgsGraduatedSymbolRenderer(field, [])
        renderer.setSourceSymbol(symbol)
        renderer.setClassificationMethod(QgsApplication.classificationMethodRegistry().method(mode_name))
        if ramp is not None:
            renderer.setSourceColorRamp(ramp.clone())
        renderer.updateClasses(layer, classes)
    except Exception:
        renderer = QgsGraduatedSymbolRenderer.createRenderer(
            layer, field, classes, getattr(QgsGraduatedSymbolRenderer, mode_name),
            symbol, ramp.clone() if ramp is not None else None)
    layer.setRenderer(renderer)
    return [{"lower": r.lowerValue(), "upper": r.upperValue(), "label": r.label()} for r in renderer.ranges()]


def _numeric_domain_error(layer, field: str) -> dict | None:
    """Refuse a graduated map the field cannot carry: the wrong type, or no value."""
    index = layer.fields().indexOf(field)
    try:
        if not layer.fields().at(index).isNumeric():
            return {"_error": f"{field!r} is not a numeric field, so it cannot be graduated.",
                    "code": "INVALID_ARGS",
                    "fields": [f.name() for f in layer.fields() if f.isNumeric()]}
    except Exception:  # nosec B110 - a provider that cannot describe the field is judged on values
        pass
    values = layer.uniqueValues(index, 2)
    real = [v for v in values if v is not None and str(v) != "NULL"]
    if not real:
        return {"_error": f"Every value of {field!r} is empty, so no class could be built.",
                "code": "EXECUTION_FAILED",
                "suggestion": "Check the value_field name against the fields the layer actually carries."}
    return None


def _build(path: str, name: str, field: str, classes: int, mode_name: str, ramp_name: str,
           credit: str, abstract: str, licence: str = "", source: str = "", source_url: str = "") -> dict:
    layer = QgsVectorLayer(path, name, "ogr")
    if not layer.isValid():
        return {"_error": "The statistics layer could not be read as a vector layer.",
                "code": "EXECUTION_FAILED"}
    if layer.fields().indexOf(field) < 0:
        return {"_error": f"The layer carries no field named {field!r}.", "code": "INVALID_ARGS",
                "fields": [f.name() for f in layer.fields()]}
    if layer.featureCount() == 0:
        return {"_error": "The statistics layer is empty, so nothing was added.",
                "code": "EXECUTION_FAILED"}



    domain_error = _numeric_domain_error(layer, field)
    if domain_error:
        return domain_error
    ranges = _classify(layer, field, classes, mode_name, ramp_name)
    if not ranges:
        return {"_error": f"No class could be built from {field!r}, so no map was drawn.",
                "code": "EXECUTION_FAILED",
                "suggestion": "Check the field holds comparable numbers, or ask for fewer classes."}
    metadata = QgsLayerMetadata()
    metadata.setTitle(name)
    metadata.setAbstract(abstract)


    if licence:
        metadata.setLicenses([licence])
    if source:
        metadata.setIdentifier(source)
    if source_url:
        try:
            from qgis.core import QgsAbstractMetadataBase

            link = QgsAbstractMetadataBase.Link()
            link.name = "source"
            link.type = "WWW:LINK"
            link.url = source_url
            metadata.setLinks([link])
        except Exception:  # noqa: BLE001  # nosec B110 - an older QGIS keeps the abstract only
            pass
    layer.setMetadata(metadata)
    layer.setAttribution(credit)
    QgsProject.instance().addMapLayer(layer)
    return {"layer": layer.name(), "layer_id": layer.id(), "features": layer.featureCount(),
            "field": field, "classes": ranges, "crs": layer.crs().authid()}


def _is_backend_url(url: str) -> bool:
    """True when this address is served by the backend this plugin is paired with."""
    from urllib.parse import urlsplit

    from ..core.settings import Settings

    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
        server = (urlsplit(Settings().server_url).hostname or "").lower().rstrip(".")
    except Exception:  # noqa: BLE001 - an address we cannot read is not ours
        return False
    return bool(host) and host == server


def _map_statistic(args: dict) -> dict:
    url = str(args.get("url") or "").strip()
    field = str(args.get("value_field") or "").strip()
    if not url or not field:
        return {"_error": "map_statistic needs the url find_statistic returned and its value_field.",
                "code": "INVALID_ARGS"}
    mode_name = _MODES.get(str(args.get("classification") or "quantile").lower())
    if mode_name is None:
        return {"_error": f"{args.get('classification')!r} is not a classification.", "code": "INVALID_ARGS",
                "classification": sorted(_MODES)}
    try:
        classes = max(2, min(int(args.get("classes") or 5), 12))
    except (TypeError, ValueError):
        return {"_error": "classes must be a whole number between 2 and 12.", "code": "INVALID_ARGS"}
    name = str(args.get("name") or "").strip() or field.replace("_", " ").title()
    source = str(args.get("source") or "").strip()
    year = str(args.get("year") or "").strip()
    licence = str(args.get("licence") or "").strip()

    budget = tuning.limit("net", "download_timeout_s", _FETCH_TIMEOUT_S)
    response = net.fetch(url, timeout=budget, max_bytes=_MAX_BYTES,
                         total_timeout=budget * _TOTAL_TIMEOUT_FACTOR)
    body = response.body
    try:
        parsed = json.loads(body.decode("utf-8"))
        count = len(parsed.get("features") or []) if isinstance(parsed, dict) else 0
    except (UnicodeDecodeError, ValueError):
        return {"_error": "That address did not return GeoJSON.", "code": "EXECUTION_FAILED",
                "hint": "Pass the url find_statistic returned, unchanged."}
    if not count:
        return {"_error": "That layer carries no features, so no map was drawn.",
                "code": "EXECUTION_FAILED"}

    path = Path(create_managed_temp_dir("statistics")) / f"{_slug(name)}.geojson"
    path.write_bytes(body)
    credit = ", ".join(part for part in (source, year, licence) if part)



    joined_here = _is_backend_url(url)
    provenance = ("Values joined to the boundaries on the official code by the TerraLab statistics route."
                  if joined_here else
                  "Loaded from an address given by the caller; the join was not performed or checked here.")
    abstract = (f"{name}. Source: {source or 'not stated'}. Year: {year or 'not stated'}. "
                f"Licence: {licence or 'not stated'}. {provenance}")
    built = _run_on_main_thread(_build, str(path), name, field, classes, mode_name,
                                str(args.get("color_ramp") or ""), credit, abstract,
                                licence, source, url if joined_here else "")
    if isinstance(built, dict) and built.get("_error"):
        return built
    built["credit"] = credit
    built["join_verified"] = joined_here
    built["note"] = ("Say the source, the year and the licence in the answer: they are on the layer "
                     "but nobody reads layer properties.")
    return built


def register_statistics_tools(registry: ToolRegistry):


    registry.register(Tool(
        name="map_statistic",
        input_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "value_field": {"type": "string"},
                "name": {"type": "string"},
                "classification": {"type": "string", "enum": sorted(_MODES)},
                "classes": {"type": "integer", "minimum": 2, "maximum": 12},
                "color_ramp": {"type": "string"},
                "source": {"type": "string"},
                "year": {"type": "string"},
                "licence": {"type": "string"},
            },
            "required": ["url", "value_field"],
        },
        handler=_map_statistic,
        danger="write",
        background=True,
    ))

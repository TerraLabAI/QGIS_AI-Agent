# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import codecs
import re

from qgis.core import (
    QgsAbstractDatabaseProviderConnection,
    QgsCoordinateReferenceSystem,
    QgsDataSourceUri,
    QgsFeatureRequest,
    QgsProviderRegistry,
    QgsVectorLayer,
    QgsVectorLayerExporterTask,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import limits
from ..core.feature_requests import feature_request
from ..core.quiet_credentials import no_login_prompt
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._layers import layer_not_found, resolve_layer
from .layer_lookup import _field_not_found_error


_TEXT_SAMPLE_FEATURES = 500
_TEXT_SAMPLE_QUOTED = 3


_UTF8_READ_AS_LATIN = re.compile("[\u00c2\u00c3][\u0080-\u00bf]")
_LATIN_READ_AS_UTF8 = "\ufffd"
_POSTGRES_NAME_MAX = 63


def register_postgis_import_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="import_to_postgis",
        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Import {layer_name} into PostGIS[ table {table}]"),
        input_schema={
            "type": "object",
            "properties": {
                "layer_name": {"type": "string"},
                "connection": {"type": "string"},
                "schema": {"type": "string"},
                "table": {"type": "string"},
                "primary_key": {"type": "string"},
                "geometry_column": {"type": "string"},
                "target_crs": {"type": "string"},
                "source_encoding": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "selected_only": {"type": "boolean"},
                "spatial_index": {"type": "boolean"},
                "lowercase_names": {"type": "boolean"},
            },
            "required": ["layer_name", "connection"],
        },
        handler=_import_to_postgis,
    ))


class _PostgisImportTask(QgsVectorLayerExporterTask):


    def __init__(self, layer, uri: str, crs, options: dict, index: dict | None):
        super().__init__(layer, uri, "postgres", crs, options, False)
        self._index = index
        self.index_error = ""
        self._index_connection = None
        if index:



            try:
                metadata = QgsProviderRegistry.instance().providerMetadata("postgres")
                self._index_connection = metadata.createConnection(index["uri"], {})
            except Exception as exc:  # noqa: BLE001
                self.index_error = str(exc)[:300]

    def run(self):
        ok = super().run()
        if ok and self._index_connection is not None and not self.isCanceled():
            try:
                options = QgsAbstractDatabaseProviderConnection.SpatialIndexOptions()
                options.geometryColumnName = self._index["column"]
                self._index_connection.createSpatialIndex(self._index["schema"], self._index["table"], options)
            except Exception as exc:  # noqa: BLE001
                self.index_error = str(exc)[:300]
        return ok


def _connection(name: str):
    metadata = QgsProviderRegistry.instance().providerMetadata("postgres")
    if metadata is None:
        return None, tool_error("The PostgreSQL provider is not available in this QGIS installation.",
                                "EXECUTION_FAILED", "")
    try:
        connections = metadata.connections(False)
    except Exception as exc:  # noqa: BLE001
        return None, tool_error(f"PostgreSQL saved connections are unavailable: {exc}", "EXECUTION_FAILED", "")
    connection = connections.get(name)
    if connection is None:
        return None, tool_error(f"No saved PostgreSQL connection named {name!r}.", "INVALID_ARGS",
                                hint="pg_connection_unknown", connection=name,
                                saved_connections=sorted(connections)[:20])
    if not isinstance(connection, QgsAbstractDatabaseProviderConnection):
        return None, tool_error(f"Connection {name!r} is not a database connection.", "INVALID_ARGS", "")
    return connection, None


def _table_name(value: str, lowercase: bool) -> str:
    name = re.sub(r"\s+", "_", value.strip()).replace(".", "_")
    if lowercase:
        name = name.lower()
    return name[:_POSTGRES_NAME_MAX]


def _reopened(layer, encoding: str):

    copy = QgsVectorLayer(layer.source(), layer.name(), layer.providerType())
    if not copy.isValid():
        return None
    if encoding:
        copy.setProviderEncoding(encoding)
    if layer.subsetString() and copy.subsetString() != layer.subsetString():
        copy.setSubsetString(layer.subsetString())
    return copy


def _source_copy(layer, selected_only: bool, encoding: str):

    ceiling = limits.current("MAX_FEATURES_MATERIALISED")
    reopen_error = tool_error(f"The source of {layer.name()!r} could not be opened again for the import.",
                              "EXECUTION_FAILED", hint="pg_source_reopen", layer=layer.name())
    if selected_only:
        ids = list(layer.selectedFeatureIds())
        if not ids:
            return None, tool_error(f"{layer.name()!r} has no selected features.", "INVALID_ARGS",
                                    hint="pg_no_selection", layer=layer.name())
        if len(ids) > ceiling:
            return None, tool_error(f"{len(ids):,} selected features is past the {ceiling:,} this call copies.",
                                    "INVALID_ARGS", hint="pg_selection_too_large", selected=len(ids), ceiling=ceiling)
        source = layer
        if encoding and layer.providerType() != "memory":

            source = _reopened(layer, encoding)
            if source is None:
                return None, reopen_error
        return source.materialize(QgsFeatureRequest().setFilterFids(ids)), None
    if layer.providerType() == "memory":
        count = layer.featureCount()
        if count > ceiling:
            return None, tool_error(f"{layer.name()!r} is a scratch layer of {count:,} features, past the "
                                    f"{ceiling:,} this call copies.", "INVALID_ARGS",
                                    hint="pg_scratch_too_large", layer=layer.name(), features=count, ceiling=ceiling)
        return layer.materialize(QgsFeatureRequest()), None
    copy = _reopened(layer, encoding)
    if copy is None:
        return None, reopen_error
    return copy, None


def _suspect_text(layer) -> dict:

    text_fields = [f.name() for f in layer.fields()
                   if any(w in str(f.typeName() or "").lower() for w in ("string", "text", "char"))]
    if not text_fields:
        return {}



    request = feature_request(attributes=text_fields, fields=layer.fields(), geometry=False,
                              limit=_TEXT_SAMPLE_FEATURES)
    utf8_as_latin, latin_as_utf8 = [], []
    for feature in layer.getFeatures(request):
        for name in text_fields:
            value = feature[name]
            if not isinstance(value, str):
                continue
            if _UTF8_READ_AS_LATIN.search(value):
                utf8_as_latin.append(value[:60])
            elif _LATIN_READ_AS_UTF8 in value:
                latin_as_utf8.append(value[:60])
        if len(utf8_as_latin) + len(latin_as_utf8) >= _TEXT_SAMPLE_QUOTED * 2:
            break
    if utf8_as_latin:
        return {"samples": utf8_as_latin[:_TEXT_SAMPLE_QUOTED], "source_encoding": "UTF-8",
                "reading": "UTF-8 text read as a single-byte code page"}
    if latin_as_utf8:
        return {"samples": latin_as_utf8[:_TEXT_SAMPLE_QUOTED], "source_encoding": "windows-1252",
                "reading": "single-byte text (a Windows code page) read as UTF-8"}
    return {}


def _key_problem(layer, field_name: str):
    ceiling = limits.current("MAX_FEATURES_MATERIALISED")
    count = layer.featureCount()
    if not isinstance(count, int) or count < 0 or count > ceiling:
        return None
    index = layer.fields().indexOf(field_name)
    values = layer.uniqueValues(index)
    nulls = [v for v in values if v is None or (hasattr(v, "isNull") and v.isNull())]
    if nulls:
        return tool_error(f"{field_name!r} has empty values, and a primary key cannot. Nothing was written.",
                          "INVALID_ARGS", hint="pg_key_has_nulls", field=field_name)
    if len(values) < count:
        return tool_error(f"{field_name!r} repeats: {count - len(values):,} of {count:,} rows share a value "
                          "with another row, so the insert would fail partway. Nothing was written.",
                          "INVALID_ARGS", hint="pg_key_repeats", field=field_name,
                          repeated=count - len(values), rows=count)
    return None


def _import_to_postgis(args: dict) -> dict:
    layer = resolve_layer(args.get("layer_name"))
    if layer is None:
        return layer_not_found(args.get("layer_name"))
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{layer.name()!r} is not a vector layer.", "INVALID_ARGS",
                          hint="pg_raster_layer", layer=layer.name())
    if not layer.isValid():
        return tool_error(f"{layer.name()!r} is not readable (its source is missing or broken).", "INVALID_ARGS",
                          hint="pg_layer_unreadable", layer=layer.name())
    if layer.isModified():
        return tool_error(f"{layer.name()!r} has unsaved edits, and the import reads what is saved.",
                          "INVALID_ARGS", hint="pg_unsaved_edits", layer=layer.name())
    connection_name = str(args.get("connection") or "").strip()
    connection, error = _connection(connection_name)
    if error:
        return error

    lowercase = args.get("lowercase_names", True) is not False
    schema = str(args.get("schema") or "public").strip()
    table = _table_name(str(args.get("table") or layer.name()), lowercase)
    if not table:
        return tool_error("The table name is empty.", "INVALID_ARGS", "table.")
    overwrite = bool(args.get("overwrite"))
    has_geometry = layer.isSpatial()
    geometry_column = ""
    if has_geometry:
        geometry_column = str(args.get("geometry_column") or "geom").strip()
        if lowercase:
            geometry_column = geometry_column.lower()

    encoding = str(args.get("source_encoding") or "").strip()
    if encoding:
        try:
            codecs.lookup(encoding)
        except LookupError:
            return tool_error(f"Unknown encoding {encoding!r}.", "INVALID_ARGS",
                              hint="pg_encoding_unknown", encoding=encoding)

    crs = layer.crs()
    target = str(args.get("target_crs") or "").strip()
    if target:
        crs = QgsCoordinateReferenceSystem(target)
        if not crs.isValid():
            return tool_error(f"target_crs {target!r} is not a CRS QGIS knows.", "INVALID_ARGS",
                              hint="pg_target_crs_unknown", target_crs=target)
    if has_geometry and (not crs.isValid() or not layer.crs().isValid()):
        return tool_error(f"{layer.name()!r} has no known CRS, so its SRID in PostGIS would be 0.",
                          "INVALID_ARGS", "The import needs the layer's CRS declared.")

    primary_key = str(args.get("primary_key") or "").strip()
    if primary_key:
        if layer.fields().indexOf(primary_key) < 0:
            return _field_not_found_error(layer, primary_key)



    try:
        with no_login_prompt():
            schemas = connection.schemas()
            if schema not in schemas:
                return tool_error(f"Schema {schema!r} does not exist in {connection_name!r}.", "INVALID_ARGS",
                                  f"Schemas: {sorted(schemas)[:30]}.")
            exists = connection.tableExists(schema, table)
            postgis = True
            if has_geometry:
                postgis = bool(connection.executeSql(
                    "SELECT 1 FROM pg_extension WHERE extname = 'postgis'"))
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"Could not reach {connection_name!r}: {str(exc)[:300]}", "EXECUTION_FAILED",
                          hint="pg_unreachable", connection=connection_name)
    if exists and not overwrite:
        return tool_error(f"Table {schema}.{table} already exists. Nothing was written.", "INVALID_ARGS",
                          hint="pg_table_exists", schema=schema, table=table)
    if not postgis:
        return tool_error(f"The database behind {connection_name!r} has no PostGIS extension, so a layer "
                          "with geometry cannot be stored there. Nothing was written.", "INVALID_ARGS",
                          hint="pg_no_postgis", connection=connection_name)

    copy, error = _source_copy(layer, bool(args.get("selected_only")), encoding)
    if error:
        return error
    if primary_key:

        bad = _key_problem(copy, primary_key)
        if bad:
            return bad
    suspect = _suspect_text(copy)
    if suspect and not encoding:
        return tool_error(
            f"Some text in {layer.name()!r} reads as {suspect['reading']} "
            f"({'; '.join(repr(s) for s in suspect['samples'])}). Written now, the database would keep it "
            "broken. Nothing was written.",
            "INVALID_ARGS",
            hint="pg_text_misread", source_encoding=suspect["source_encoding"], reading=suspect["reading"])

    uri = QgsDataSourceUri(connection.uri())
    uri.setSchema(schema)
    uri.setTable(table)
    uri.setKeyColumn(primary_key)
    uri.setGeometryColumn(geometry_column)
    options = {"lowercaseFieldNames": lowercase}
    if overwrite:
        options["overwrite"] = True
    index = None
    if has_geometry and args.get("spatial_index", True) is not False:
        index = {"uri": connection.uri(), "schema": schema, "table": table, "column": geometry_column}
    feature_count = copy.featureCount()
    task = _PostgisImportTask(copy, uri.uri(False), crs, options, index)

    from .processing_run import register_task

    done = {
        "schema": schema, "table": table, "connection": connection_name,
        "feature_count": feature_count,
        "srid": crs.postgisSrid() if has_geometry else None,
        "crs": crs.authid() if has_geometry else None,
        "reprojected": bool(has_geometry and target and crs != layer.crs()),
        "primary_key": primary_key or "new serial column id",
        "geometry_column": geometry_column or None,
        "replaced_existing": bool(exists),
        "source_encoding": encoding or "as read by QGIS",
        "load": {"tool": "add_layer_from_connection",
                 "args": {"provider": "postgres", "connection": connection_name, "schema": schema,
                          "table": table}},
    }
    if suspect:
        done["encoding_warning"] = f"Text still reads as {suspect['reading']}: {suspect['samples'][0]!r}"

    def wire(_task_id: str, entry: dict) -> None:
        entry["source_copy"] = copy

        def complete() -> None:
            entry.pop("source_copy", None)
            if entry.get("status") != "running":
                return
            if index:
                done["spatial_index"] = ("not built: " + task.index_error) if task.index_error else "built"
            entry["status"] = "complete"
            entry["progress"] = 100
            entry["outputs"] = done

        def failed(_code, message) -> None:
            entry.pop("source_copy", None)
            if entry.get("status") != "running":
                return
            entry["status"] = "error"
            entry["error"] = f"The import into {schema}.{table} failed: {str(message)[:400]}"
            if exists:
                entry["error"] += " The table that was there before is already replaced."

        def stopped() -> None:
            entry.pop("source_copy", None)
            entry.setdefault("note", f"The import was stopped. {schema}.{table} may hold part of the rows: "
                                     "check it with list_connection_tables before importing again.")

        task.exportComplete.connect(complete)
        task.errorOccurred.connect(failed)
        try:
            task.taskTerminated.connect(stopped)
        except (AttributeError, TypeError):
            pass

    task_id, _entry = register_task(task, f"import_to_postgis {schema}.{table}", connect=wire)
    return {"task_id": task_id, "status": "running", "importing": f"{schema}.{table}",
            "feature_count": feature_count,
            "note": "Writing in the background.", "note_hint": "pg_import_running",
            "poll": {"tool": "get_task_status", "args": {"task_id": task_id},
                     "interval_s": 0.5, "label": f"Importing into {schema}.{table}"}}

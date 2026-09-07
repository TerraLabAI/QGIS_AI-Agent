# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






from __future__ import annotations

import os
import re

from qgis.core import (
    QgsAbstractDatabaseProviderConnection,
    QgsApplication,
    QgsBookmark,
    QgsCoordinateReferenceSystem,
    QgsDataSourceUri,
    QgsExpressionContextUtils,
    QgsMapThemeCollection,
    QgsProject,
    QgsProviderRegistry,
    QgsRectangle,
    QgsReferencedRectangle,
    QgsSettings,
    QgsVectorLayer,
    QgsWkbTypes,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.utils import iface

from ..core.crs_ref import crs_ref
from ..core.quiet_credentials import no_login_prompt
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from . import guards
from ._compat import enum_value
from .core_tools import _jsonable_value


def _runs_own_sql(args: dict) -> bool:






    return bool(str(args.get("sql") or "").strip())


_PG_MODES = ["endpoint_using_auth_manager", "service_using_auth_manager", "service_only"]
_PG_SSL_MODES = ["prefer", "disable", "allow", "require", "verify-ca", "verify-full"]
_SCALAR = {"type": ["string", "number", "boolean"]}


def register_harvest_project_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="list_connections",
        danger="read",
        catalog=True,
        input_schema={
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
            },
            "required": [],
        },
        handler=_list_connections,
    ))

    registry.register(Tool(
        name="create_postgresql_connection",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "connection_mode": {"type": "string", "enum": _PG_MODES},
                "host": {"type": "string"},
                "port": {"type": "integer"},
                "database": {"type": "string"},
                "auth_config_id": {"type": "string"},
                "ssl_mode": {"type": "string", "enum": _PG_SSL_MODES},
                "service": {"type": "string"},
            },
            "required": ["name", "connection_mode"],
        },
        handler=_create_postgresql_connection,
    ))

    registry.register(Tool(
        name="list_connection_tables",
        danger="read",
        input_schema={
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "connection": {"type": "string"},
                "schema": {"type": "string"},
            },
            "required": ["provider", "connection"],
        },
        handler=_list_connection_tables,
    ))

    registry.register(Tool(
        name="add_layer_from_connection",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add {table} from {connection}"),
        input_schema={
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "connection": {"type": "string"},
                "table": {"type": "string"},
                "schema": {"type": "string"},
                "sql": {"type": "string"},
                "geometry_column": {"type": "string"},
                "primary_key": {"type": "string"},
                "name": {"type": "string"},
            },
            "required": ["provider", "connection"],
        },
        handler=_add_layer_from_connection,
        destructive_when=_runs_own_sql,
    ))

    registry.register(Tool(
        name="execute_connection_sql",


        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Run SQL on {connection}"),
        input_schema={
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "connection": {"type": "string"},
                "sql": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": _CONNECTION_SQL_MAX_ROWS},
            },
            "required": ["provider", "connection", "sql"],
        },
        handler=_execute_connection_sql,
    ))

    registry.register(Tool(
        name="get_bookmarks",
        danger="read",
        input_schema={
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["all", "project", "user"]},
            },
            "required": [],
        },
        handler=_get_bookmarks,
    ))

    registry.register(Tool(
        name="add_bookmark",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Add the bookmark {name}"),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "xmin": {"type": "number"},
                "ymin": {"type": "number"},
                "xmax": {"type": "number"},
                "ymax": {"type": "number"},
                "crs": {"type": "string"},
                "group": {"type": "string"},
                "scope": {"type": "string", "enum": ["project", "user"]},
            },
            "required": ["name", "xmin", "ymin", "xmax", "ymax"],
        },
        handler=_add_bookmark,
    ))

    registry.register(Tool(
        name="remove_bookmark",
        danger="destructive",
        input_schema={
            "type": "object",
            "properties": {"bookmark_id": {"type": "string"}},
            "required": ["bookmark_id"],
        },
        handler=_remove_bookmark,
    ))

    registry.register(Tool(
        name="get_map_themes",
        danger="read",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_map_themes,
    ))

    registry.register(Tool(
        name="add_map_theme",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_add_map_theme,
    ))

    registry.register(Tool(
        name="remove_map_theme",
        danger="destructive",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_remove_map_theme,
    ))

    registry.register(Tool(
        name="apply_map_theme",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_apply_map_theme,
    ))

    registry.register(Tool(
        name="get_project_variables",
        danger="read",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_project_variables,
    ))

    registry.register(Tool(
        name="set_project_variable",
        danger="write",
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": dict(_SCALAR),
            },
            "required": ["key", "value"],
        },
        handler=_set_project_variable,
    ))

    registry.register(Tool(
        name="get_setting",
        danger="read",
        input_schema={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
        handler=_get_setting,
        argument_check=guards.settings_key_refusal,
    ))

    registry.register(Tool(
        name="set_setting",
        danger="destructive",
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": dict(_SCALAR),
            },
            "required": ["key", "value"],
        },
        handler=_set_setting,
        argument_check=guards.settings_key_refusal,
    ))











_SECRET_NAMES = ("sslpassword", "password", "pass", "pwd")


def _quoted_run(quote: str) -> str:
    return f"{quote}(?:\\\\.|[^{quote}\\\\])*{quote}"


_URI_SECRET_RE = re.compile(
    r"\b(" + "|".join(_SECRET_NAMES) + ")=(" + "|".join((_quoted_run("'"), _quoted_run('"'), r"\S*")) + ")",
    re.IGNORECASE,
)


def _has_flag(flags, flag) -> bool:
    if flag is None:
        return False
    try:
        return bool(flags & flag)
    except TypeError:
        return False


def _redact_uri(uri: str) -> str:
    return _URI_SECRET_RE.sub(r"\1=***", uri or "")


_DATABASE_PROVIDERS_HINT = "a postgres, ogr (GeoPackage), spatialite, mssql or oracle connection"


class _NoConnections(Exception):


    def __init__(self, unknown: bool, reason=None):
        super().__init__(str(reason))
        self.unknown = unknown
        self.reason = reason


def _connections_by_name(provider: str) -> dict:

    metadata = QgsProviderRegistry.instance().providerMetadata(provider)
    if metadata is None:
        raise _NoConnections(unknown=True)
    try:
        return metadata.connections(False)
    except Exception as exc:
        raise _NoConnections(unknown=False, reason=exc) from exc


def _saved_connection(provider: str, connection: str):

    try:
        available = _connections_by_name(provider)
    except _NoConnections as missing:
        if missing.unknown:
            return None, tool_error(f"Unknown data provider: {provider!r}", "INVALID_ARGS",
                                    "Call list_connections to see the providers that have saved connections.")
        return None, tool_error(f"Provider {provider!r} has no saved-connection support: {missing.reason}",
                                "INVALID_ARGS",
                                "Use a database provider such as postgres, ogr (GeoPackage), spatialite, mssql "
                                "or oracle.")
    found = available.get(connection)
    if found is not None:
        return found, None
    return None, tool_error(f"No saved {provider!r} connection named {connection!r} (available: {sorted(available)})",
                            "INVALID_ARGS", "Call list_connections for the exact connection names.")


def _saved_database(provider: str, connection: str, not_a_database: str):

    conn, error = _saved_connection(provider, connection)
    if error is None and not isinstance(conn, QgsAbstractDatabaseProviderConnection):
        error = tool_error(not_a_database, "INVALID_ARGS", f"Pick {_DATABASE_PROVIDERS_HINT}.")
    return (None, error) if error else (conn, None)


def _bookmark_summary(bookmark, scope: str) -> dict:
    box = bookmark.extent()
    crs = box.crs()
    corners = zip(("xmin", "ymin", "xmax", "ymax"),
                  (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()))
    return {"id": bookmark.id(), "name": bookmark.name(), "group": bookmark.group(), "scope": scope,
            "extent": dict(corners), "crs": crs.authid() if crs.isValid() else None}


def _bookmark_managers(scope: str) -> list:
    managers = []
    if scope in ("project", "all"):
        managers.append(("project", QgsProject.instance().bookmarkManager()))
    if scope in ("user", "all"):
        managers.append(("user", QgsApplication.bookmarkManager()))
    return managers


def _theme_not_found(name: str) -> dict:
    existing = QgsProject.instance().mapThemeCollection().mapThemes()
    return tool_error(
        f"Map theme not found: {name!r}. Existing themes: {existing}",
        "INVALID_ARGS",
        "Call get_map_themes for the exact names, or add_map_theme to create one.",
    )






def _connection_rows(provider: str) -> list:

    try:
        available = _connections_by_name(provider)
    except _NoConnections:
        return []
    rows = []
    for label, conn in available.items():
        row = {"provider": provider, "name": label}
        try:
            row["uri"] = _redact_uri(conn.uri())
        except Exception:  # nosec B110
            pass
        rows.append(row)
    return rows


def _list_connections(args: dict) -> dict:
    registry = QgsProviderRegistry.instance()
    only = args.get("provider")
    if only and registry.providerMetadata(only) is None:
        return tool_error(f"Unknown data provider: {only!r}", "INVALID_ARGS",
                          f"Use one of: {', '.join(sorted(registry.providerList()))}")
    rows = [row for key in ([only] if only else registry.providerList()) for row in _connection_rows(key)]
    return {"connections": rows, "count": len(rows)}




_PG_MODE_PARAMS = {
    "endpoint_using_auth_manager": ("host", "port", "database", "auth_config_id"),
    "service_using_auth_manager": ("service", "auth_config_id"),
    "service_only": ("service",),
}








_PG_CONNECT_TIMEOUT_S = 5


_PG_TEXT_ARGS = ("name", "host", "port", "database", "auth_config_id", "service")


def _pg_settings(args: dict):






    mode = args["connection_mode"]
    needs = _PG_MODE_PARAMS[mode]
    settings = {key: "" if args.get(key) is None else str(args.get(key)).strip() for key in _PG_TEXT_ARGS}
    missing = next((key for key in ("name", *needs) if not settings[key]), None)
    if missing:
        return None, tool_error(f"{missing} is required for connection_mode {mode!r}", "INVALID_ARGS",
                                f"connection_mode {mode!r} needs: {', '.join(needs)}")
    stray = next((key for key in _PG_TEXT_ARGS if settings[key] and key not in {"name", "database", *needs}), None)
    if stray:
        return None, tool_error(f"{stray} is not used by connection_mode {mode!r}", "INVALID_ARGS",
                                f"Drop {stray}, or pick the mode that uses it.")
    port = None
    if "port" in needs:
        try:
            port = int(settings["port"])
        except ValueError:
            port = 0
        if port < 1 or port > 65535:
            return None, tool_error("PostgreSQL port must be an integer from 1 to 65535", "INVALID_ARGS",
                                    "Ask the user for the real database port; do not assume 5432.")
    settings["port"] = port

    ssl_mode = str(args.get("ssl_mode") or "prefer").strip().lower().replace("_", "-")
    member = "Ssl" + "".join(word.capitalize() for word in ssl_mode.split("-"))
    ssl = (enum_value((QgsDataSourceUri, f"SslMode.{member}"), (QgsDataSourceUri, member))
           if ssl_mode in _PG_SSL_MODES else None)
    if ssl is None:
        return None, tool_error(f"Unknown SSL mode: {ssl_mode!r}", "INVALID_ARGS", f"Use one of {_PG_SSL_MODES}")
    settings.update(mode=mode, ssl_mode=ssl_mode, ssl=ssl)
    auth = settings["auth_config_id"]
    if auth and auth not in QgsApplication.authManager().configIds():
        return None, tool_error(f"Authentication configuration {auth!r} does not exist", "INVALID_ARGS",
                                "Create it in Settings > Options > Authentication, then pass its id.")
    return settings, None


def _create_postgresql_connection(args: dict) -> dict:
    settings, refusal = _pg_settings(args)
    if refusal:
        return refusal
    name = settings["name"]
    postgres = QgsProviderRegistry.instance().providerMetadata("postgres")
    if postgres is None:
        return tool_error("The PostgreSQL provider is not available in this QGIS installation")
    try:
        taken = name in postgres.connections(False)
    except Exception as e:
        return tool_error(f"PostgreSQL saved connections are unavailable: {e}")
    if taken:
        return tool_error(f"A saved PostgreSQL connection named {name!r} already exists", "INVALID_ARGS",
                          "Pick another name, or use the existing connection with list_connection_tables.")

    uri = QgsDataSourceUri()
    host, port, database, service = settings["host"], settings["port"], settings["database"], settings["service"]
    if service:
        uri.setConnection(service, database, "", "", settings["ssl"], settings["auth_config_id"])
        where = f"service {service!r}"
    else:
        uri.setConnection(host, str(port), database, "", "", settings["ssl"], settings["auth_config_id"])
        where = f"{host}:{port}/{database}"



    uri.setParam("connect_timeout", str(_PG_CONNECT_TIMEOUT_S))




    try:
        with no_login_prompt():
            opened = postgres.createConnection(uri.uri(False), {})
            opened.executeSql("SELECT 1")
    except Exception as e:
        return tool_error(f"Failed to connect to PostgreSQL ({where}): {e}", "EXECUTION_FAILED",
                          "Check the host, port, database, service name and credentials, then call again.")
    postgres.saveConnection(opened, name)

    answer = {"provider": "postgres", "name": name, "connection_mode": settings["mode"]}
    answer.update((key, settings[key]) for key in ("host", "database", "auth_config_id", "service") if settings[key])
    if port is not None:
        answer["port"] = port
    answer.update(ssl_mode=settings["ssl_mode"], validated=True)
    return answer


_DB = QgsAbstractDatabaseProviderConnection

_TABLE_KINDS = (("vector", "Vector"), ("raster", "Raster"), ("view", "View"), ("aspatial", "Aspatial"))


def _can(conn, capability: str) -> bool:
    return _has_flag(conn.capabilities(), enum_value((_DB, f"Capability.{capability}"), (_DB, capability)))


def _table_row(table, flag_by_label: dict) -> dict:
    try:
        crs = [c.authid() for c in table.crsList() if c.authid()]
    except Exception:
        crs = []
    flags = table.flags()
    return {"name": table.tableName(), "schema": table.schema() or None,
            "geometry_column": table.geometryColumn() or None, "primary_key": list(table.primaryKeyColumns()),
            "comment": table.comment() or None, "crs": crs,
            "kinds": [label for label, flag in flag_by_label.items() if _has_flag(flags, flag)]}


def _tables_failure(conn, connection: str, exc) -> dict:

    try:
        uri = conn.uri() or ""
    except Exception:  # nosec B110
        uri = ""
    names_a_file = uri and not uri.startswith(("http", "dbname", "service", "host"))
    if names_a_file and not os.path.exists(uri.split("|")[0]):
        return tool_error(f"The file behind connection {connection!r} is missing: {uri}", "EXECUTION_FAILED",
                          "The saved connection points at a file that no longer exists; load the data "
                          "from its new path with add_vector_layer.")
    return tool_error(f"Could not list the tables of {connection!r}: {exc}", "EXECUTION_FAILED",
                      "Check the connection opens in the Browser panel, then call again.")


def _list_connection_tables(args: dict) -> dict:
    provider, connection, schema = args["provider"], args["connection"], args.get("schema")
    conn, refusal = _saved_database(provider, connection,
                                    f"Connection {connection!r} is not a database connection, it has no tables")
    if refusal:
        return refusal
    schemas = []
    if _can(conn, "Schemas"):
        try:
            schemas = list(conn.schemas())
        except Exception:  # nosec B110
            schemas = []
    head = {"provider": provider, "connection": connection}
    if schemas and schema is None:
        return {**head, "schemas": schemas, "message": "Pass schema to list the tables of one of these schemas"}
    try:
        found = conn.tables(schema or "")
    except Exception as e:
        return _tables_failure(conn, connection, e)
    flag_by_label = {label: enum_value((_DB, f"TableFlag.{member}"), (_DB, member)) for label, member in _TABLE_KINDS}
    rows = [_table_row(table, flag_by_label) for table in found]
    return {**head, "schema": schema, "schemas": schemas, "tables": rows, "count": len(rows)}


def _query_layer(conn, provider: str, sql: str, args: dict):

    if not _can(conn, "SqlLayers"):
        return None, tool_error(f"Provider {provider!r} cannot build layers from SQL queries", "INVALID_ARGS",
                                "Load the table instead and filter it with select_by_attribute or execute_sql.")
    request = _DB.SqlVectorLayerOptions()
    request.sql = sql
    request.layerName = args.get("name") or "query"
    if args.get("geometry_column"):
        request.geometryColumn = args["geometry_column"]
    if args.get("primary_key"):
        request.primaryKeyColumns = [args["primary_key"]]
    return conn.createSqlVectorLayer(request), f"query {sql[:80]!r}"


def _add_layer_from_connection(args: dict) -> dict:
    provider, connection = args["provider"], args["connection"]
    table, sql = args.get("table"), args.get("sql")
    conn, refusal = _saved_database(provider, connection, f"Connection {connection!r} is not a database connection")
    if refusal:
        return refusal
    if sql:
        layer, what = _query_layer(conn, provider, sql, args)
        if layer is None and isinstance(what, dict):
            return what
    elif table:
        layer = QgsVectorLayer(conn.tableUri(args.get("schema") or "", table), args.get("name") or table,
                               conn.providerKey())
        what = f"table {table!r}"
    else:
        return tool_error("Either table or sql must be provided", "INVALID_ARGS",
                          "Call list_connection_tables to pick a table name.")

    if layer is None or not layer.isValid():
        source = layer.dataProvider() if layer is not None else None
        detail = source.error().summary() if source is not None else ""
        return tool_error(f"Failed to load {what} from {connection!r}: {detail}", "EXECUTION_FAILED",
                          "Check the table name and schema with list_connection_tables; a SQL layer may need "
                          "geometry_column and primary_key.")

    QgsProject.instance().addMapLayer(layer)
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "provider": layer.providerType(),
        "geometry_type": QgsWkbTypes.displayString(layer.wkbType()),
        "feature_count": layer.featureCount(),
        "crs": crs_ref(layer.crs()),
    }


_CONNECTION_SQL_MAX_ROWS = 1000


def _execute_connection_sql(args: dict) -> dict:








    provider, connection, sql = args["provider"], args["connection"], str(args.get("sql") or "").strip()
    if not sql:
        return tool_error("sql is empty", "INVALID_ARGS", "Pass the SQL statement to run.")
    limit = max(1, min(int(args.get("limit") or 100), _CONNECTION_SQL_MAX_ROWS))
    conn, refusal = _saved_database(provider, connection, f"Connection {connection!r} is not a database connection")
    if refusal:
        return refusal
    execute_cap = enum_value((_DB, "Capability.ExecuteSql"), (_DB, "ExecuteSql"))
    if execute_cap is not None and not _has_flag(conn.capabilities(), execute_cap):
        return tool_error(f"Provider {provider!r} cannot run SQL on its connections", "INVALID_ARGS",
                          "Load the table with add_layer_from_connection and query it with execute_sql.")

    columns: list[str] = []
    try:
        with no_login_prompt():
            if hasattr(conn, "execSql"):
                result = conn.execSql(sql)
                columns = [str(c) for c in result.columns()]
                rows, truncated = _first_rows(result, limit)
            else:
                every = conn.executeSql(sql) or []
                rows, truncated = every[:limit], len(every) > limit
    except Exception as e:
        return tool_error(f"The database refused the SQL: {_redact_uri(str(e))}", "EXECUTION_FAILED",
                          "Fix the statement in the database's own dialect; list_connection_tables gives "
                          "the exact table and column names.")

    refreshed = _refresh_layers_of(conn)
    response = {
        "provider": provider,
        "connection": connection,
        "columns": columns,
        "rows": [[_sql_cell(v) for v in row] for row in rows],
        "count": len(rows),
        "truncated": truncated,
    }
    if truncated:
        response["message"] = (f"Only the first {limit} rows are shown; add a WHERE, an aggregate or a "
                               f"LIMIT, or raise limit up to {_CONNECTION_SQL_MAX_ROWS}.")
    if refreshed:
        response["refreshed_layers"] = refreshed
    return response


def _first_rows(result, limit: int):

    rows: list = []
    while result.hasNextRow():
        row = result.nextRow()
        if len(rows) == limit:
            return rows, True
        rows.append(row)
    return rows, False


_SQL_CELL_MAX_CHARS = 1000


def _sql_cell(value):
    if isinstance(value, (bytes, bytearray)) or type(value).__name__ == "QByteArray":
        return f"<{len(value)} bytes>"
    value = _jsonable_value(value)


    if isinstance(value, str) and len(value) > _SQL_CELL_MAX_CHARS:
        return f"{value[:_SQL_CELL_MAX_CHARS]}... ({len(value)} characters; select ST_AsText or a measure instead)"
    return value


def _refresh_layers_of(conn) -> list[str]:

    try:
        uri = conn.uri() or ""
    except Exception:  # nosec B110
        return []
    target = QgsDataSourceUri(uri)


    key = (target.database(), target.host(), target.service())
    path = os.path.normcase(os.path.abspath(uri.split("|")[0])) if not any(key) else ""
    names = []
    for layer in QgsProject.instance().mapLayers().values():
        if layer.providerType() != conn.providerKey():
            continue
        if any(key):
            source = QgsDataSourceUri(layer.source())
            if (source.database(), source.host(), source.service()) != key:
                continue
        elif not path or os.path.normcase(os.path.abspath(layer.source().split("|")[0])) != path:
            continue
        layer.reload()
        layer.triggerRepaint()
        names.append(layer.name())
    return names






def _get_bookmarks(args: dict) -> dict:
    scope = args.get("scope") or "all"
    bookmarks = []
    for label, manager in _bookmark_managers(scope):
        bookmarks.extend(_bookmark_summary(b, label) for b in manager.bookmarks())
    return {"bookmarks": bookmarks, "count": len(bookmarks)}


def _add_bookmark(args: dict) -> dict:






    asked = args.get("crs")
    project_crs = QgsProject.instance().crs()
    if asked:
        crs = QgsCoordinateReferenceSystem(asked)
        crs_source = "argument"
    elif project_crs.isValid():
        crs = project_crs
        crs_source = "project"
    else:
        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        crs_source = "default"
    if not crs.isValid():
        return tool_error(f"crs {asked!r} is not a coordinate system QGIS knows.", "INVALID_ARGS",
                          "Pass an authority code such as EPSG:4326, or leave crs out to use the project CRS.")
    rect = QgsRectangle(args["xmin"], args["ymin"], args["xmax"], args["ymax"])
    if rect.isEmpty():
        return tool_error(
            f"The extent is empty: xmax must exceed xmin and ymax must exceed ymin "
            f"(got xmin={args['xmin']}, xmax={args['xmax']}, ymin={args['ymin']}, ymax={args['ymax']}).",
            "INVALID_ARGS",
            f"Take the numbers from get_canvas_extent or get_layer_info; they are read as "
            f"{crs.authid() or 'the project CRS'} unless crs says otherwise.")
    scope = args.get("scope") or "project"
    bookmark = QgsBookmark()
    bookmark.setName(args["name"])
    bookmark.setGroup(args.get("group") or "")
    bookmark.setExtent(QgsReferencedRectangle(rect, crs))
    _label, store = _bookmark_managers(scope)[0]
    added = store.addBookmark(bookmark)

    pair = tuple(added[:2]) if isinstance(added, (list, tuple)) else (added, bool(added))
    bookmark_id, ok = pair
    if not ok:
        return tool_error(f"QGIS refused the bookmark {args['name']!r}", "EXECUTION_FAILED",
                          "Check the extent is valid in the given CRS.")
    return {"id": bookmark_id, "name": args["name"], "scope": scope, "crs": crs.authid(),
            "crs_source": crs_source}


def _remove_bookmark(args: dict) -> dict:
    bookmark_id = args["bookmark_id"]
    for scope, manager in _bookmark_managers("all"):
        match = [b for b in manager.bookmarks() if b.id() == bookmark_id]
        if match:
            if not manager.removeBookmark(bookmark_id):
                return tool_error(
                    f"QGIS could not remove bookmark {bookmark_id!r} from the {scope} store.",
                    "EXECUTION_FAILED",
                    "Call get_bookmarks to see whether it is still there; a bookmark in the user "
                    "profile store cannot be removed while the project one is open.")
            return {"removed": bookmark_id, "name": match[0].name(), "scope": scope}
    return tool_error(f"No bookmark with id {bookmark_id!r}", "INVALID_ARGS",
                      "Call get_bookmarks and pass the 'id' field, not the name.")


def _themes():
    return QgsProject.instance().mapThemeCollection()


def _visible_count(name: str) -> int:
    return len(_themes().mapThemeVisibleLayerIds(name))


def _layer_panel():

    return QgsProject.instance().layerTreeRoot(), iface.layerTreeView().layerTreeModel()


def _get_map_themes(args: dict) -> dict:
    project = QgsProject.instance()

    def described(name):
        ids = _themes().mapThemeVisibleLayerIds(name)
        shown = []
        for layer_id in ids:
            layer = project.mapLayer(layer_id)
            shown.append(layer.name() if layer else layer_id)
        return {"name": name, "visible_layer_count": len(ids), "visible_layers": shown, "visible_layer_ids": ids}

    themes = [described(name) for name in _themes().mapThemes()]
    return {"themes": themes, "count": len(themes)}


def _add_map_theme(args: dict) -> dict:
    name = args["name"]
    snapshot = QgsMapThemeCollection.createThemeFromCurrentState(*_layer_panel())
    replacing = _themes().hasMapTheme(name)
    (_themes().update if replacing else _themes().insert)(name, snapshot)
    return {"name": name, "action": "updated" if replacing else "created", "visible_layer_count": _visible_count(name)}


def _remove_map_theme(args: dict) -> dict:
    name = args["name"]
    if not _themes().hasMapTheme(name):
        return _theme_not_found(name)
    _themes().removeMapTheme(name)
    return {"removed": name}


def _apply_map_theme(args: dict) -> dict:
    name = args["name"]
    if not _themes().hasMapTheme(name):
        return _theme_not_found(name)
    _themes().applyTheme(name, *_layer_panel())
    iface.mapCanvas().refresh()
    return {"applied": name, "visible_layer_count": _visible_count(name)}






def _get_project_variables(args: dict) -> dict:
    project = QgsProject.instance()
    scope = QgsExpressionContextUtils.projectScope(project)
    variables = {}
    for variable in scope.variableNames():
        if variable in ("layers", "layer_ids"):
            continue
        value = _jsonable_value(scope.variable(variable))
        if isinstance(value, str) and len(value) > 300:
            value = value[:300] + "..."
        variables[variable] = value
    custom = {key: _jsonable_value(value) for key, value in project.customVariables().items()}
    return {"custom": custom, "variables": variables, "count": len(custom),
            "omitted": ["layers", "layer_ids"]}


def _set_project_variable(args: dict) -> dict:
    key, value = args["key"], args["value"]
    if not str(key).strip():
        return tool_error("key must not be empty", "INVALID_ARGS", "Use a short identifier such as 'client_name'.")
    project = QgsProject.instance()
    previous = project.customVariables().get(key)
    QgsExpressionContextUtils.setProjectVariable(project, key, value)
    return {"key": key, "value": _jsonable_value(value), "previous": _jsonable_value(previous),
            "expression": f"@{key}"}


_SETTINGS_DENY_TERMS = ("password", "passwd", "pwd", "token", "secret", "apikey", "api_key", "authcfg")


def _denied_setting_key(key: str) -> dict:
    return tool_error(
        f"{key!r} looks like a credential key; QGIS settings are not the place to read or write it.",
        "PERMISSION_DENIED",
        "Credentials are managed by the QGIS authentication system (Settings > Options > Authentication).",
    )


def _get_setting(args: dict) -> dict:
    key = args["key"]
    if any(term in key.lower() for term in _SETTINGS_DENY_TERMS):
        return _denied_setting_key(key)
    settings = QgsSettings()
    exists = settings.contains(key)
    result = {"key": key, "exists": exists, "value": _jsonable_value(settings.value(key)) if exists else None}
    if not exists:
        settings.beginGroup(key)
        try:
            result["child_keys"] = list(settings.childKeys())[:50]
            result["child_groups"] = list(settings.childGroups())[:50]
        finally:
            settings.endGroup()
    return result


def _set_setting(args: dict) -> dict:
    key, value = args["key"], args["value"]
    if any(term in key.lower() for term in _SETTINGS_DENY_TERMS):
        return _denied_setting_key(key)
    settings = QgsSettings()
    previous = _jsonable_value(settings.value(key)) if settings.contains(key) else None
    settings.setValue(key, value)
    settings.sync()
    return {"key": key, "value": _jsonable_value(settings.value(key)), "previous": previous}

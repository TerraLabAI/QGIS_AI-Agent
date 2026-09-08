# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Saved connections, bookmarks, map themes, project variables and QGIS settings."""








from __future__ import annotations

import os
import re

from qgis.core import (
    QgsAbstractDatabaseProviderConnection,
    QgsApplication,
    QgsBookmark,
    QgsCoordinateReferenceSystem,
    QgsCredentials,
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
from qgis.utils import iface

from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .core_tools import _jsonable_value

_PG_MODES = ["endpoint_using_auth_manager", "service_using_auth_manager", "service_only"]
_PG_SSL_MODES = ["prefer", "disable", "allow", "require", "verify-ca", "verify-full"]
_SCALAR = {"type": ["string", "number", "boolean"]}


def register_harvest_project_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="list_connections",
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
    ))

    registry.register(Tool(
        name="get_bookmarks",
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
        input_schema={
            "type": "object",
            "properties": {"bookmark_id": {"type": "string"}},
            "required": ["bookmark_id"],
        },
        handler=_remove_bookmark,
        destructive=True,
    ))

    registry.register(Tool(
        name="get_map_themes",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_map_themes,
    ))

    registry.register(Tool(
        name="add_map_theme",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_add_map_theme,
    ))

    registry.register(Tool(
        name="remove_map_theme",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_remove_map_theme,
        destructive=True,
    ))

    registry.register(Tool(
        name="apply_map_theme",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=_apply_map_theme,
    ))

    registry.register(Tool(
        name="get_project_variables",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_project_variables,
    ))

    registry.register(Tool(
        name="set_project_variable",
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
        input_schema={
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
        handler=_get_setting,
    ))

    registry.register(Tool(
        name="set_setting",
        input_schema={
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": dict(_SCALAR),
            },
            "required": ["key", "value"],
        },
        handler=_set_setting,
        destructive=True,
    ))







_URI_SECRET_RE = re.compile(r"\b(password|pass|pwd)=('[^']*'|\"[^\"]*\"|\S*)", re.IGNORECASE)


def qgis_enum(root, *paths):
    """The first attribute path that resolves on ``root``, else None."""





    for path in paths:
        obj = root
        for part in path.split("."):
            obj = getattr(obj, part, None)
            if obj is None:
                break
        else:
            return obj
    return None


def _has_flag(flags, flag) -> bool:
    if flag is None:
        return False
    try:
        return bool(flags & flag)
    except TypeError:
        return False


def _redact_uri(uri: str) -> str:
    return _URI_SECRET_RE.sub(r"\1=***", uri or "")


def _saved_connection(provider: str, connection: str):
    """Return (connection, None) or (None, error_dict)."""
    metadata = QgsProviderRegistry.instance().providerMetadata(provider)
    if metadata is None:
        return None, tool_error(
            f"Unknown data provider: {provider!r}",
            "INVALID_ARGS",
            "Call list_connections to see the providers that have saved connections.",
        )
    try:
        connections = metadata.connections(False)
    except Exception as e:
        return None, tool_error(
            f"Provider {provider!r} has no saved-connection support: {e}",
            "INVALID_ARGS",
            "Use a database provider such as postgres, ogr (GeoPackage), spatialite, mssql or oracle.",
        )
    conn = connections.get(connection)
    if conn is None:
        return None, tool_error(
            f"No saved {provider!r} connection named {connection!r} (available: {sorted(connections)})",
            "INVALID_ARGS",
            "Call list_connections for the exact connection names.",
        )
    return conn, None


class _QuietCredentials(QgsCredentials):
    """Refuse every credential request instead of opening the Enter Credentials dialog."""

    def request(self, realm, username, password, message=""):
        return False, username, password

    def requestMasterPassword(self, password, stored=False):
        return False, password


def _bookmark_summary(bookmark, scope: str) -> dict:
    extent = bookmark.extent()
    return {
        "id": bookmark.id(),
        "name": bookmark.name(),
        "group": bookmark.group(),
        "scope": scope,
        "extent": {
            "xmin": extent.xMinimum(),
            "ymin": extent.yMinimum(),
            "xmax": extent.xMaximum(),
            "ymax": extent.yMaximum(),
        },
        "crs": extent.crs().authid() if extent.crs().isValid() else None,
    }


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






def _list_connections(args: dict) -> dict:
    registry = QgsProviderRegistry.instance()
    provider = args.get("provider")
    providers = [provider] if provider else registry.providerList()
    entries = []
    for name in providers:
        metadata = registry.providerMetadata(name)
        if metadata is None:
            if provider:
                return tool_error(
                    f"Unknown data provider: {name!r}",
                    "INVALID_ARGS",
                    f"Use one of: {', '.join(sorted(registry.providerList()))}",
                )
            continue
        try:
            connections = metadata.connections(False)
        except Exception:  # nosec B112 - provider has no connection support
            continue
        for conn_name, conn in connections.items():
            entry = {"provider": name, "name": conn_name}
            try:
                entry["uri"] = _redact_uri(conn.uri())
            except Exception:  # nosec B110 - provider data is optional
                pass
            entries.append(entry)
    return {"connections": entries, "count": len(entries)}




_PG_MODE_PARAMS = {
    "endpoint_using_auth_manager": ("host", "port", "database", "auth_config_id"),
    "service_using_auth_manager": ("service", "auth_config_id"),
    "service_only": ("service",),
}








_PG_CONNECT_TIMEOUT_S = 5


def _create_postgresql_connection(args: dict) -> dict:
    mode = args["connection_mode"]
    required = _PG_MODE_PARAMS[mode]
    given = {
        key: "" if args.get(key) is None else str(args.get(key)).strip()
        for key in ("name", "host", "port", "database", "auth_config_id", "service")
    }
    for key in ("name", *required):
        if not given[key]:
            return tool_error(f"{key} is required for connection_mode {mode!r}", "INVALID_ARGS",
                              f"connection_mode {mode!r} needs: {', '.join(required)}")
    allowed = {"name", "database", *required}
    for key, value in given.items():
        if value and key not in allowed:
            return tool_error(f"{key} is not used by connection_mode {mode!r}", "INVALID_ARGS",
                              f"Drop {key}, or pick the mode that uses it.")
    name, host, database = given["name"], given["host"], given["database"]
    auth_config_id, service = given["auth_config_id"], given["service"]
    port = None
    if "port" in required:
        try:
            port = int(given["port"])
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            return tool_error("PostgreSQL port must be an integer from 1 to 65535", "INVALID_ARGS",
                              "Ask the user for the real database port; do not assume 5432.")

    ssl_mode = str(args.get("ssl_mode") or "prefer").strip().lower().replace("_", "-")
    ssl_attr = {"prefer": "SslPrefer", "disable": "SslDisable", "allow": "SslAllow",
                "require": "SslRequire", "verify-ca": "SslVerifyCa", "verify-full": "SslVerifyFull"}.get(ssl_mode)
    ssl_value = qgis_enum(QgsDataSourceUri, f"SslMode.{ssl_attr}", ssl_attr) if ssl_attr else None
    if ssl_value is None:
        return tool_error(f"Unknown SSL mode: {ssl_mode!r}", "INVALID_ARGS", f"Use one of {_PG_SSL_MODES}")

    if auth_config_id and auth_config_id not in QgsApplication.authManager().configIds():
        return tool_error(f"Authentication configuration {auth_config_id!r} does not exist", "INVALID_ARGS",
                          "Create it in Settings > Options > Authentication, then pass its id.")

    metadata = QgsProviderRegistry.instance().providerMetadata("postgres")
    if metadata is None:
        return tool_error("The PostgreSQL provider is not available in this QGIS installation")
    try:
        existing = metadata.connections(False)
    except Exception as e:
        return tool_error(f"PostgreSQL saved connections are unavailable: {e}")
    if name in existing:
        return tool_error(f"A saved PostgreSQL connection named {name!r} already exists", "INVALID_ARGS",
                          "Pick another name, or use the existing connection with list_connection_tables.")

    uri = QgsDataSourceUri()
    if service:
        uri.setConnection(service, database, "", "", ssl_value, auth_config_id)
        target = f"service {service!r}"
    else:
        uri.setConnection(host, str(port), database, "", "", ssl_value, auth_config_id)
        target = f"{host}:{port}/{database}"



    uri.setParam("connect_timeout", str(_PG_CONNECT_TIMEOUT_S))




    previous = QgsCredentials.instance()
    quiet = _QuietCredentials()
    quiet.setInstance(quiet)
    try:
        connection = metadata.createConnection(uri.uri(False), {})
        connection.executeSql("SELECT 1")
    except Exception as e:
        return tool_error(f"Failed to connect to PostgreSQL ({target}): {e}", "EXECUTION_FAILED",
                          "Check the host, port, database, service name and credentials, then call again.")
    finally:
        quiet.setInstance(previous)

    metadata.saveConnection(connection, name)
    details = {key: given[key] for key in ("host", "database", "auth_config_id", "service") if given[key]}
    if port is not None:
        details["port"] = port
    return {"provider": "postgres", "name": name, "connection_mode": mode, **details,
            "ssl_mode": ssl_mode, "validated": True}


def _list_connection_tables(args: dict) -> dict:
    provider, connection, schema = args["provider"], args["connection"], args.get("schema")
    conn, error = _saved_connection(provider, connection)
    if error:
        return error
    if not isinstance(conn, QgsAbstractDatabaseProviderConnection):
        return tool_error(f"Connection {connection!r} is not a database connection, it has no tables",
                          "INVALID_ARGS", "Pick a postgres, ogr (GeoPackage), spatialite, mssql or oracle connection.")

    caps = conn.capabilities()
    schemas = []
    if _has_flag(caps, qgis_enum(QgsAbstractDatabaseProviderConnection, "Capability.Schemas", "Schemas")):
        try:
            schemas = list(conn.schemas())
        except Exception:  # nosec B110 - provider data is optional
            schemas = []
    if schema is None and schemas:
        return {"provider": provider, "connection": connection, "schemas": schemas,
                "message": "Pass schema to list the tables of one of these schemas"}

    try:
        raw_tables = conn.tables(schema or "")
    except Exception as e:
        uri = ""
        try:
            uri = conn.uri() or ""
        except Exception:  # nosec B110 - connection URI is optional
            pass
        if uri and not uri.startswith(("http", "dbname", "service", "host")) and not os.path.exists(uri.split("|")[0]):
            return tool_error(f"The file behind connection {connection!r} is missing: {uri}", "EXECUTION_FAILED",
                              "The saved connection points at a file that no longer exists; load the data "
                              "from its new path with add_vector_layer.")
        return tool_error(f"Could not list the tables of {connection!r}: {e}", "EXECUTION_FAILED",
                          "Check the connection opens in the Browser panel, then call again.")

    kind_attrs = (("vector", "Vector"), ("raster", "Raster"), ("view", "View"), ("aspatial", "Aspatial"))
    kinds = [(label, qgis_enum(QgsAbstractDatabaseProviderConnection, f"TableFlag.{attr}", attr))
             for label, attr in kind_attrs]
    tables = []
    for table in raw_tables:
        flags = table.flags()
        try:
            crs_list = [c.authid() for c in table.crsList() if c.authid()]
        except Exception:
            crs_list = []
        tables.append({
            "name": table.tableName(),
            "schema": table.schema() or None,
            "geometry_column": table.geometryColumn() or None,
            "primary_key": list(table.primaryKeyColumns()),
            "comment": table.comment() or None,
            "crs": crs_list,
            "kinds": [label for label, flag in kinds if _has_flag(flags, flag)],
        })
    return {"provider": provider, "connection": connection, "schema": schema,
            "schemas": schemas, "tables": tables, "count": len(tables)}


def _add_layer_from_connection(args: dict) -> dict:
    provider, connection = args["provider"], args["connection"]
    table, schema, sql = args.get("table"), args.get("schema"), args.get("sql")
    conn, error = _saved_connection(provider, connection)
    if error:
        return error
    if not isinstance(conn, QgsAbstractDatabaseProviderConnection):
        return tool_error(f"Connection {connection!r} is not a database connection", "INVALID_ARGS",
                          "Pick a postgres, ogr (GeoPackage), spatialite, mssql or oracle connection.")

    if sql:
        sql_layers = qgis_enum(QgsAbstractDatabaseProviderConnection, "Capability.SqlLayers", "SqlLayers")
        if not _has_flag(conn.capabilities(), sql_layers):
            return tool_error(f"Provider {provider!r} cannot build layers from SQL queries", "INVALID_ARGS",
                              "Load the table instead and filter it with select_by_attribute or execute_sql.")
        options = QgsAbstractDatabaseProviderConnection.SqlVectorLayerOptions()
        options.sql = sql
        options.layerName = args.get("name") or "query"
        if args.get("geometry_column"):
            options.geometryColumn = args["geometry_column"]
        if args.get("primary_key"):
            options.primaryKeyColumns = [args["primary_key"]]
        layer = conn.createSqlVectorLayer(options)
        target = f"query {sql[:80]!r}"
    elif table:
        uri = conn.tableUri(schema or "", table)
        layer = QgsVectorLayer(uri, args.get("name") or table, conn.providerKey())
        target = f"table {table!r}"
    else:
        return tool_error("Either table or sql must be provided", "INVALID_ARGS",
                          "Call list_connection_tables to pick a table name.")

    if layer is None or not layer.isValid():
        detail = ""
        if layer is not None and layer.dataProvider() is not None:
            detail = layer.dataProvider().error().summary()
        return tool_error(f"Failed to load {target} from {connection!r}: {detail}", "EXECUTION_FAILED",
                          "Check the table name and schema with list_connection_tables; a SQL layer may need "
                          "geometry_column and primary_key.")

    QgsProject.instance().addMapLayer(layer)
    return {
        "layer_id": layer.id(),
        "name": layer.name(),
        "provider": layer.providerType(),
        "geometry_type": QgsWkbTypes.displayString(layer.wkbType()),
        "feature_count": layer.featureCount(),
        "crs": layer.crs().authid(),
    }






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
    manager = _bookmark_managers(scope)[0][1]
    result = manager.addBookmark(bookmark)

    bookmark_id, ok = (result[0], result[1]) if isinstance(result, (list, tuple)) else (result, bool(result))
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


def _get_map_themes(args: dict) -> dict:
    collection = QgsProject.instance().mapThemeCollection()
    project = QgsProject.instance()
    themes = []
    for name in collection.mapThemes():
        layer_ids = collection.mapThemeVisibleLayerIds(name)
        layers = [project.mapLayer(i) for i in layer_ids]
        themes.append({
            "name": name,
            "visible_layer_count": len(layer_ids),
            "visible_layers": [layer.name() if layer else layer_id for layer, layer_id in zip(layers, layer_ids)],
            "visible_layer_ids": layer_ids,
        })
    return {"themes": themes, "count": len(themes)}


def _add_map_theme(args: dict) -> dict:
    name = args["name"]
    collection = QgsProject.instance().mapThemeCollection()
    root = QgsProject.instance().layerTreeRoot()
    model = iface.layerTreeView().layerTreeModel()
    record = QgsMapThemeCollection.createThemeFromCurrentState(root, model)
    action = "updated" if collection.hasMapTheme(name) else "created"
    if action == "updated":
        collection.update(name, record)
    else:
        collection.insert(name, record)
    return {"name": name, "action": action,
            "visible_layer_count": len(collection.mapThemeVisibleLayerIds(name))}


def _remove_map_theme(args: dict) -> dict:
    name = args["name"]
    collection = QgsProject.instance().mapThemeCollection()
    if not collection.hasMapTheme(name):
        return _theme_not_found(name)
    collection.removeMapTheme(name)
    return {"removed": name}


def _apply_map_theme(args: dict) -> dict:
    name = args["name"]
    collection = QgsProject.instance().mapThemeCollection()
    if not collection.hasMapTheme(name):
        return _theme_not_found(name)
    root = QgsProject.instance().layerTreeRoot()
    model = iface.layerTreeView().layerTreeModel()
    collection.applyTheme(name, root, model)
    iface.mapCanvas().refresh()
    return {"applied": name, "visible_layer_count": len(collection.mapThemeVisibleLayerIds(name))}






def _get_project_variables(args: dict) -> dict:
    project = QgsProject.instance()
    scope = QgsExpressionContextUtils.projectScope(project)
    variables = {}
    for name in scope.variableNames():
        if name in ("layers", "layer_ids"):
            continue
        value = _jsonable_value(scope.variable(name))
        if isinstance(value, str) and len(value) > 300:
            value = value[:300] + "..."
        variables[name] = value
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

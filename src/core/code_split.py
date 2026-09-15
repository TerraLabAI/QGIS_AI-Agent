# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which ``execute_code`` snippets may leave the QGIS process."""





















from __future__ import annotations

import ast
import re






TRAP_RE = re.compile(
    r"^(set|add|remove|delete|change|commit|rollBack|trigger|reload|refresh|write|read|save|load|import|"
    r"clear|truncate|create|rename|select|selected|deselect|invert|modify|update|move|insert|split|"
    r"reshape|begin|end|destroy|start|undo|redo|take|reorder|resolve|emit)([A-Z0-9_]|$)"
)
TRAP_KEEP = frozenset({"readOnly", "createExpressionContext", "createExpressionContextScope", "createMapRenderer"})
TRAP_BASES = (
    "QgsProject", "QgsMapLayer", "QgsDataProvider", "QgsMapLayerStore", "QgsLayerTreeNode", "QgsFeatureRenderer",
    "QgsSymbol", "QgsSymbolLayer", "QgsRasterRenderer", "QgsAbstractVectorLayerLabeling", "QgsPalLayerSettings",
    "QgsMapLayerStyleManager", "QgsVectorLayerEditBuffer",
)

LIVE_NAMES = frozenset({
    "iface", "processing", "qgis", "edit", "QgsApplication", "QgsMapCanvas", "QgsMessageBar", "QgsGui",
    "QgsTask", "QgsTaskManager", "QgsExpressionContextUtils", "QgsVectorFileWriter", "QgsRasterFileWriter",
    "QgsMapSettings", "QgsMapRendererParallelJob", "QgsMapRendererSequentialJob",
    "QgsMapRendererCustomPainterJob", "QImage", "QPainter", "QFont", "QBrush", "QPen", "QTransform",
    "QPixmap", "QIcon", "QWidget", "QDialog", "QMessageBox", "QApplication", "QCoreApplication", "QTimer",
    "QThread", "QEventLoop",
})
LIVE_NAME_PREFIXES = (
    "QgsLayerTree", "QgsLayout", "QgsPrintLayout", "QgsProcessing", "QgsMapTool", "QgsRubberBand",
    "QgsVertexMarker", "QgsMapLayerAction", "QgsSnapping", "QgsAuth",
)
LIVE_MODULES = (
    "processing", "console", "PyQt5", "PyQt6", "sip", "qgis.utils", "qgis.gui", "qgis._gui",
    "qgis.PyQt.QtWidgets", "qgis.PyQt.QtNetwork", "qgis.server",
)
LIVE_ATTRS = frozenset({
    "mapCanvas", "activeLayer", "layerTreeRoot", "layerTreeRegistryBridge", "messageBar", "mainWindow",
    "layoutManager", "mapThemeCollection", "selectedFeatures", "selectedFeatureIds", "selectedFeatureCount",
    "addMapLayer", "addMapLayers", "removeMapLayer", "removeMapLayers",
    "startEditing", "commitChanges", "rollBack", "triggerRepaint", "addFeature", "addFeatures", "deleteFeature",
    "deleteFeatures", "deleteSelectedFeatures", "changeAttributeValue", "changeAttributeValues",
    "changeGeometry", "changeGeometryValues", "changeFeatures", "addAttribute", "addAttributes",
    "deleteAttribute", "deleteAttributes", "renameAttribute", "renameAttributes", "truncate",
    "createSpatialIndex", "createAttributeIndex", "writeBlock", "writeAsVectorFormat", "writeAsVectorFormatV2",
    "writeAsVectorFormatV3", "saveNamedStyle", "saveDefaultStyle", "exportNamedStyle", "writeEntry", "readEntry",
    "customVariables", "setCustomVariables", "snappingConfig", "relationManager", "auxiliaryStorage",
    "annotationManager", "bookmarkManager",
})
_FILE_WRITE_ATTRS = frozenset({"copy", "copy2", "copyfile", "copytree", "move", "makedirs", "mkdir", "rename",
                               "replace", "remove", "unlink", "rmdir", "removedirs", "rmtree", "truncate", "link",
                               "symlink", "chmod"})


_PATH_WRITE_ATTRS = frozenset({"write_text", "write_bytes", "touch", "symlink_to", "hardlink_to"})

_OPEN_MODE = re.compile(r"[rwaxbtU+]{1,4}")
_PROJECT_WRITE_ATTRS = frozenset({"write", "read", "clear", "removeAllMapLayers"})
_OPEN_WRITE_MARKERS = ("w", "a", "x", "+")


class _LiveFinder(ast.NodeVisitor):
    """Walks the whole tree and stops at the first reason the snippet stays live."""

    def __init__(self, changing=None) -> None:
        self.reason = ""
        self._changing = changing or ()

    def visit(self, node: ast.AST) -> None:
        if not self.reason:
            super().visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802 - ast visitor naming
        if node.id in LIVE_NAMES or node.id.startswith(LIVE_NAME_PREFIXES):
            self.reason = f"uses {node.id}"
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            if _module_is_live(alias.name):
                self.reason = f"imports {alias.name}"
                return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        if _module_is_live(module):
            self.reason = f"imports {module}"
            return
        for alias in node.names:
            if alias.name in LIVE_NAMES or alias.name.startswith(LIVE_NAME_PREFIXES):
                self.reason = f"uses {alias.name}"
                return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        attr = node.attr
        if attr in self._changing:
            self.reason = f"calls .{attr}, which changes the project"
            return
        if attr in LIVE_ATTRS:
            self.reason = f"calls .{attr}"
            return
        if isinstance(node.value, ast.Name) and node.value.id in ("shutil", "os") and attr in _FILE_WRITE_ATTRS:
            self.reason = f"writes files with {node.value.id}.{attr}"
            return
        if attr in _PATH_WRITE_ATTRS:
            self.reason = f"writes files with .{attr}"
            return
        if attr in _PROJECT_WRITE_ATTRS and "project" in _source_text(node.value).lower():
            self.reason = f"calls project.{attr}"
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Attribute) and node.func.attr == "open":
            modes = list(node.args[:2]) + [keyword.value for keyword in node.keywords if keyword.arg == "mode"]
            for mode in modes:
                if (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                        and _OPEN_MODE.fullmatch(mode.value)
                        and any(marker in mode.value for marker in _OPEN_WRITE_MARKERS)):
                    self.reason = "opens a file for writing"
                    return
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            mode = None
            if len(node.args) > 1:
                mode = node.args[1]
            else:
                for keyword in node.keywords:
                    if keyword.arg == "mode":
                        mode = keyword.value
                        break
            if mode is not None:
                text = mode.value if isinstance(mode, ast.Constant) and isinstance(mode.value, str) else None
                if text is None or any(marker in text for marker in _OPEN_WRITE_MARKERS):
                    self.reason = "opens a file for writing"
                    return
        self.generic_visit(node)


def _module_is_live(module: str) -> bool:
    return any(module == name or module.startswith(name + ".") for name in LIVE_MODULES)


def _source_text(node: ast.AST) -> str:
    """The source of one node, or "" when this Python cannot rebuild it."""
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - an unreadable value simply matches nothing
        return ""


def live_reason(code: str, changing=None) -> str:
    """The reason this snippet must stay in QGIS, or "" when it may leave."""






    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return "does not parse"
    finder = _LiveFinder(changing=changing)
    finder.visit(tree)
    return finder.reason


class _QgisFinder(ast.NodeVisitor):
    """Walks the whole tree and stops at the first QGIS name or import."""

    def __init__(self) -> None:
        self.needs = False

    def visit(self, node: ast.AST) -> None:
        if not self.needs:
            super().visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        name = node.id


        if name in ("project", "NULL") or name.startswith("Qgs") \
                or (len(name) > 1 and name[0] == "Q" and name[1].isupper()):
            self.needs = True
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            if alias.name.startswith(("qgis", "PyQt")):
                self.needs = True
                return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if (node.module or "").startswith(("qgis", "PyQt")):
            self.needs = True
            return
        self.generic_visit(node)


def needs_qgis(code: str) -> bool:
    """True when the snippet uses QGIS at all; the child then starts a QGIS app."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return True
    finder = _QgisFinder()
    finder.visit(tree)
    return finder.needs


class _LayersFinder(ast.NodeVisitor):
    """Walks the whole tree and stops at the first layer reached by name."""

    def __init__(self) -> None:
        self.lists = False

    def visit(self, node: ast.AST) -> None:
        if not self.lists:
            super().visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr == "mapLayers":
            self.lists = True
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Attribute) and node.func.attr in (
                "mapLayer", "mapLayersByName", "mapLayersByShortName"):
            named = bool(node.args) and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str)
            if not named:
                self.lists = True
                return
        self.generic_visit(node)


def lists_layers(code: str) -> bool:
    """True when the snippet reaches layers it does not name, so all are copied."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return False
    finder = _LayersFinder()
    finder.visit(tree)
    return finder.lists




QUIET_CALLS = frozenset({
    "instance", "mapLayer", "mapLayers", "mapLayersByName", "mapLayersByShortName", "fields", "crs", "extent",
    "featureCount", "name", "id", "source", "providerType", "dataProvider", "fileName", "homePath", "ellipsoid",
    "indexOf", "lookupField", "isValid", "geometryType", "wkbType", "customProperty",
})
_PROJECT_ROOTS = frozenset({"project", "QgsProject"})


def quiet_before(code: str, line: int) -> bool:
    """True when nothing with an effect can have run before the trapped call."""










    if line <= 0:
        return False
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return False
    rooted = set(_PROJECT_ROOTS)
    for statement in tree.body:
        if _last_line(statement) < line:
            if not _quiet_statement(statement, rooted):
                return False
            continue
        holding = [other for other in tree.body if other.lineno <= line <= _last_line(other)]
        return holding == [statement] and _plain_trapped_call(statement, rooted)
    return False


def _last_line(statement: ast.stmt) -> int:
    return getattr(statement, "end_lineno", None) or statement.lineno


def _assigned_names(targets) -> list[str] | None:
    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)) and all(isinstance(item, ast.Name) for item in target.elts):
            names.extend(item.id for item in target.elts)
        else:
            return None
    return names


def _root_name(node: ast.AST) -> str:
    """The name an attribute, subscript and call chain starts from, or ""."""
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.func if isinstance(node, ast.Call) else node.value
    return node.id if isinstance(node, ast.Name) else ""


def _quiet_value(node: ast.AST) -> bool:
    if isinstance(node, (ast.Constant, ast.Name)):
        return True
    if isinstance(node, ast.Attribute):
        return _quiet_value(node.value)
    if isinstance(node, ast.Subscript):
        return _quiet_value(node.value) and _quiet_value(node.slice)
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_quiet_value(item) for item in node.elts)
    if isinstance(node, ast.UnaryOp):
        return _quiet_value(node.operand)
    if isinstance(node, ast.BinOp):
        return _quiet_value(node.left) and _quiet_value(node.right)
    if isinstance(node, ast.Call):
        return (isinstance(node.func, ast.Attribute) and node.func.attr in QUIET_CALLS
                and _quiet_value(node.func.value) and _plain_arguments(node))
    return False


def _plain_arguments(call: ast.Call) -> bool:
    return (all(_quiet_value(argument) for argument in call.args)
            and all(keyword.arg is not None and _quiet_value(keyword.value) for keyword in call.keywords))


def _quiet_statement(statement: ast.stmt, rooted: set) -> bool:
    """True for a statement that changes nothing; tracks which names hold project objects."""
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        for alias in statement.names:
            bound = (alias.asname or alias.name).split(".")[0]


            if isinstance(statement, ast.ImportFrom) and statement.module == "qgis.core" \
                    and alias.name == "QgsProject":
                rooted.add(bound)
            else:
                rooted.discard(bound)
        return True
    if isinstance(statement, ast.Pass):
        return True
    if isinstance(statement, ast.Expr):
        return isinstance(statement.value, ast.Constant)
    if isinstance(statement, (ast.Assign, ast.AnnAssign)):
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        names = _assigned_names(targets)
        if names is None:
            return False
        if statement.value is None:
            return True
        if not _quiet_value(statement.value):
            return False
        if _root_name(statement.value) in rooted:
            rooted.update(names)
        else:
            rooted.difference_update(names)
        return True
    return False


def _plain_trapped_call(statement: ast.stmt, rooted: set) -> bool:
    if not (isinstance(statement, ast.Expr)
            or (isinstance(statement, ast.Assign) and _assigned_names(statement.targets) is not None)):
        return False
    value = statement.value
    return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute)
            and _root_name(value.func) in rooted and _quiet_value(value.func.value) and _plain_arguments(value))

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import ast
import functools
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

    "iface", "processing", "qgis", "edit", "canvas", "active", "tools",
    "QgsApplication", "QgsMapCanvas", "QgsMessageBar", "QgsGui",
    "QgsTask", "QgsTaskManager", "QgsExpressionContextUtils",
    "QgsMapSettings", "QgsMapRendererParallelJob", "QgsMapRendererSequentialJob",
    "QgsMapRendererCustomPainterJob", "QImage", "QPainter", "QFont", "QBrush", "QPen", "QTransform",
    "QPixmap", "QIcon", "QWidget", "QDialog", "QMessageBox", "QApplication", "QCoreApplication", "QTimer",
    "QThread", "QEventLoop",
})

LAYER_HELPERS = frozenset({"layer", "layers"})
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
    "createSpatialIndex", "createAttributeIndex", "writeBlock", "deleteShapeFile",
    "saveNamedStyle", "saveDefaultStyle", "exportNamedStyle", "writeEntry", "readEntry",
    "customVariables", "setCustomVariables", "snappingConfig", "relationManager", "auxiliaryStorage",
    "annotationManager", "bookmarkManager",
})
_FILE_WRITE_ATTRS = frozenset({"copy", "copy2", "copyfile", "copytree", "move", "makedirs", "mkdir", "rename",
                               "replace", "remove", "unlink", "rmdir", "removedirs", "rmtree", "truncate", "link",
                               "symlink", "chmod"})








_PATH_WRITE_ATTRS = frozenset({"touch", "symlink_to", "hardlink_to", "mkdir",
                               "extract", "extractall", "writestr", "mkdtemp"})
_PROJECT_WRITE_ATTRS = frozenset({"write", "read", "clear", "removeAllMapLayers"})


class _LiveFinder(ast.NodeVisitor):


    def __init__(self, changing=None) -> None:
        self.reason = ""
        self._changing = changing or ()
        self._names, self._modules, self._attrs = _live_tables()

    def visit(self, node: ast.AST) -> None:
        if not self.reason:
            super().visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if node.id in self._names or node.id.startswith(LIVE_NAME_PREFIXES):
            self.reason = f"uses {node.id}"
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            if _module_is_live(alias.name, self._modules):
                self.reason = f"imports {alias.name}"
                return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        module = node.module or ""
        if _module_is_live(module, self._modules):
            self.reason = f"imports {module}"
            return
        for alias in node.names:
            if alias.name in _PATH_WRITE_ATTRS:
                self.reason = f"writes files with {alias.name}"
                return
            if alias.name in self._names or alias.name.startswith(LIVE_NAME_PREFIXES):
                self.reason = f"uses {alias.name}"
                return
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        attr = node.attr
        if attr in self._changing:
            self.reason = f"calls .{attr}, which changes the project"
            return
        if attr in self._attrs:
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
        func = node.func
        callee = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if callee == "ZipFile":
            modes = list(node.args[1:2]) + [keyword.value for keyword in node.keywords if keyword.arg == "mode"]
            for mode in modes:
                if not (isinstance(mode, ast.Constant) and mode.value == "r"):
                    self.reason = "writes a zip archive"
                    return
        self.generic_visit(node)


@functools.lru_cache(maxsize=4)
def _parsed(code: str) -> ast.AST:







    return ast.parse(code or "")


def _module_is_live(module: str, modules=LIVE_MODULES) -> bool:
    return any(module == name or module.startswith(name + ".") for name in modules)


def _live_tables() -> tuple:




    try:
        from . import tuning
    except ImportError:
        return LIVE_NAMES, LIVE_MODULES, LIVE_ATTRS
    return (tuning.names("live_names", LIVE_NAMES), tuning.names("live_modules", LIVE_MODULES),
            tuning.names("live_attrs", LIVE_ATTRS))


def _source_text(node: ast.AST) -> str:

    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001
        return ""


def live_reason(code: str, changing=None) -> str:







    try:
        tree = _parsed(code or "")
    except SyntaxError:
        return "does not parse"
    finder = _LiveFinder(changing=changing)
    finder.visit(tree)
    return finder.reason


class _QgisFinder(ast.NodeVisitor):


    def __init__(self) -> None:
        self.needs = False

    def visit(self, node: ast.AST) -> None:
        if not self.needs:
            super().visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        identifier = node.id


        if identifier in ("project", "NULL", "Qgis") or identifier in LAYER_HELPERS or identifier.startswith("Qgs") \
                or (len(identifier) > 1 and identifier[0] == "Q" and identifier[1].isupper()):
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

    try:
        tree = _parsed(code or "")
    except SyntaxError:
        return True
    finder = _QgisFinder()
    finder.visit(tree)
    return finder.needs


def names_read(code: str) -> frozenset[str]:












    try:
        tree = _parsed(code or "")
    except SyntaxError:
        return frozenset()
    reader = _NamesRead()
    reader.block(tree.body, set())
    return frozenset(reader.needed)


def names_mentioned(code: str) -> frozenset[str]:

    try:
        tree = _parsed(code or "")
    except SyntaxError:
        return frozenset()
    return frozenset(node.id for node in ast.walk(tree) if isinstance(node, ast.Name))


def _targets(node: ast.AST) -> set[str]:

    return {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store)}


_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.ListComp, ast.SetComp,
           ast.DictComp, ast.GeneratorExp)


class _NamesRead:


    def __init__(self) -> None:
        self.needed: set[str] = set()

    def loads(self, node: ast.AST, bound: set[str]) -> None:

        if node is None:
            return
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, (ast.Load, ast.Del)) and node.id not in bound:
                self.needed.add(node.id)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            inner = set(bound)
            for generator in node.generators:
                self.loads(generator.iter, inner)
                self._store_target(generator.target, inner)
                for condition in generator.ifs:
                    self.loads(condition, inner)
            for part in ((node.key, node.value) if isinstance(node, ast.DictComp) else (node.elt,)):
                self.loads(part, inner)
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):


            if not isinstance(node, ast.Lambda):
                for decorator in node.decorator_list:
                    self.loads(decorator, bound)
            for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
                self.loads(default, bound)
            local = _scope_locals(node)
            for sub in _scope_nodes(node):
                if (isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Load, ast.Del))
                        and sub.id not in local and sub.id not in bound):
                    self.needed.add(sub.id)
            return
        if isinstance(node, ast.ClassDef):


            for part in list(node.decorator_list) + list(node.bases) + [keyword.value for keyword in node.keywords]:
                self.loads(part, bound)
            inner = set(bound)
            for statement in node.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.loads(statement, bound)
                    inner.add(statement.name)
                else:
                    self.statement(statement, inner)
            return
        for child in ast.iter_child_nodes(node):
            self.loads(child, bound)

    def block(self, body: list, bound: set[str]) -> set[str]:

        for statement in body:
            self.statement(statement, bound)
        return bound

    def statement(self, node: ast.stmt, bound: set[str]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            self.loads(node, bound)
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Assign):
            self.loads(node.value, bound)
            for target in node.targets:
                self._store_target(target, bound)
        elif isinstance(node, ast.AugAssign):
            self.loads(node.value, bound)
            self.loads(node.target if not isinstance(node.target, ast.Name)
                       else ast.Name(id=node.target.id, ctx=ast.Load()), bound)
            self._store_target(node.target, bound)
        elif isinstance(node, ast.AnnAssign):
            self.loads(node.value, bound)
            if node.value is not None:
                self._store_target(node.target, bound)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self.loads(node.iter, bound)
            inner = set(bound)
            self._store_target(node.target, inner)
            self.block(node.body, inner)
            self.block(node.orelse, set(bound))
        elif isinstance(node, ast.While):
            self.loads(node.test, bound)
            self.block(node.body, set(bound))
            self.block(node.orelse, set(bound))
        elif isinstance(node, ast.If):
            self.loads(node.test, bound)
            then = self.block(node.body, set(bound))
            other = self.block(node.orelse, set(bound))
            if node.orelse:
                bound |= then & other
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                self.loads(item.context_expr, bound)
                if item.optional_vars is not None:
                    self._store_target(item.optional_vars, bound)

            self.block(node.body, set(bound))
        elif isinstance(node, ast.Try) or type(node).__name__ == "TryStar":
            self.block(node.body, set(bound))
            for handler in node.handlers:
                self.loads(handler.type, bound)
                inner = set(bound) | ({handler.name} if handler.name else set())
                self.block(handler.body, inner)
            self.block(node.orelse, set(bound))
            self.block(node.finalbody, bound)
        else:

            self.loads(node, bound)

    def _store_target(self, target: ast.AST, bound: set[str]) -> None:

        for sub in ast.walk(target):
            if isinstance(sub, (ast.Attribute, ast.Subscript)):
                self.loads(sub.value, bound)
                if isinstance(sub, ast.Subscript):
                    self.loads(sub.slice, bound)
        bound |= _targets(target)


def _scope_nodes(scope: ast.AST):

    body = scope.body if isinstance(scope.body, list) else [scope.body]
    for statement in body:
        yield from ast.walk(statement)


def _own_nodes(scope: ast.AST):

    body = scope.body if isinstance(scope.body, list) else [scope.body]
    pending = list(body)
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, _SCOPES):
            continue
        pending.extend(ast.iter_child_nodes(node))


def _scope_locals(node: ast.AST) -> set[str]:

    out: set[str] = set()
    args = node.args
    for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        out.add(arg.arg)
    for arg in (args.vararg, args.kwarg):
        if arg is not None:
            out.add(arg.arg)
    declared = set()
    for sub in _own_nodes(node):
        if isinstance(sub, (ast.Global, ast.Nonlocal)):
            declared.update(sub.names)
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
            out.add(sub.id)
        elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(sub.name)
        elif isinstance(sub, (ast.Import, ast.ImportFrom)):
            out.update((alias.asname or alias.name).split(".")[0] for alias in sub.names)
        elif isinstance(sub, ast.ExceptHandler) and sub.name:
            out.add(sub.name)
    return out - declared


class _LayersFinder(ast.NodeVisitor):













    def __init__(self, helpers=LAYER_HELPERS) -> None:
        self.lists = False
        self.helpers = frozenset(helpers)
        self.named: set[str] = set()

    def visit(self, node: ast.AST) -> None:
        if not self.lists:
            super().visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
        if node.attr == "mapLayers":
            self.lists = True
            return
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802

        if node.id in LAYER_HELPERS and node.id in self.helpers:
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
        if (isinstance(node.func, ast.Name) and node.func.id == "layer" and "layer" in self.helpers
                and len(node.args) == 1 and not node.keywords and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            self.named.add(node.args[0].value)
            return
        self.generic_visit(node)


def _layers_finder(code: str) -> _LayersFinder | None:
    try:
        tree = _parsed(code or "")
    except SyntaxError:
        return None
    finder = _LayersFinder(LAYER_HELPERS & names_read(code))
    finder.visit(tree)
    return finder


def lists_layers(code: str) -> bool:

    finder = _layers_finder(code)
    return bool(finder and finder.lists)


def helper_layer_names(code: str) -> frozenset[str]:

    finder = _layers_finder(code)
    return frozenset(finder.named) if finder and not finder.lists else frozenset()




QUIET_CALLS = frozenset({
    "instance", "mapLayer", "mapLayers", "mapLayersByName", "mapLayersByShortName", "fields", "crs", "extent",
    "featureCount", "name", "id", "source", "providerType", "dataProvider", "fileName", "homePath", "ellipsoid",
    "indexOf", "lookupField", "isValid", "geometryType", "wkbType", "customProperty",
})
_PROJECT_ROOTS = frozenset({"project", "QgsProject"})


def quiet_before(code: str, line: int) -> bool:











    if line <= 0:
        return False
    try:
        tree = _parsed(code or "")
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

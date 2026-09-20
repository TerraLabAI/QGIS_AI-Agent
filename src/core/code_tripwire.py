# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later













































from __future__ import annotations

import os
import sys
import threading

from . import code_effects as ce
from . import security

_STATE_ATTR = "_terralab_code_tripwire"


class NeedsPermission(BaseException):


    def __init__(self, cls: str, reason: str):
        super().__init__(reason)
        self.cls = cls
        self.reason = reason


class Refused(BaseException):


    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class _State:


    def __init__(self) -> None:
        self.handler = None
        self.thread = 0
        self.installed = False


def _state() -> _State:
    state = getattr(sys, _STATE_ATTR, None)
    if state is None:
        state = _State()
        setattr(sys, _STATE_ATTR, state)
    return state


def enabled() -> bool:





    from . import tuning

    return tuning.flag("execute_code", "tripwire_enabled", True)


def install() -> bool:

    state = _state()
    if state.installed:
        return True

    def hook(event, args, _state=state):
        handler = _state.handler
        if handler is not None and threading.get_ident() == _state.thread:
            handler(event, args)

    try:
        sys.addaudithook(hook)
    except Exception:  # noqa: BLE001
        return False
    state.installed = True
    return True




def _plugin_dirs() -> tuple[str, ...]:

    dirs = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))]


    if "qgis.core" in sys.modules:
        try:
            from qgis.core import QgsApplication

            dirs.append(os.path.join(QgsApplication.qgisSettingsDirPath(), "python", "plugins"))
        except Exception:  # nosec B110
            pass
    out = []
    for folder in dirs:
        for spelling in (os.path.abspath(folder), os.path.realpath(folder)):
            out.append(os.path.normcase(spelling).rstrip(os.sep) + os.sep)
    return tuple(dict.fromkeys(out))


_PLUGINS_DIRS: list = []

_ACTING_FOR_SNIPPET = frozenset({"code_guard.py", "code_guard_files.py", "code_tripwire.py"})


def _from_snippet(depth: int = 2) -> bool:







    try:
        frame = sys._getframe(depth)
    except ValueError:
        return False
    while frame is not None:
        name = frame.f_code.co_filename
        if name == "<snippet>":
            return True
        if not name.startswith("<") and os.path.basename(name) not in _ACTING_FOR_SNIPPET:
            if not _PLUGINS_DIRS:
                _PLUGINS_DIRS.append(_plugin_dirs())
            if os.path.normcase(os.path.abspath(name)).startswith(_PLUGINS_DIRS[0]):
                return False
        frame = frame.f_back
    return False



_SNIPPET_FILES = frozenset({"<snippet>"})


def _snippet_origin(depth: int = 2) -> str:






    try:
        frame = sys._getframe(depth)
    except ValueError:
        return ""
    importing = False
    while frame is not None:
        name = frame.f_code.co_filename
        if name in _SNIPPET_FILES:
            return "import" if importing else "snippet"
        if name.startswith("<frozen importlib") or name.startswith("<frozen zipimport"):
            importing = True
        elif not name.startswith("<") and os.path.basename(name) not in _ACTING_FOR_SNIPPET:
            if not _PLUGINS_DIRS:
                _PLUGINS_DIRS.append(_plugin_dirs())
            if os.path.normcase(os.path.abspath(name)).startswith(_PLUGINS_DIRS[0]):
                return ""
        frame = frame.f_back
    return ""


_SOFTWARE: dict = {"key": None, "roots": ()}


def _software_roots() -> tuple:






    key = tuple(sys.path)
    if _SOFTWARE["key"] == key:
        return _SOFTWARE["roots"]
    roots = [sys.prefix, sys.base_prefix, sys.exec_prefix] + [p for p in sys.path if p]
    if "qgis.core" in sys.modules:
        try:
            from qgis.core import QgsApplication

            roots += [QgsApplication.prefixPath(), QgsApplication.pkgDataPath()]
        except Exception:  # nosec B110
            pass
    if not _PLUGINS_DIRS:
        _PLUGINS_DIRS.append(_plugin_dirs())
    roots += list(_PLUGINS_DIRS[0])
    out = []
    for root in roots:
        if not isinstance(root, str) or not root.strip():
            continue
        full = os.path.abspath(root)
        if not os.path.isdir(full) or security._too_wide(full):
            continue
        out.extend((os.path.normpath(full), os.path.realpath(full)))
    _SOFTWARE.update(key=key, roots=tuple(dict.fromkeys(out)))
    return _SOFTWARE["roots"]


def _under_software(full: str) -> bool:
    return any(security._under(spelling, root) for root in _software_roots()
               for spelling in dict.fromkeys((full, os.path.realpath(full))))




def file_class(path, creating: bool = True) -> tuple[str, str]:

    try:
        text = os.fspath(path)
    except TypeError:
        return ce.FW, "writes a file"
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = str(text).split("|", 1)[0].strip()
    if not text or text.startswith(("memory:", "TEMPORARY_OUTPUT", "/vsimem/")):
        return ce.P, ""
    full = os.path.abspath(os.path.expanduser(text))
    shown = os.path.basename(full) or full


    if any(security._under(spelling, root) for root in security.temp_roots()
           for spelling in dict.fromkeys((full, os.path.realpath(full)))):
        return ce.P, ""
    if os.path.exists(full):
        return ce.FW, f"replaces the file {shown}"
    roots = [security.project_dir(), security._output_root] + list(security._allowed_roots)
    if creating and any(security._under(full, root) for root in roots if root):
        return ce.FP, f"writes the new file {shown}"
    return ce.FW, f"writes {full}"


def _remote(source) -> bool:
    text = str(source or "").strip()
    head = text.split("|", 1)[0]
    lowered = text.lower()

    return (("://" in head and not head.lower().startswith("file://")) or security.unwrap_vsi(head)[0] == "remote"
            or "url=http" in lowered or "url=ftp" in lowered)


def _source_file(source, provider: str = "") -> str:

    text = str(source or "").strip()
    try:
        from qgis.core import QgsProviderRegistry

        if provider:
            decoded = str(QgsProviderRegistry.instance().decodeUri(provider, text).get("path") or "")
            if decoded:
                text = decoded
    except Exception:  # nosec B110
        pass
    return security.local_part(text.split("|", 1)[0].split("?", 1)[0] if text.lower().startswith("file:")
                               else text.split("|", 1)[0])


def _outside_scope(source, provider: str = "") -> str:

    path = _source_file(source, provider)
    if not path or not (os.path.isabs(os.path.expanduser(path)) or path.startswith("~")):
        return ""
    return security.validate_read(path) or ""




class _Arm:
    def __init__(self, granted: str) -> None:
        self.granted = granted
        self.trip: BaseException | None = None
        self.patched: list = []
        self.sources: dict = {}
        self.judging = False

    def need(self, cls: str, reason: str) -> None:
        if ce.RANK.get(cls, ce.RANK[ce.ASK]) <= ce.RANK.get(self.granted, 0):
            return
        exc = NeedsPermission(cls, reason)
        if self.trip is None:
            self.trip = exc
        raise exc

    def refuse(self, reason: str) -> None:
        exc = Refused(reason)
        if self.trip is None:
            self.trip = exc
        raise exc


    def on_event(self, event: str, args) -> None:




        if self.judging:
            return
        self.judging = True
        try:
            self._on_event(event, args)
        finally:
            self.judging = False

    def _on_event(self, event: str, args) -> None:
        kind = _event_kind(event)
        if not kind:
            return
        if args and "__pycache__" in str(args[0]):

            return
        if event == "open":
            path, mode, flags = (tuple(args) + (None, None, None))[:3]
            if isinstance(path, int) or path is None:
                return
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND))
            if not writing:
                self.judge_read(path, "reads")
                return
            text = str(path)
            if not _from_snippet(3):
                return
            cls, reason = file_class(text)
            self.need(cls, reason)
        elif event in _DELETE_EVENTS:
            if _from_snippet(3):
                self.need(ce.D, f"deletes {os.path.basename(str(args[0])) if args else 'a file'}")
        elif event in _COPY_EVENTS:
            if _from_snippet(3):
                self.need(*file_class(args[1] if len(args) > 1 else ""))
        elif event in _MOVE_EVENTS:
            if _from_snippet(3):
                shown = os.path.basename(str(args[0])) if args else "a file"
                self.need(ce.FW, f"moves or changes {shown}")
        elif event == "shutil.make_archive" or event in _MKDIR_EVENTS:
            if _from_snippet(3):
                self.need(*file_class(args[0] if args else ""))
        elif event in _LISTING_EVENTS:
            self.judge_read(args[0] if args else ".", "lists")
        elif kind == "network":
            if _from_snippet(3):
                self.refuse("reaches the network; code reads layers and local files, and the fetch "
                            "tools (add_data, fetch_osm_data) carry the address card")
        elif kind == "process":
            if _from_snippet(3):
                self.refuse("starts a program or loads native code, which code may not do")
        elif kind == "setting":
            if _from_snippet(3):
                self.refuse("changes a system setting, which code may not do")

    def judge_read(self, path, verb: str) -> None:

        try:
            text = os.fsdecode(path)
        except TypeError:
            return
        if not text or text.startswith(("<", "memory:")):
            return
        full = os.path.abspath(os.path.expanduser(text))
        if _under_software(full) or _snippet_origin(4) != "snippet":
            return
        reason = security.validate_read(full)
        if reason:
            what = "a folder" if verb == "lists" else "a file"
            self.refuse(f"{verb} {what} outside the read scope: {reason}")


    def patch(self, traps=None) -> None:
        for base, name, judge, static in (_traps() if traps is None else traps):
            if base is None or not hasattr(base, name):
                continue
            for owner in _with_overrides(base, name):
                self._patch_one(owner, name, judge, static)

    def _patch_one(self, owner, name, judge, static) -> None:
        own = name in owner.__dict__
        raw = owner.__dict__.get(name) if own else None

        original = next((c.__dict__[name] for c in owner.__mro__ if name in c.__dict__), None)
        if original is None:
            return
        if static:
            original = getattr(owner, name)
        wrapper = _wrap(self, original, judge, static)
        try:
            setattr(owner, name, staticmethod(wrapper) if static else wrapper)
        except (AttributeError, TypeError):
            return
        self.patched.append((owner, name, own, raw))

    def unpatch(self) -> None:
        while self.patched:
            owner, name, own, raw = self.patched.pop()
            try:
                if own:
                    setattr(owner, name, raw)
                else:
                    delattr(owner, name)
            except (AttributeError, TypeError):  # nosec B110
                from .logger import log_warning

                log_warning(f"code tripwire: {owner.__name__}.{name} was not put back")


def _with_overrides(base, name: str) -> list:

    out, stack, seen = [base], list(base.__subclasses__()), {base}
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        if name in cls.__dict__:
            out.append(cls)
        stack.extend(cls.__subclasses__())
    return out


def _wrap(arm: _Arm, original, judge, static: bool):
    def trapped(*args, **kwargs):
        if _from_snippet(2):
            judge(arm, *args, **kwargs)
        if static or not args or not hasattr(original, "__get__"):
            return original(*args, **kwargs)



        return original.__get__(args[0], type(args[0]))(*args[1:], **kwargs)

    trapped.__name__ = getattr(original, "__name__", "trapped")
    trapped.__doc__ = getattr(original, "__doc__", None)
    return trapped


_DELETE_EVENTS = frozenset({"os.remove", "os.rmdir", "shutil.rmtree", "os.truncate"})
_COPY_EVENTS = frozenset({"shutil.copyfile", "shutil.copytree"})
_MOVE_EVENTS = frozenset({"os.rename", "shutil.move", "os.link", "os.symlink", "os.chmod", "os.chown",
                          "os.utime"})
_MKDIR_EVENTS = frozenset({"os.mkdir"})

_LISTING_EVENTS = frozenset({"os.listdir", "os.scandir", "glob.glob", "glob.glob/2", "pathlib.Path.glob",
                             "pathlib.Path.rglob"})

_NETWORK_MODULES = frozenset({"socket", "urllib", "http", "ftplib", "smtplib", "webbrowser", "imaplib", "poplib",
                              "nntplib", "telnetlib"})
_PROCESS_MODULES = frozenset({"subprocess", "ctypes", "pty", "_posixsubprocess"})

_PROCESS_EVENTS = frozenset({"os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile",
                             "os.kill", "os.add_dll_directory", "sys.addaudithook",
                             "sys.setprofile", "sys.settrace", "cpython.run_command", "cpython.run_file",
                             "cpython.run_module", "cpython.run_startup", "cpython.run_stdin"})
_SETTINGS_EVENTS = frozenset({"os.putenv", "os.unsetenv"})
_FILE_EVENTS = (frozenset({"open", "shutil.make_archive"}) | _DELETE_EVENTS | _COPY_EVENTS | _MOVE_EVENTS
                | _MKDIR_EVENTS | _LISTING_EVENTS)


def _served_refusals() -> dict:

    try:
        from . import tuning
    except ImportError:
        return {}
    return tuning.service_doc("code_classes") or {}


def _event_kind(event: str) -> str:

    module = event.split(".", 1)[0]
    served = _served_refusals()
    if module in _NETWORK_MODULES or module in served.get("network_modules", ()):
        return "network"
    if module in _PROCESS_MODULES or module in served.get("process_modules", ()) or event in _PROCESS_EVENTS:
        return "process"
    if module == "winreg" or event in _SETTINGS_EVENTS:
        return "setting"
    return "file" if event in _FILE_EVENTS else ""




def _layer_name(layer) -> str:
    try:
        return layer.name()
    except Exception:  # noqa: BLE001
        return "a layer"


def _holds_memory(layer) -> bool:
    try:
        if layer.isModified():
            return True
    except Exception:  # nosec B110
        pass
    try:
        return layer.providerType() == "memory"
    except Exception:  # noqa: BLE001
        return False


def _file_of(layer) -> str:
    try:
        return os.path.basename(str(layer.source()).split("|", 1)[0]) or _layer_name(layer)
    except Exception:  # noqa: BLE001
        return _layer_name(layer)


def _commit(arm, layer, *args, **kwargs):
    if layer.providerType() == "memory":
        return
    buffer = layer.editBuffer()
    deletes = bool(buffer is not None and (buffer.deletedFeatureIds() or buffer.deletedAttributeIds()))
    if deletes:
        arm.need(ce.D, f"deletes features of '{_layer_name(layer)}' from {_file_of(layer)}")
    arm.need(ce.FW, f"saves the edit to '{_layer_name(layer)}' into {_file_of(layer)}")


def _provider_write(deletes: bool):
    def judge(arm, provider, *args, **kwargs):
        try:
            if provider.name() == "memory":
                return
        except Exception:  # nosec B110
            pass
        try:
            shown = os.path.basename(str(provider.dataSourceUri()).split("|", 1)[0])
        except Exception:  # noqa: BLE001
            shown = "its source"
        if deletes:
            arm.need(ce.D, f"deletes from {shown}")
        arm.need(ce.FW, f"writes into {shown}")
    return judge


def _layers_in(project, value) -> list:
    items = value if isinstance(value, (list, tuple, set)) else [value]
    out = []
    for item in items:
        layer = project.mapLayer(item) if isinstance(item, str) else item
        if layer is not None:
            out.append(layer)
    return out


def _open_project(project) -> bool:







    try:
        from qgis.core import QgsProject

        return project is QgsProject.instance()
    except Exception:  # noqa: BLE001
        return True


def _remove(arm, project, value=None, *args, **kwargs):
    if not _open_project(project):
        return
    for layer in _layers_in(project, value):
        if _holds_memory(layer):
            arm.need(ce.D, f"removes '{_layer_name(layer)}', whose data lives only in this project")


def _remove_all(arm, project, *args, **kwargs):
    if not _open_project(project):
        return
    arm.need(ce.D, "removes every layer of the project")


def _project_write(arm, project, *args, **kwargs):
    path = args[0] if args and isinstance(args[0], str) else project.fileName()
    if not path:
        arm.need(ce.FW, "saves the project")
    arm.need(*file_class(path))


def _project_read(arm, project, *args, **kwargs):
    if not _open_project(project):

        path = args[0] if args and isinstance(args[0], str) else ""
        reason = _outside_scope(path) if path else ""
        if reason:
            arm.refuse(f"reads a project from outside the read scope: {reason}")
        return
    arm.need(ce.D, "replaces the open project")


def _add_layers(arm, project, value=None, *args, **kwargs):
    for layer in _layers_in(project, value):
        try:
            source, provider = layer.source(), layer.providerType()
        except (AttributeError, RuntimeError, TypeError):
            continue
        if _remote(source):
            arm.refuse("adds a layer read from the network; add_data carries the address card")


        reason = _outside_scope(source, provider)
        if reason:
            arm.refuse(f"adds a layer from outside the read scope: {reason}")


def _add_by_uri(arm, iface, uri=None, *args, **kwargs):
    if _remote(uri):
        arm.refuse("adds a layer read from the network; add_data carries the address card")
    provider = args[1] if len(args) > 1 and isinstance(args[1], str) else str(kwargs.get("providerKey") or "")
    reason = _outside_scope(uri, provider)
    if reason:
        arm.refuse(f"adds a layer from outside the read scope: {reason}")


def _layer_read(arm, layer, *args, **kwargs):

    try:
        source, provider = layer.source(), layer.providerType()
    except (AttributeError, RuntimeError, TypeError):
        return
    _judge_source(arm, source, provider)


def _provider_read(arm, provider, *args, **kwargs):
    try:
        source, name = provider.dataSourceUri(), provider.name()
    except (AttributeError, RuntimeError, TypeError):
        return
    _judge_source(arm, source, name)


def _judge_source(arm, source, provider: str) -> None:
    key = f"{provider}\n{source}"
    reason = arm.sources.get(key)
    if reason is None:
        reason = arm.sources[key] = _outside_scope(source, provider)
    if reason:
        arm.refuse(f"reads a layer from outside the read scope: {reason}")


def _path_arg(index: int):
    def judge(arm, *args, **kwargs):
        values = [a for a in args if isinstance(a, str)]
        target = args[index] if len(args) > index and isinstance(args[index], str) else (values[0] if values else "")
        arm.need(*file_class(target))
    return judge


def _deletes(reason: str):
    def judge(arm, *args, **kwargs):
        arm.need(ce.D, reason)
    return judge


def _refuses(reason: str):
    def judge(arm, *args, **kwargs):
        arm.refuse(reason)
    return judge


def _raster_write(arm, writer, *args, **kwargs):
    try:
        target = writer.outputUrl()
    except Exception:  # noqa: BLE001
        target = ""
    arm.need(*file_class(target))


_NETWORK = "reaches the network; code reads layers and local files, and the fetch tools carry the address card"
_SETTING = "changes a QGIS setting shared by every project, which code may not do"


def _traps():

    try:
        import qgis.core as core
    except ImportError:
        return []
    try:
        import qgis.gui as gui
    except ImportError:
        gui = None
    get = lambda name: getattr(core, name, None)  # noqa: E731
    out = [
        (get("QgsVectorLayer"), "commitChanges", _commit, False),
        (get("QgsProject"), "removeMapLayer", _remove, False),
        (get("QgsProject"), "removeMapLayers", _remove, False),
        (get("QgsProject"), "takeMapLayer", _remove, False),
        (get("QgsProject"), "removeAllMapLayers", _remove_all, False),
        (get("QgsProject"), "clear", _remove_all, False),
        (get("QgsProject"), "write", _project_write, False),
        (get("QgsProject"), "read", _project_read, False),
        (get("QgsProject"), "addMapLayer", _add_layers, False),
        (get("QgsProject"), "addMapLayers", _add_layers, False),
        (get("QgsVectorFileWriter"), "writeAsVectorFormat", _path_arg(1), True),
        (get("QgsVectorFileWriter"), "writeAsVectorFormatV2", _path_arg(1), True),
        (get("QgsVectorFileWriter"), "writeAsVectorFormatV3", _path_arg(1), True),
        (get("QgsVectorFileWriter"), "create", _path_arg(0), True),
        (get("QgsVectorFileWriter"), "deleteShapeFile", _deletes("deletes a shapefile"), True),
        (get("QgsRasterFileWriter"), "writeRaster", _raster_write, False),
        (get("QgsMapLayer"), "saveNamedStyle", _path_arg(1), False),
        (get("QgsMapLayer"), "saveSldStyle", _path_arg(1), False),
        (get("QgsMapLayer"), "saveDefaultStyle", _deletes("replaces the layer's default style file"), False),
        (get("QgsLayoutExporter"), "exportToImage", _path_arg(1), False),
        (get("QgsLayoutExporter"), "exportToPdf", _path_arg(1), False),
        (get("QgsLayoutExporter"), "exportToSvg", _path_arg(1), False),
        (get("QgsCoordinateReferenceSystem"), "saveAsUserCrs", _refuses(_SETTING), False),
        (get("QgsExpressionContextUtils"), "setGlobalVariable", _refuses(_SETTING), True),
        (get("QgsExpressionContextUtils"), "setGlobalVariables", _refuses(_SETTING), True),
        (get("QgsExpressionContextUtils"), "removeGlobalVariable", _refuses(_SETTING), True),
    ]
    for name in ("addFeatures", "addFeature", "changeAttributeValues", "changeGeometryValues",
                 "changeFeatures", "addAttributes", "renameAttributes", "createSpatialIndex",
                 "createAttributeIndex"):
        out.append((get("QgsVectorDataProvider"), name, _provider_write(False), False))
    for name in ("deleteFeatures", "deleteAttributes", "truncate"):
        out.append((get("QgsVectorDataProvider"), name, _provider_write(True), False))
    for cls in ("QgsNetworkAccessManager", "QgsBlockingNetworkRequest"):
        for name in ("get", "post", "put", "head", "deleteResource", "sendCustomRequest", "blockingGet",
                     "blockingPost"):
            out.append((get(cls), name, _refuses(_NETWORK), False))
    out.append((get("QgsNetworkContentFetcher"), "fetchContent", _refuses(_NETWORK), False))
    out.extend(_read_traps())
    if gui is not None:
        out.append((getattr(gui, "QgsMapCanvas", None), "saveAsImage", _path_arg(1), False))
        iface_type = getattr(gui, "QgisInterface", None)
        for name in ("addVectorLayer", "addRasterLayer", "addMeshLayer", "addVectorTileLayer",
                     "addPointCloudLayer"):
            out.append((iface_type, name, _add_by_uri, False))
    return out


def _read_traps() -> list:

    if "qgis.core" not in sys.modules:
        return []
    try:
        import qgis.core as core
    except ImportError:
        return []
    get = lambda name: getattr(core, name, None)  # noqa: E731
    out = [(get("QgsVectorLayer"), name, _layer_read, False)
           for name in ("getFeatures", "getFeature", "uniqueValues", "aggregate", "minimumValue", "maximumValue")]
    out += [(get("QgsVectorDataProvider"), "getFeatures", _provider_read, False)]
    out += [(get("QgsRasterDataProvider"), name, _provider_read, False) for name in ("block", "identify", "sample")]
    return out


def guard_child() -> None:







    state = _state()
    if not install():
        return

    security.read_scope_enabled()
    security.validate_read(os.getcwd())
    _software_roots()
    arm = _Arm(ce.D)
    state.thread = threading.get_ident()
    arm.patch(_read_traps())
    state.handler = arm.on_event
    _CURRENT.append(arm)


def judge_outputs(parameters) -> None:

    arm = _current()
    if arm is None or not isinstance(parameters, dict):
        return
    for value in parameters.values():
        if isinstance(value, str) and ("/" in value or "\\" in value) and os.path.splitext(value.split("|", 1)[0])[1]:
            if not os.path.exists(value.split("|", 1)[0]):
                arm.need(*file_class(value))


def judge_write(path) -> None:





    arm = _current()
    if arm is not None:
        arm.need(*file_class(path))


_CURRENT: list = []


def _current() -> _Arm | None:
    return _CURRENT[-1] if _CURRENT and threading.get_ident() == _state().thread else None


class armed:  # noqa: N801







    def __init__(self, granted: str) -> None:
        self.arm = _Arm(granted)

    def __enter__(self) -> _Arm:
        state = _state()
        install()
        state.thread = threading.get_ident()
        self.arm.patch()
        state.handler = self.arm.on_event
        _CURRENT.append(self.arm)
        return self.arm

    def __exit__(self, *exc) -> bool:
        state = _state()
        state.handler = None
        if _CURRENT and _CURRENT[-1] is self.arm:
            _CURRENT.pop()
        self.arm.unpatch()
        return False


class suspended:  # noqa: N801


    def __enter__(self):
        state = _state()
        self.handler = state.handler
        self.arm = _CURRENT.pop() if _CURRENT else None
        state.handler = None
        if self.arm is not None:
            self.arm.unpatch()
        return self

    def __exit__(self, *exc) -> bool:
        state = _state()
        if self.arm is not None:
            self.arm.patch()
            _CURRENT.append(self.arm)
        state.handler = self.handler
        return False

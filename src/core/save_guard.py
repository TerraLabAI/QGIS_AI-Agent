# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
















from __future__ import annotations

import os
import time
import zipfile

from qgis.core import QgsAuxiliaryStorage, QgsProject
from qgis.PyQt.QtCore import QCoreApplication, QDir, QEvent, QObject, QTimer

from . import telemetry
from . import telemetry_events as ev
from .logger import log, log_warning



_ERRORS = (
    ("Unable to write temporary qgs file", "temp_unwritable"),
    ("Unable to save zip file", "zip_unwritable"),
    ("Unable to save auxiliary storage", "auxiliary_storage"),
    ("Unable to create backup file", "backup_unwritable"),
    ("Your project may be corrupted on disk", "write_incomplete"),
    ("Unable to save to file", "file_unwritable"),
    ("Unable to save project to storage", "storage"),
)
_SAVE_ACTIONS = ("mActionSaveProject", "mActionSaveProjectAs", "mActionExit")
_ZONE_PROPERTY = "terralab/zone_of_interest"


def tr(text: str) -> str:
    return QCoreApplication.translate("SaveGuard", text)


def error_code(text: str) -> str:
    for needle, code in _ERRORS:
        if needle in (text or ""):
            return code
    return "other" if text else ""


def _temp_path() -> str:


    global _known_temp
    path = QDir.tempPath()
    if path:
        _known_temp = path
        return path
    env = next((os.environ[k] for k in ("TMPDIR", "TMP", "TEMP") if os.environ.get(k)), "")
    return _known_temp or env.rstrip("/\\")


_known_temp = ""


def temp_folder_ready(where: str) -> bool:








    global unsaved_aux_edits
    path = _temp_path()
    made = []
    if path and not os.path.isdir(path):
        made.append(path)
    project = QgsProject.instance()
    files = _project_temp_files(project)
    _note_held(files)
    gone = [name for name in files if not os.path.exists(name)]
    if not made and not gone:
        return False
    ok = True
    sources: dict = {}
    for name in made + gone:
        try:
            if name == path:
                os.makedirs(path, exist_ok=True)
                continue
            source = _bring_back(name, project)
            sources[source] = sources.get(source, 0) + 1
        except (OSError, zipfile.BadZipFile) as exc:
            ok = False
            log_warning(f"Not made again in QGIS's temp folder ({name}): {exc}")
    if any(_is_auxiliary(name) for name in gone):
        unsaved_aux_edits = _pending_aux_edits(project)
    if ok and gone:
        log_warning(f"QGIS's temp folder or the project's files in it were gone, made again so the project "
                    f"can be saved ({len(gone)} file(s): {sources}): {path}")
    telemetry.track(ev.QGIS_TEMP_FOLDER_RESTORED, {
        "where": where, "ok": ok, "root": bool(made), "files": len(gone),
        "from_open": sources.get("open", 0), "from_disk": sources.get("disk", 0),
        "empty": sources.get("empty", 0), "aux_edits_unsaved": unsaved_aux_edits}, flush_now=True)
    return True



_held: dict = {}


unsaved_aux_edits = 0


def forget_held() -> None:

    global unsaved_aux_edits
    _held.clear()
    unsaved_aux_edits = 0


def _project_temp_files(project) -> list:
    files = [name for name in project.attachedFiles() if name]
    storage = project.auxiliaryStorage()
    if storage is not None and storage.currentFileName():
        files.append(storage.currentFileName())
    return files


def _note_held(files: list) -> None:
    for name in files:
        try:
            st = os.stat(name)
        except OSError:
            continue
        _held[name] = (st.st_dev, st.st_ino)


def _open_descriptor(ident):


    if ident is None:
        return None
    for folder in ("/proc/self/fd", "/dev/fd"):
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for entry in names:
            if not entry.isdigit():
                continue
            try:
                st = os.fstat(int(entry))
            except OSError:
                continue
            if (st.st_dev, st.st_ino) == ident:
                return int(entry)
        return None
    return None


def _write_whole(name: str, read) -> None:
    os.makedirs(os.path.dirname(name), exist_ok=True)
    part = name + ".part"
    with open(part, "wb") as out:
        while True:
            chunk = read()
            if not chunk:
                break
            out.write(chunk)
    os.replace(part, name)


def _bring_back(name: str, project) -> str:

    fd = _open_descriptor(_held.get(name))
    if fd is not None:
        offset = [0]

        def read():
            chunk = os.pread(fd, 1 << 20, offset[0])
            offset[0] += len(chunk)
            return chunk

        _write_whole(name, read)
        return "open"
    return _from_disk(name, project)


def _from_disk(name: str, project) -> str:



    disk = project.fileName() or ""
    if not disk:
        _empty_file(name)
        return "empty"
    if project.projectStorage() is not None or not os.path.isfile(disk):
        raise OSError(f"the saved project ({disk}) is not a file here")
    aux = _is_auxiliary(name)
    ext = "." + QgsAuxiliaryStorage.extension().lower()
    stem = os.path.splitext(os.path.basename(disk))[0]
    if zipfile.is_zipfile(disk):
        archive = disk
    elif aux:
        sibling = os.path.join(os.path.dirname(disk), stem + ext)
        if not os.path.isfile(sibling):
            _empty_file(name)
            return "empty"
        with open(sibling, "rb") as src:
            _write_whole(name, lambda: src.read(1 << 20))
        return "disk"
    else:
        archive = os.path.join(os.path.dirname(disk), stem + "_attachments.zip")
        if not os.path.isfile(archive):
            _empty_file(name)
            return "empty"
    base = os.path.basename(name).lower()
    with zipfile.ZipFile(archive) as zf:
        member = next((m for m in zf.namelist()
                       if (m.lower().endswith(ext) if aux else os.path.basename(m).lower() == base)), None)
        if member is None:
            _empty_file(name)
            return "empty"
        with zf.open(member) as src:
            _write_whole(name, lambda: src.read(1 << 20))
    return "disk"


def _is_auxiliary(name: str) -> bool:
    storage = QgsProject.instance().auxiliaryStorage()
    current = storage.currentFileName() if storage is not None else ""
    return bool(current) and _key(name) == _key(current)


def _pending_aux_edits(project) -> int:
    count = 0
    for layer in project.mapLayers().values():
        aux = layer.auxiliaryLayer() if hasattr(layer, "auxiliaryLayer") else None
        if aux is not None and aux.isModified():
            count += 1
    return count


def _empty_file(name: str) -> None:


    if name.lower().endswith("_styles.db"):
        try:
            from qgis.core import QgsStyle

            if QgsStyle().createDatabase(name):
                return
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Style database not created, an empty file stands in ({name}): {exc}")
    with open(name, "ab"):
        pass


class SaveGuard(QObject):


    def __init__(self, iface, busy=None, keep_layers=None, writing=None):
        super().__init__()
        self._iface = iface
        self._busy = busy or (lambda: False)
        self._keep_layers = keep_layers
        self._writing = writing or (lambda: 0)
        self._watched: list = []
        self._menus: list = []
        self._app = None
        self._blocker = None
        self._pending: dict | None = None
        self._quitting = False
        self._restored = False
        self._bar_item = None


    def install(self) -> None:
        _temp_path()
        _note_held(_project_temp_files(QgsProject.instance()))
        window = self._iface.mainWindow()
        self._watch(window)
        for name in _SAVE_ACTIONS:
            action = window.findChild(_qaction(), name) if window is not None else None
            if action is None:
                continue
            self._watch(action)
            widgets = getattr(action, "associatedWidgets", None) or getattr(action, "associatedObjects", None)
            for widget in (widgets() if widgets else []):
                self._watch(widget)

                show = getattr(widget, "aboutToShow", None)
                if show is not None and widget not in self._menus:
                    show.connect(self._before_menu)
                    self._menus.append(widget)
        app = QCoreApplication.instance()
        if app is not None and hasattr(app, "applicationStateChanged"):
            app.applicationStateChanged.connect(self._on_app_state)
            self._app = app
        project = QgsProject.instance()
        project.writeProject.connect(self._on_write)
        project.readProject.connect(self._on_read)
        try:
            from qgis.gui import QgsApplicationExitBlockerInterface

            guard = self

            class _RunBlocker(QgsApplicationExitBlockerInterface):
                def allowExit(self):  # noqa: N802
                    return guard.allow_exit()

            self._blocker = _RunBlocker()
            self._iface.registerApplicationExitBlocker(self._blocker)
        except Exception as exc:  # noqa: BLE001
            self._blocker = None
            log_warning(f"Quit question not installed: {exc}")

    def uninstall(self) -> None:
        for obj in self._watched:
            try:
                obj.removeEventFilter(self)
            except RuntimeError:
                pass
        self._watched = []
        for menu in self._menus:
            try:
                menu.aboutToShow.disconnect(self._before_menu)
            except (TypeError, RuntimeError):
                pass
        self._menus = []
        if self._app is not None:
            try:
                self._app.applicationStateChanged.disconnect(self._on_app_state)
            except (TypeError, RuntimeError):
                pass
            self._app = None
        project = QgsProject.instance()
        for signal, slot in ((project.writeProject, self._on_write), (project.readProject, self._on_read)):
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        if self._blocker is not None:
            try:
                self._iface.unregisterApplicationExitBlocker(self._blocker)
            except Exception:  # nosec B110
                pass
            self._blocker = None
        self._drop_bar()

    def _watch(self, obj) -> None:
        if obj is not None and obj not in self._watched:
            obj.installEventFilter(self)
            self._watched.append(obj)


    def eventFilter(self, obj, event):  # noqa: N802
        kind = event.type()
        if kind == QEvent.Type.Close and obj is self._iface.mainWindow():
            self._quitting = True
            self._restored = temp_folder_ready("quit") or self._restored

            QTimer.singleShot(0, self._end_quit_turn)
        elif kind in (QEvent.Type.Shortcut, QEvent.Type.MouseButtonPress, QEvent.Type.Show):
            self._restored = temp_folder_ready("save") or self._restored
        return False

    def _before_menu(self) -> None:
        self._restored = temp_folder_ready("save") or self._restored

    def _on_app_state(self, state) -> None:
        from qgis.PyQt.QtCore import Qt

        if state == Qt.ApplicationState.ApplicationActive:
            self._restored = temp_folder_ready("focus") or self._restored

    def _end_quit_turn(self) -> None:
        self._quitting = False

    def _on_write(self, doc=None) -> None:
        project = QgsProject.instance()
        name = project.fileName() or ""
        if _is_own_copy(name) or _gathered_sections(doc):
            return
        self._restored = temp_folder_ready("write") or self._restored
        if self._pending is None:
            self._pending = {"at": time.monotonic(), "during_run": bool(self._busy()),
                             "while_quitting": self._quitting,
                             "format": os.path.splitext(name)[1].lower().lstrip(".")[:8] or "none"}
            QTimer.singleShot(0, self._settle)

    def _settle(self) -> None:
        pending, self._pending = self._pending, None
        if pending is None:
            return
        project = QgsProject.instance()
        error = project.error() or ""
        temporary = self._temporary_layers()
        props = {
            "ok": not error, "error_code": error_code(error), "during_run": pending["during_run"],
            "while_quitting": pending["while_quitting"], "format": pending["format"],
            "temp_folder_restored": self._restored, "agent_temp_layers": len(temporary),
            "duration_ms": int((time.monotonic() - pending["at"]) * 1000),
        }
        self._restored = False
        telemetry.track(ev.PROJECT_SAVE_FINISHED, props, flush_now=bool(error))
        global unsaved_aux_edits
        if unsaved_aux_edits:
            unsaved_aux_edits = 0
            self._iface.messageBar().pushWarning(
                tr("Label positions not saved"),
                tr("QGIS's temporary folder was deleted while the project was open: labels or diagrams "
                   "moved since the previous save could not be saved. The positions saved before are "
                   "kept. Move them again, then save."))
        if error:
            log_warning(f"QGIS could not save the project: {error}")
        elif temporary and not pending["during_run"] and not pending["while_quitting"]:
            self._name_temporary(temporary)
        elif not temporary:


            self._drop_bar()


    def _temporary_layers(self) -> list:



        try:
            from ..tools.layer_tools import scratch_layers
        except Exception:  # noqa: BLE001
            return []
        memory, scratch = scratch_layers(QgsProject.instance())
        kept = []
        for layer in memory:
            if layer.customProperty(_ZONE_PROPERTY) or layer.customProperty("skipMemoryLayersCheck"):
                continue
            if layer.featureCount() != 0:
                kept.append(layer)
        return kept + list(scratch)

    def _name_temporary(self, layers: list) -> None:
        names = ", ".join(layer.name() for layer in layers[:3]) + ("..." if len(layers) > 3 else "")
        text = tr("{n} temporary layer(s) ({names}) are not saved to disk: their contents will be "
                  "lost when the project closes.").format(n=len(layers), names=names)
        bar = self._iface.messageBar()
        try:
            from qgis.core import Qgis
            from qgis.PyQt.QtWidgets import QPushButton

            self._drop_bar()
            item = bar.createMessage(tr("Project saved"), text)
            if self._keep_layers is not None and not self._busy():
                button = QPushButton(tr("Keep them in a file"))
                button.clicked.connect(self._keep)
                item.layout().addWidget(button)
            self._bar_item = bar.pushWidget(item, Qgis.MessageLevel.Warning, 0)
        except Exception as exc:  # noqa: BLE001
            log_warning(f"Temporary layers not shown: {exc}")
        log(f"Saved with temporary layers: {names}")

    def _keep(self) -> None:
        self._drop_bar()
        if self._keep_layers is None or self._busy():
            return

        result = self._keep_layers() or {}
        if result.get("_error"):
            self._iface.messageBar().pushWarning(tr("Temporary layers not kept"), str(result["_error"]))

    def _drop_bar(self) -> None:
        item, self._bar_item = self._bar_item, None
        if item is not None:
            try:
                self._iface.messageBar().popWidget(item)
            except RuntimeError:
                pass


    def allow_exit(self) -> bool:


        self._restored = temp_folder_ready("quit") or self._restored
        busy = bool(self._busy())
        decision = "quit"
        if busy:
            from qgis.PyQt.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self._iface.mainWindow(), tr("AI Agent is still working"),
                tr("The AI agent has not finished your request. Quit QGIS and stop it?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            decision = "quit" if answer == QMessageBox.StandardButton.Yes else "stay"
        elif self._writing():


            from qgis.PyQt.QtWidgets import QMessageBox

            answer = QMessageBox.question(
                self._iface.mainWindow(), tr("AI Agent is still saving layers"),
                tr("{n} layer(s) are still being written to the GeoPackage beside the project. If QGIS "
                   "quits now, they are lost. Quit anyway?").format(n=self._writing()),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            decision = "quit" if answer == QMessageBox.StandardButton.Yes else "stay"
        project = QgsProject.instance()
        telemetry.track(ev.QGIS_QUIT, {
            "run_active": busy, "decision": decision, "project_dirty": bool(project.isDirty()),
            "project_saved_once": bool(project.fileName()), "temp_folder_restored": self._restored,
            "agent_temp_layers": len(self._temporary_layers())}, flush_now=True)
        return decision == "quit"


    def _on_read(self, _doc=None) -> None:
        project = QgsProject.instance()
        forget_held()
        _note_held(_project_temp_files(project))
        if _is_own_copy(project.fileName() or ""):
            return
        layers = list(project.mapLayers().values())
        dead = [layer for layer in layers if not layer.isValid()]
        if not dead:
            return
        roots = _temp_roots()
        in_temp = sum(1 for layer in dead if _under(str(layer.source() or ""), roots))
        telemetry.track(ev.PROJECT_OPENED_DEAD_PATHS, {
            "layers": len(layers), "dead": len(dead), "dead_in_temp": in_temp})


def _qaction():
    from qgis.PyQt.QtWidgets import QAction

    return QAction


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path)).replace("\\", "/")


def _temp_roots() -> list:
    from . import policy

    roots = [policy.AGENT_TMP_DIR, _temp_path()]
    return [_key(root).rstrip("/") + "/" for root in roots if root]


def _under(source: str, roots: list) -> bool:
    path = _key(source.split("|", 1)[0])
    return any(path.startswith(root) for root in roots)


def _gathered_sections(doc) -> bool:




    try:
        return doc is not None and not doc.documentElement().hasAttribute("version")
    except Exception:  # noqa: BLE001
        return False


def _is_own_copy(file_name: str) -> bool:

    if not file_name:
        return False
    try:
        from .settings import account_dir

        return _key(file_name).startswith(_key(os.path.dirname(account_dir())).rstrip("/") + "/")
    except Exception:  # noqa: BLE001
        return False

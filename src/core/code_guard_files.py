# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import glob as _glob
import os
import pathlib as _pathlib
import tempfile as _tempfile
import types
import zipfile as _zipfile

from .host_platform import IS_WINDOWS


def _check(path, write: bool = False, overwrite: bool | None = None, scoped: bool = True) -> str:





    from .code_guard import note_written
    from .security import anchor, validate_path

    if isinstance(path, int):
        raise PermissionError("execute_code cannot use a file descriptor.")
    text = anchor(path)




    remedy = "This call has no overwrite option; a new file name avoids it." if overwrite is False else None
    error = validate_path(str(text), write=write, overwrite=overwrite, overwrite_remedy=remedy,
                          scoped=scoped and not write)
    if error:
        raise PermissionError(error)
    if write:




        note_written(text)
    return str(text)


def _allowed(path) -> bool:
    from .security import validate_read

    return validate_read(str(path)) is None




def safe_glob(pathname, *, root_dir=None, recursive=False):






    from .security import anchor

    pattern = os.fspath(pathname)
    if root_dir is not None:
        base = _check(root_dir)
        found = _matches(os.path.join(base, pattern), recursive)
        return [os.path.relpath(p, base) for p in found]
    return _matches(anchor(pattern), recursive)


def _parts(path: str) -> list:

    parts = []
    while True:
        head, tail = os.path.split(path)
        if head == path:
            parts.append(head)
            break
        if tail:
            parts.append(tail)
        path = head
        if not head:
            break
    return parts[::-1]


def _entries(folder: str, part: str, dirs_only: bool) -> list:

    import fnmatch

    try:
        with os.scandir(folder) as listing:
            names = [(e.name, e.is_dir()) for e in listing]
    except OSError:
        return []
    hidden = part.startswith(".")
    return sorted(os.path.join(folder, name) for name, is_dir in names
                  if (hidden or not name.startswith(".")) and (is_dir or not dirs_only)
                  and fnmatch.fnmatch(name, part))


def _subfolders(folder: str):





    pending = [folder]
    while pending:
        current = pending.pop()
        yield current
        try:
            with os.scandir(current) as listing:
                children = [e.path for e in listing
                            if not e.name.startswith(".") and e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        pending.extend(sorted((c for c in children if _allowed(c)), reverse=True))


def _matches(pattern: str, recursive: bool) -> list:






    parts = _parts(pattern)
    start = 0
    while start < len(parts) and not _glob.has_magic(parts[start]):
        start += 1
    root = os.path.join(*parts[:start]) if start else ""
    rest = parts[start:]
    if not rest:
        return [root] if os.path.lexists(root) and _allowed(root) else []
    _check(root or os.curdir)
    found: list = []

    def step(folder: str, index: int) -> None:
        part = rest[index]
        last = index == len(rest) - 1
        if recursive and part == "**":
            for sub in _subfolders(folder):
                if last:
                    found.extend(p for p in _entries(sub, "*", False) if _allowed(p))
                else:
                    step(sub, index + 1)
            return
        for path in _entries(folder, part, dirs_only=not last):
            if last:
                if _allowed(path):
                    found.append(path)
            elif _allowed(path):
                step(path, index + 1)

    step(root, 0)
    return list(dict.fromkeys(found))


def _safe_iglob(pathname, *, root_dir=None, recursive=False):
    return iter(safe_glob(pathname, root_dir=root_dir, recursive=recursive))


def safe_glob_module() -> types.ModuleType:
    module = types.ModuleType("glob", "glob as exposed to execute_code: results pass the read check")
    module.glob = safe_glob
    module.iglob = _safe_iglob
    module.escape = _glob.escape
    module.has_magic = _glob.has_magic
    return module




def _mkdtemp(suffix=None, prefix=None, dir=None):  # noqa: A002
    for part in (suffix, prefix):
        if part and ("/" in str(part) or "\\" in str(part) or ".." in str(part)):
            raise PermissionError("mkdtemp takes a plain prefix and suffix, not a path.")
    _check(dir or _tempfile.gettempdir(), write=True)
    return _tempfile.mkdtemp(suffix=suffix, prefix=prefix, dir=dir)


def safe_tempfile_module() -> types.ModuleType:
    module = types.ModuleType("tempfile", "tempfile as exposed to execute_code: gettempdir and mkdtemp")
    module.gettempdir = _tempfile.gettempdir
    module.mkdtemp = _mkdtemp
    return module




class ZipArchive:


    __slots__ = ("filename", "mode", "namelist", "infolist", "getinfo", "read", "testzip", "printdir",
                 "extract", "extractall", "write", "writestr", "close")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __iter__(self):
        return iter(self.namelist())

    def __repr__(self) -> str:
        return f"<ZipArchive {self.filename!r} mode={self.mode!r}>"


def _zip_file(file, mode="r", compression=_zipfile.ZIP_STORED, allowZip64=True, compresslevel=None):  # noqa: N803
    if mode not in ("r", "w", "a", "x"):
        raise ValueError("ZipFile mode is 'r', 'w', 'a' or 'x'")
    if not isinstance(file, (str, os.PathLike)):
        raise PermissionError("execute_code opens a zip archive by its path.")
    writing = mode != "r"
    path = _check(file, write=writing, overwrite=False if mode in ("w", "x") else None)
    real = _zipfile.ZipFile(path, mode, compression, allowZip64, compresslevel)
    archive = ZipArchive()
    archive.filename = path
    archive.mode = mode
    for name in ("namelist", "infolist", "getinfo", "read", "testzip", "printdir", "close"):
        setattr(archive, name, getattr(real, name))

    def extract(member, path=None, pwd=None):
        target = _check(path or "", write=True)
        return real.extract(member, target, pwd)

    def extractall(path=None, members=None, pwd=None):
        target = _check(path or "", write=True)
        return real.extractall(target, members, pwd)

    def write(filename, arcname=None, compress_type=None, compresslevel=None):
        if not writing:
            raise ValueError("write() needs the archive opened with mode 'w', 'a' or 'x'")
        return real.write(_check(filename), arcname, compress_type, compresslevel)

    def writestr(zinfo_or_arcname, data, compress_type=None, compresslevel=None):
        if not writing:
            raise ValueError("writestr() needs the archive opened with mode 'w', 'a' or 'x'")
        return real.writestr(zinfo_or_arcname, data, compress_type, compresslevel)

    archive.extract = extract
    archive.extractall = extractall
    archive.write = write
    archive.writestr = writestr
    return archive


def _is_zipfile(filename) -> bool:
    return _zipfile.is_zipfile(_check(filename))


def safe_zipfile_module() -> types.ModuleType:
    module = types.ModuleType("zipfile", "zipfile as exposed to execute_code: paths pass the file check")
    module.ZipFile = _zip_file
    module.is_zipfile = _is_zipfile
    for name in ("ZipInfo", "BadZipFile", "LargeZipFile", "ZIP_STORED", "ZIP_DEFLATED", "ZIP_BZIP2", "ZIP_LZMA"):
        setattr(module, name, getattr(_zipfile, name))
    return module




_PureNative = _pathlib.PureWindowsPath if IS_WINDOWS else _pathlib.PurePosixPath


class Path(_PureNative):









    __slots__ = ()

    @classmethod
    def cwd(cls):

        from .security import workspace_dir

        return cls(workspace_dir())

    @classmethod
    def home(cls):
        return cls(os.path.expanduser("~"))

    def expanduser(self):
        return type(self)(os.path.expanduser(str(self)))

    def absolute(self):
        from .security import anchor

        return type(self)(os.path.abspath(anchor(str(self))))

    def resolve(self, strict=False):
        from .security import anchor

        return type(self)(os.path.realpath(anchor(str(self))))

    def exists(self):
        return os.path.exists(_check(self, scoped=False))

    def is_file(self):
        return os.path.isfile(_check(self, scoped=False))

    def is_dir(self):
        return os.path.isdir(_check(self, scoped=False))

    def stat(self):
        return os.stat(_check(self, scoped=False))

    def iterdir(self):
        folder = _check(self)
        return iter([self / name for name in sorted(os.listdir(folder)) if _allowed(os.path.join(folder, name))])

    def glob(self, pattern):
        return iter([type(self)(p) for p in safe_glob(os.path.join(str(self), pattern), recursive=True)])

    def rglob(self, pattern):
        return self.glob(os.path.join("**", pattern))

    def read_bytes(self):
        path = _check(self)
        with open(path, "rb") as handle:  # noqa: PTH123
            return handle.read()

    def read_text(self, encoding=None, errors=None):
        path = _check(self)

        with open(path, encoding=encoding or "utf-8-sig", errors=errors) as handle:  # noqa: PTH123
            return handle.read()

    def open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):  # noqa: A003


        from .code_guard import _guarded_open

        extra = {k: v for k, v in (("encoding", encoding), ("errors", errors), ("newline", newline))
                 if v is not None}
        return _guarded_open(str(self), mode, buffering, **extra)

    def write_text(self, data, encoding=None, errors=None, newline=None):

        with self.open("w", encoding=encoding, errors=errors, newline=newline) as handle:
            return handle.write(data)

    def write_bytes(self, data):
        with self.open("wb") as handle:
            return handle.write(data)

    def mkdir(self, mode=0o777, parents=False, exist_ok=False):
        path = _check(self, write=True)
        if parents:
            os.makedirs(path, mode, exist_ok=exist_ok)
        elif not (exist_ok and os.path.isdir(path)):
            os.mkdir(path, mode)


def safe_pathlib_module() -> types.ModuleType:
    module = types.ModuleType("pathlib", "pathlib as exposed to execute_code: pure paths, checked reads")
    module.Path = Path
    module.PurePath = _pathlib.PurePath
    module.PurePosixPath = _pathlib.PurePosixPath
    module.PureWindowsPath = _pathlib.PureWindowsPath
    return module


GUARDED_MODULES = {
    "glob": safe_glob_module,
    "tempfile": safe_tempfile_module,
    "zipfile": safe_zipfile_module,
    "pathlib": safe_pathlib_module,
}

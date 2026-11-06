# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import os
import urllib.parse

from qgis.core import QgsProject, QgsVectorLayer

from ..core import accounts, accounts_cloud, limits, vsi
from ..core.accounts_cloud import AZURE, GCS, S3, Ref
from ..core.background import run_on_main_thread, still_awaited, take
from ..core.host_platform import remove_tree
from ..core.net_state import is_cancelled
from ..core.net_stream import STREAM_KEEP_FREE_BYTES, free_disk_bytes
from ..core.output_paths import safe_file_name
from ..core.policy import create_managed_temp_dir
from ..core.tool_registry import tool_error
from .data_common import built_here, worker_options


def ref_of(source: str) -> Ref | None:
    return accounts_cloud.parse(source)


def public_address(ref: Ref) -> str:




    if _is_web(ref) or _is_vsi(ref):
        return ""
    key = urllib.parse.quote(ref.key)
    if ref.store == S3:
        return f"https://{ref.bucket}.s3.amazonaws.com/{key}"
    if ref.store == GCS:
        return f"https://storage.googleapis.com/{ref.bucket}/{key}"
    return f"https://{ref.account}.blob.core.windows.net/{ref.bucket}/{key}" if ref.account else ""


def _is_web(ref: Ref) -> bool:
    return ref.address.lower().startswith(("http://", "https://"))


def _is_vsi(ref: Ref) -> bool:
    return ref.address.startswith("/vsi")


def add(ref: Ref, args: dict) -> tuple[str, dict] | None:


    key, entry = accounts_cloud.find(ref)
    what = f"Open {ref.address}."
    if entry is None:
        if public_address(ref) or _is_web(ref) or _is_vsi(ref):
            return None
        return "", accounts_cloud.need(ref, "sign_in", _not_reached(ref), key, what)
    ref = accounts_cloud.located(ref, key)
    if not accounts.granted(key):
        return "", accounts_cloud.consent(ref, key)
    if not accounts_cloud.give(ref, key):
        return "", tool_error("The saved keys could not be handed to GDAL for this file.", "EXECUTION_FAILED",
                              host=ref.shown)
    ext = os.path.splitext(ref.key.lower())[1]
    kind, result = _streamed(ref, key, args) if ext in vsi.VSICURL_ALLOWED_EXTENSIONS else \
        _downloaded(ref, key, args, ext)
    if isinstance(result, dict) and result.get("_error") is None and accounts.MARK not in result:
        accounts.worked(key)
    return kind, result


def after_failure(ref: Ref, result: dict) -> dict:


    if not isinstance(result, dict) or result.get("code") == "NETWORK_ERROR":
        return result
    marked = result.pop(accounts.MARK, None)
    failure = result.pop("failure", None) if marked else None
    failure = failure if isinstance(failure, dict) else result
    status = _status(failure)
    hidden = ref.store == AZURE or not ref.located
    if status in (401, 403) or (hidden and status in (0, 400, 404, 409)) or marked:
        return accounts_cloud.need(ref, "sign_in", failure, what=f"Open {ref.address}.")
    return failure


def _status(failure: dict) -> int:
    for name in ("http_status", "status"):
        try:
            if failure.get(name):
                return int(failure[name])
        except (TypeError, ValueError):
            continue
    return 0


def _not_reached(ref: Ref) -> dict:
    return tool_error(f"{ref.address} is in {ref.shown}, and no access to it is saved on this computer.",
                      "PERMISSION_DENIED", host=ref.shown)


def _refused(ref: Ref, key: str, failure: dict, said: list[str]) -> dict:

    if any(accounts_cloud.REFUSED.search(line) for line in said):
        failure = dict(failure, code="PERMISSION_DENIED")
        failure.pop("gdal_error", None)
        return accounts_cloud.need(ref, "retry", failure, key, what=f"Open {ref.address}.")
    return failure


def _open_here(ref: Ref):


    from osgeo import gdal
    said: list[str] = []
    gdal.PushErrorHandler(lambda _cls, _no, msg: said.append(str(msg)))
    gdal.SetThreadLocalConfigOption("CPL_DEBUG", "ON")
    try:
        try:
            dataset = gdal.OpenEx(ref.path, gdal.OF_VECTOR | gdal.OF_RASTER | gdal.OF_READONLY)
        except RuntimeError as exc:
            said.append(str(exc))
            dataset = None
    finally:
        gdal.SetThreadLocalConfigOption("CPL_DEBUG", None)
        gdal.PopErrorHandler()
    return dataset, said


def _streamed(ref: Ref, key: str, args: dict) -> tuple[str, dict]:
    dataset, said = _open_here(ref)
    name = str(args.get("name") or "").strip() or os.path.splitext(os.path.basename(ref.key))[0] or ref.bucket
    if dataset is None:
        failure = tool_error(f"{ref.address} did not open.", "INVALID_ARGS", url=ref.address)
        return "", _refused(ref, key, failure, said)
    layers = [dataset.GetLayer(i).GetName() for i in range(dataset.GetLayerCount())]
    is_raster = dataset.RasterCount > 0 and not layers
    dataset = None
    if is_raster:
        from .stac_tools import _add_own_s3
        result = _add_own_s3(ref.address, ref.path, name, None, None, args.get("bbox"),
                             on_refused=lambda failure: _refused(ref, key, failure, said + [str(failure)]))
        return "raster", result
    wanted = str(args.get("layer") or "").strip()
    if len(layers) > 1 and wanted not in layers:
        return "", tool_error(f"{ref.address} holds {len(layers)} layers: name one in layer.", "INVALID_ARGS",
                              layers_available=layers[:50], url=ref.address)
    uri = f"{ref.path}|layername={wanted}" if len(layers) > 1 else ref.path
    built = built_here(lambda: QgsVectorLayer(uri, name, "ogr", worker_options()))

    def _create():
        layer = take(built)
        if layer is None:
            layer = QgsVectorLayer(uri, name, "ogr")
        if not layer.isValid():
            return tool_error(f"{ref.address} did not open as a layer.", "INVALID_ARGS", url=ref.address)
        if not still_awaited():
            return tool_error("The call ended before the layer was added.", "CANCELLED")
        QgsProject.instance().addMapLayer(layer)
        return {"layer_name": layer.name(), "layer_id": layer.id(), "url": ref.address,
                "read_with": "own_account", **({"layer": wanted} if wanted else {})}

    result = run_on_main_thread(_create, timeout=60)
    return ("vector", result) if not result.get("_error") else ("", _refused(ref, key, result, said))


def _downloaded(ref: Ref, key: str, args: dict, ext: str) -> tuple[str, dict]:


    from osgeo import gdal
    allowed = ext or "{noext}"
    gdal.SetThreadLocalConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", allowed)
    said: list[str] = []
    gdal.PushErrorHandler(lambda _cls, _no, msg: said.append(str(msg)))
    gdal.SetThreadLocalConfigOption("CPL_DEBUG", "ON")
    folder = create_managed_temp_dir("cloud")
    local = os.path.join(folder, safe_file_name(os.path.basename(ref.key), "download" + ext))
    try:
        stat = gdal.VSIStatL(ref.path)
        if stat is None:
            remove_tree(folder)
            failure = tool_error(f"{ref.address} was not found, or the keys may not read it.", "INVALID_ARGS",
                                 url=ref.address)
            return "", _refused(ref, key, failure, said)
        free = free_disk_bytes(folder)
        cap = limits.current("MAX_STREAM_BYTES")
        if stat.size > cap or (free is not None and stat.size > free - STREAM_KEEP_FREE_BYTES):
            remove_tree(folder)
            return "", tool_error(f"{ref.address} is {stat.size} bytes, more than this computer takes in one load.",
                                  "INVALID_ARGS", size_bytes=stat.size, url=ref.address)
        copied = gdal.CopyFile(ref.path, local, callback=lambda *_: 0 if is_cancelled() else 1)
    except RuntimeError as exc:
        said.append(str(exc))
        copied = -1
    finally:
        accounts_cloud.take_back(ref, key)
        gdal.SetThreadLocalConfigOption("CPL_DEBUG", None)
        gdal.PopErrorHandler()
        gdal.SetThreadLocalConfigOption("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", None)
    if is_cancelled():
        remove_tree(folder)
        return "", tool_error("Stopped.", "CANCELLED")
    if copied != 0 or not os.path.isfile(local):
        remove_tree(folder)
        failure = tool_error(f"{ref.address} was not downloaded.", "NETWORK_ERROR", url=ref.address)
        return "", _refused(ref, key, failure, said)
    from .facade import _dispatch_add, deduce_kind
    kind = deduce_kind(local, args.get("kind"))
    result = _dispatch_add(kind, dict(args, source=local))
    if isinstance(result, dict) and result.get("_error") is None:
        result.update({"url": ref.address, "read_with": "own_account", "path": local})
    return kind, result


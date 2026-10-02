# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later





































from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405
import zipfile
import zlib
from contextlib import closing

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import http_headers, net
from ..core.background import run_on_main_thread
from ..core.host_platform import remove_tree
from ..core.logger import log
from ..core.output_paths import default_folder, exports_folder, safe_file_name
from ..core.policy import create_managed_temp_dir
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .data_tools import _USER_AGENT

HUB = "https://hub.qgis.org"
_LIST_URL = HUB + "/api/v1/resources/"
_DOWNLOAD_URL = HUB + "/api/v1/resource/{uuid}/"



IMPORT, DOWNLOAD, USER_INSTALLS, VIEW = "import", "download", "user_installs", "view"
TYPES = {
    "style": ("styles", IMPORT),
    "layerdefinition": ("layerdefinitions", IMPORT),
    "geopackage": ("geopackages", DOWNLOAD),
    "3dmodel": ("wavefronts", DOWNLOAD),
    "model": ("models", USER_INSTALLS),
    "processingscript": ("scripts", USER_INSTALLS),
    "map": ("map-gallery", VIEW),
    "screenshot": ("screenshots", VIEW),
}
CC0 = "CC0-1.0"


_CODE_EXTENSIONS = frozenset({
    ".model3", ".py", ".pyc", ".pyw", ".pyd", ".js", ".sh", ".bash", ".bat", ".cmd", ".ps1",
    ".vbs", ".exe", ".dll", ".so", ".dylib", ".jar", ".msi", ".app", ".qgs", ".qgz",
})

_DATA_EXTENSIONS = frozenset({
    ".gpkg", ".obj", ".mtl", ".gltf", ".glb", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp",
    ".txt",
})

_SAVED_KINDS = frozenset({".gpkg", ".obj", ".gltf", ".glb"})
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})



_CODE_ACTION_TYPES = frozenset({"0", "1", "2", "3", "4"})
_URL_ACTION_TYPES = frozenset({"5", "6", "7"})


_ZIP_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError, ValueError)
_COPY_CHUNK = 1024 * 1024
_WEB = re.compile(r"^(?:https?|ftp)://", re.IGNORECASE)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_DB_PROVIDERS = frozenset({"postgres", "postgresraster", "mssql", "oracle", "hana", "db2"})

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TIMEOUT_S = 30
_LIST_TOTAL_S = 60
_LIST_MAX_BYTES = 4 * 1024 * 1024
_LIST_CACHE_S = 600


_FILE_MAX_BYTES = 200 * 1024 * 1024
_FILE_TOTAL_S = 300

_TEXT_MAX_BYTES = 20 * 1024 * 1024
_MAX_WORDS = 4
_DESCRIPTION_CHARS = 280
_HUB_TAG = "QGIS Hub"


def register_hub_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="search_qgis_hub",
        danger="read",
        label=QT_TRANSLATE_NOOP("AIAgent", "Search the QGIS Hub[ for {query}]"),
        catalog=True,
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "resource_type": {"type": "string", "enum": sorted(TYPES)},
                "style_type": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": [],
        },
        handler=_search_qgis_hub,
        background=True,
    ))

    registry.register(Tool(
        name="import_qgis_hub_resource",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Import from the QGIS Hub"),
        input_schema={
            "type": "object",
            "properties": {
                "uuid": {"type": "string"},
                "layer": {"type": "string"},
            },
            "required": ["uuid"],
        },
        handler=_import_qgis_hub_resource,
        background=True,
        open_world=True,
    ))





def page_url(type_key: str, name: str) -> str:

    section = TYPES.get(type_key, ("styles", IMPORT))[0]
    return f"{HUB}/{section}/?q={urllib.parse.quote(name)}"


def _type_key(value) -> str:
    return re.sub(r"[\s_-]+", "", str(value or "")).lower()


def _fetch_list(params: dict) -> tuple[list, int]:
    url = _LIST_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    answer = net.fetch(req, timeout=_TIMEOUT_S, max_bytes=_LIST_MAX_BYTES,
                       total_timeout=_LIST_TOTAL_S, cache_ttl=_LIST_CACHE_S)
    doc = json.loads(answer.body.decode("utf-8", "replace"))
    if not isinstance(doc, dict):
        return [], 0
    rows = doc.get("results")
    return (rows if isinstance(rows, list) else []), int(doc.get("total") or 0)


def _row(item: dict) -> dict:
    kind = _type_key(item.get("resource_type"))
    use = TYPES.get(kind, ("", VIEW))[1]
    name = str(item.get("name") or "").strip()
    file_url = str(item.get("file") or "")
    extension = os.path.splitext(urllib.parse.urlsplit(file_url).path)[1].lower()
    if extension in _CODE_EXTENSIONS:
        use = USER_INSTALLS
    description = " ".join(str(item.get("description") or "").split())
    if len(description) > _DESCRIPTION_CHARS:
        description = description[:_DESCRIPTION_CHARS].rstrip() + "..."
    row = {
        "uuid": str(item.get("uuid") or ""),
        "name": name,
        "type": str(item.get("resource_type") or ""),
        "author": " ".join(str(item.get("creator") or "").split()),
        "downloads": int(item.get("download_count") or 0),
        "uploaded": str(item.get("upload_date") or "")[:10],
        "file_type": extension.lstrip("."),
        "description": description,
        "licence": CC0 if kind != "layerdefinition" else "stated on its Hub page (CC0 unless it says otherwise)",
        "page": page_url(kind, name),
        "use": use,
    }
    if item.get("resource_subtypes"):
        row["style_type"] = str(item.get("resource_subtypes"))
    if use in (USER_INSTALLS, VIEW) and file_url.startswith(HUB + "/"):
        row["download_url"] = file_url
    return row


def _search_qgis_hub(args: dict) -> dict:
    query = " ".join(str(args.get("query") or "").split())[:120]
    asked_type = args.get("resource_type")
    kind = _type_key(asked_type)
    if asked_type and kind not in TYPES:
        return tool_error(f"resource_type '{asked_type}' is not a QGIS Hub type.", "INVALID_ARGS",
                          "Valid: " + ", ".join(sorted(TYPES)) + "; optional.")
    style_type = " ".join(str(args.get("style_type") or "").split())
    if style_type:
        kind = "style"
    try:
        limit = max(1, min(20, int(args.get("limit") or 8)))
    except (TypeError, ValueError):
        limit = 8
    params = {"limit": 200}
    if kind:
        params["resource_type"] = kind
    if style_type:
        params["resource_subtypes"] = style_type
    try:
        scored: dict[str, tuple[float, dict]] = {}
        words = [w for w in re.split(r"\W+", query) if len(w) > 1][:_MAX_WORDS]
        rows, total = _fetch_list({**params, "keyword": query} if query else params)
        every_word = True
        for item in rows:
            scored[str(item.get("uuid"))] = (1.0, item)




        if not rows and len(words) > 1:
            every_word = False
            for word in words:
                found, _ = _fetch_list({**params, "keyword": word})
                weight = 1.0 / max(1, len(found))
                for item in found:
                    key = str(item.get("uuid"))
                    score, _item = scored.get(key, (0.0, item))
                    scored[key] = (score + weight, item)
            total = len(scored)
    except net.FetchCancelled:
        return tool_error("The run was stopped.", "CANCELLED", "The user stopped the run.")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return tool_error(f"The QGIS Hub did not answer: {net.describe_failure(exc) or exc}",
                          "NETWORK_ERROR", net.NETWORK_SUGGESTION)
    ranked = sorted(scored.values(), key=lambda pair: (-pair[0], -int(pair[1].get("download_count") or 0)))
    results = [_row(item) for _hits, item in ranked[:limit]]
    out = {
        "source": "QGIS Hub (hub.qgis.org)",
        "query": query,
        "resource_type": kind or "any",
        "found": total,
        "results": results,
    }
    if style_type:
        out["style_type"] = style_type
    if query and results and not every_word:
        out["matched"] = ("No resource matched every word, so these match some of them, the rarest words "
                          "first.")
    if not results:
        out["note"] = ("Nothing on the Hub matches. The Hub matches whole English words in names and "
                       "descriptions: try one broader subject word, or no resource_type.")
    uses = {row["use"] for row in results}
    how = {}
    if IMPORT in uses:
        how[IMPORT] = ("import_qgis_hub_resource with the uuid: a style lands in the Style Manager library "
                       "tagged 'QGIS Hub', a layer definition adds its layers to the project.")
    if DOWNLOAD in uses:
        how[DOWNLOAD] = ("import_qgis_hub_resource saves the file next to the project and returns its path; "
                         "add_data loads a GeoPackage from there.")
    if USER_INSTALLS in uses:
        how[USER_INSTALLS] = ("Executable code (a Processing model or script): the user installs it, not the "
                              "agent. They download it from its page (author and licence there) and add it from the "
                              "Processing Toolbox (the Models or Scripts button, 'Add Model/Script to Toolbox').")
    if VIEW in uses:
        how[VIEW] = "A picture of a map: nothing to import."
    if how:
        out["how_to_use"] = how
    return out





def _import_qgis_hub_resource(args: dict) -> dict:
    uuid = str(args.get("uuid") or "").strip().lower()
    if not _UUID.match(uuid):
        return tool_error("uuid is not a QGIS Hub resource id.", "INVALID_ARGS",
                          "search_qgis_hub answers each result with a uuid.")
    try:
        meta = _lookup(uuid)
    except net.FetchCancelled:
        return tool_error("The run was stopped.", "CANCELLED", "The user stopped the run.")
    except (urllib.error.URLError, OSError, ValueError):
        meta = {}
    kind = _type_key(meta.get("resource_type"))
    if TYPES.get(kind, ("", ""))[1] == USER_INSTALLS:

        return _user_installs(meta, kind, [])
    req = urllib.request.Request(_DOWNLOAD_URL.format(uuid=uuid), headers={"User-Agent": _USER_AGENT})
    try:
        answer = net.fetch(req, timeout=_TIMEOUT_S, max_bytes=_FILE_MAX_BYTES, total_timeout=_FILE_TOTAL_S)
    except net.FetchCancelled:
        return tool_error("The run was stopped.", "CANCELLED", "The user stopped the run.")
    except net.FetchTooLarge:
        return tool_error("This Hub item is larger than one download may be.", "INVALID_ARGS",
                          "Its Hub page offers a direct download.")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return tool_error("No approved QGIS Hub resource has this uuid.", "NOT_FOUND",
                              "search_qgis_hub answers current uuids.")
        return tool_error(f"HTTP {exc.code} from the QGIS Hub.", "HTTP_ERROR", "Usually brief.")
    except (urllib.error.URLError, OSError) as exc:
        return tool_error(f"The QGIS Hub did not answer: {net.describe_failure(exc) or exc}",
                          "NETWORK_ERROR", net.NETWORK_SUGGESTION)
    label = str(meta.get("name") or "").strip() or _label_from(answer.headers.get("content-disposition"), uuid)
    try:
        archive = zipfile.ZipFile(io.BytesIO(answer.body))


        members = [info for info in archive.infolist() if not info.is_dir()
                   and not info.filename.replace("\\", "/").startswith("__MACOSX/")
                   and not os.path.basename(info.filename.replace("\\", "/")).startswith("._")]
    except (zipfile.BadZipFile, ValueError):
        return tool_error("The QGIS Hub sent something that is not its usual zip.", "EXECUTION_FAILED",
                          "The item is on its Hub page.")
    extensions = {os.path.splitext(info.filename)[1].lower() for info in members}
    code = sorted(extensions & _CODE_EXTENSIONS)
    if code:
        return _user_installs({**meta, "name": label}, kind or ("model" if ".model3" in code else "processingscript"),
                              code)
    handlers = {".xml": _import_style, ".gpl": _import_palette,
                ".qlr": lambda data, *rest: _import_layer_definition(data, *rest, archive=archive, members=members)}



    order = [".qlr", ".xml", ".gpl"] if kind == "layerdefinition" else [".xml", ".gpl", ".qlr"]
    candidates = sorted((info for info in members if os.path.splitext(info.filename)[1].lower() in handlers),
                        key=lambda info: order.index(os.path.splitext(info.filename)[1].lower()))
    if extensions & _SAVED_KINDS and not (kind == "layerdefinition" and any(
            info.filename.lower().endswith(".qlr") for info in candidates)):
        return _save_to_disk(archive, members, label, uuid)
    first_error = None
    for member in candidates:
        extension = os.path.splitext(member.filename)[1].lower()
        try:
            data = _read_member(archive, member, _TEXT_MAX_BYTES)
        except _TooLarge:
            out = tool_error(f"The {extension} file is over {_TEXT_MAX_BYTES // (1024 * 1024)} MB.",
                             "INVALID_ARGS", "The item is on its Hub page.")
        except _ZIP_ERRORS as exc:
            out = tool_error(f"The {extension} file in this Hub zip does not unpack ({exc}).", "EXECUTION_FAILED",
                             "The item is on its Hub page.")
        else:
            out = handlers[extension](data, label, uuid, args)
        if not out.get("_error"):
            return out
        first_error = first_error or out
    if first_error:
        return first_error
    if extensions and extensions <= _IMAGE_EXTENSIONS:
        return {"imported": False, "name": label, "kind": "image",
                "tell_user": f"'{label}' is a picture of a map on the QGIS Hub; there is nothing to import.",
                "page": page_url(kind or "map", label)}
    return tool_error(f"This Hub item holds files QGIS does not import this way ({', '.join(sorted(extensions))}).",
                      "INVALID_ARGS", "The item is on its Hub page.")


def _label_from(disposition, uuid: str) -> str:

    name = urllib.parse.unquote(http_headers.disposition_filename(disposition))
    if name.lower().endswith(".zip"):
        name = name[:-len(".zip")]
    name = name.replace("-", " ").strip()
    return name or f"QGIS Hub {uuid[:8]}"


def _lookup(uuid: str) -> dict:





    rows, _total = _fetch_list({"limit": 1000})
    return next((item for item in rows if str(item.get("uuid") or "").lower() == uuid), {})


def _user_installs(meta: dict, kind: str, code: list) -> dict:
    label = str(meta.get("name") or "this item")
    button = "Models" if kind == "model" else "Scripts"
    out = {
        "imported": False,
        "name": label,
        "kind": "executable",
        "licence": CC0,
        "page": page_url(kind, label),
        "tell_user": (f"'{label}' is code that runs inside QGIS, so the agent does not install it. The user "
                      "checks the author on its page, downloads it and adds it from the Processing Toolbox "
                      f"(the {button} button, 'Add {button[:-1]} to Toolbox')."),
        "next_step": "Nothing was written.",
    }
    author = " ".join(str(meta.get("creator") or "").split())
    if author:
        out["author"] = author
    if code:
        out["files"] = code
    return out


def _style_entities():
    from qgis.core import QgsStyle

    def entity(member):
        return enum_member(QgsStyle, "StyleEntity", member)


    return (
        ("symbols", entity("SymbolEntity"), "symbol", "addSymbol"),
        ("color_ramps", entity("ColorrampEntity"), "colorRamp", "addColorRamp"),
        ("text_formats", entity("TextFormatEntity"), "textFormat", "addTextFormat"),
        ("label_settings", entity("LabelSettingsEntity"), "labelSettings", "addLabelSettings"),
        ("legend_patch_shapes", entity("LegendPatchShapeEntity"), "legendPatchShape", "addLegendPatchShape"),
        ("symbols_3d", entity("Symbol3DEntity"), "symbol3D", "addSymbol3D"),
    )


def _free_name(target, entity, name: str) -> str:





    taken = set(target.allNames(entity))
    candidate, n = name, 1
    while candidate in taken and _HUB_TAG not in target.tagsOfSymbol(entity, candidate):
        n += 1
        candidate = f"{name} ({_HUB_TAG})" if n == 2 else f"{name} ({_HUB_TAG} {n - 1})"
    return candidate


def _valid(value) -> bool:

    if value is None:
        return False
    method = getattr(value, "isValid", None)
    if callable(method):
        return bool(method())
    method = getattr(value, "isNull", None)
    return not method() if callable(method) else True


def _import_style(data: bytes, label: str, uuid: str, args: dict) -> dict:


    code = layer_definition_code(data)
    if code == [_UNREADABLE]:
        return tool_error(f"'{label}' is not a style QGIS can read: {_UNREADABLE}.", "EXECUTION_FAILED",
                          "Other results vary.")
    if code:
        return _refused(label, "style", code)
    folder = create_managed_temp_dir("hub")
    path = os.path.join(folder, "style.xml")
    with open(path, "wb") as fh:
        fh.write(data)
    layer_arg = str(args.get("layer") or "").strip()

    def _on_main():
        from qgis.core import QgsStyle

        source = QgsStyle()
        source.createMemoryDatabase()
        if not source.importXml(path):
            return {"_error": source.errorString() or "not a QGIS style file"}
        target = QgsStyle.defaultStyle()
        added: dict[str, list] = {}
        renamed = []
        for key, entity, getter, adder in _style_entities():
            for name in source.allNames(entity):
                item = getattr(source, getter)(name)
                if not _valid(item):
                    continue
                final = _free_name(target, entity, name)
                if not getattr(target, adder)(final, item, True):
                    continue
                tags = [t for t in source.tagsOfSymbol(entity, name) if t] + [_HUB_TAG]
                target.tagSymbol(entity, final, tags)
                added.setdefault(key, []).append(final)
                if final != name:
                    renamed.append({"from": name, "to": final})
        applied = _apply_symbol(target, added.get("symbols") or [], layer_arg) if layer_arg else None
        return {"added": added, "renamed": renamed, "applied": applied}

    try:
        done = run_on_main_thread(_on_main, timeout=60)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    if done.get("_error"):
        return tool_error(f"'{label}' is not a style QGIS can read: {done['_error']}", "EXECUTION_FAILED",
                          "Other results vary.")
    added = done["added"]
    if not added:
        return tool_error(f"'{label}' holds no symbol, color ramp or label style.", "EXECUTION_FAILED",
                          "Other results vary.")
    log(f"QGIS Hub: imported {label} ({uuid}) into the style library: {added}")
    count = sum(len(v) for v in added.values())
    what = "1 item" if count == 1 else f"{count} items"
    out = {
        "imported": True,
        "name": label,
        "kind": "style",
        "added": added,
        "tag": _HUB_TAG,
        "licence": CC0,
        "tell_user": (f"{what} from '{label}' {'is' if count == 1 else 'are'} now in your style library "
                      f"(Settings > Style Manager, tag '{_HUB_TAG}'), ready in every symbol picker."),
    }
    if done["renamed"]:
        out["renamed"] = done["renamed"]
        out["note"] = "Names you already had were kept; the Hub copies got a suffix."
    if done.get("applied"):
        out["applied"] = done["applied"]
    return out


def _enum_int(value) -> int:
    return int(getattr(value, "value", value))


def _apply_symbol(style, names: list, layer_arg: str) -> dict:

    from qgis.core import QgsSingleSymbolRenderer, QgsVectorLayer

    from ._layers import resolve_layer

    layer = resolve_layer(layer_arg)
    if not isinstance(layer, QgsVectorLayer):
        return {"done": False, "why": f"No vector layer '{layer_arg}' in the project."}


    wanted = _enum_int(layer.geometryType())
    for name in names:
        symbol = style.symbol(name)
        if symbol is not None and _enum_int(symbol.type()) == wanted:
            layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            layer.triggerRepaint()
            layer.emitStyleChanged()
            return {"done": True, "layer": layer.name(), "symbol": name}
    return {"done": False, "layer": layer.name(),
            "why": "None of the imported symbols is drawn for this layer's geometry."}


def parse_gpl(text: str) -> list:

    colors = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and all(p.isdigit() for p in parts[:3]):
            r, g, b = (min(255, int(p)) for p in parts[:3])
            colors.append((r, g, b))
    return colors


def _import_palette(data: bytes, label: str, uuid: str, args: dict) -> dict:
    text = data.decode("utf-8", "replace")
    if not text.lstrip().startswith("GIMP Palette"):
        return tool_error(f"'{label}' is not a GIMP palette.", "EXECUTION_FAILED", "Other results vary.")
    colors = parse_gpl(text)
    if not colors:
        return tool_error(f"'{label}' holds no colors.", "EXECUTION_FAILED", "Other results vary.")

    def _on_main():
        from qgis.core import QgsPresetSchemeColorRamp, QgsStyle
        from qgis.PyQt.QtGui import QColor

        target = QgsStyle.defaultStyle()
        entity = enum_member(QgsStyle, "StyleEntity", "ColorrampEntity")
        final = _free_name(target, entity, label)
        ramp = QgsPresetSchemeColorRamp([QColor(r, g, b) for r, g, b in colors])
        if not target.addColorRamp(final, ramp, True):
            return None
        target.tagSymbol(entity, final, [_HUB_TAG])
        return final

    final = run_on_main_thread(_on_main, timeout=30)
    if not final:
        return tool_error(f"QGIS refused the palette '{label}'.", "EXECUTION_FAILED", "Other results vary.")
    return {
        "imported": True, "name": label, "kind": "palette", "added": {"color_ramps": [final]},
        "colors": len(colors), "tag": _HUB_TAG, "licence": CC0,
        "tell_user": (f"The palette '{final}' ({len(colors)} colors) is a color ramp in your style library "
                      f"(tag '{_HUB_TAG}')."),
    }


_UNREADABLE = "unreadable XML"

_CLOUD_VSI = frozenset({"/vsis3/", "/vsigs/", "/vsiaz/", "/vsiadls/", "/vsioss/", "/vsiswift/", "/vsiwebhdfs/",
                        "/vsihdfs/"})


class _TooLarge(Exception):
    pass


class _Unsafe(Exception):
    pass


def _has_text(element) -> bool:
    return bool((element.text or "").strip())


def _python_lines(text: str) -> bool:

    return any(line.strip() and not line.strip().startswith("#") for line in text.splitlines())


def _network_share(value) -> bool:




    value = str(value or "").strip().lstrip("'\"").lower()
    if value.startswith("file://"):
        return not value.startswith("file:///")
    return value.startswith(("\\\\", "//"))


def _web_view_widget(element) -> bool:

    kind = element.get("type")
    if kind == "WebView":
        return True
    if kind != "ExternalResource":
        return False

    return any((option.get("name") == "DocumentViewer" and str(option.get("value")) == "2")
               or option.get("DocumentViewer") == "2" for option in element.iter())


def layer_definition_code(data: bytes) -> list:









    if b"<!ENTITY" in data:
        return [_UNREADABLE]
    try:
        root = ET.fromstring(data)  # nosec B314
    except ET.ParseError:
        return [_UNREADABLE]
    found = []
    for element in root.iter():
        tag = element.tag




        if tag == "editforminit" and _has_text(element):
            found.append(f"form init Python function '{element.text.strip()}'")
        elif tag == "actionsetting":
            kind = str(element.get("type", "")).strip()
            name = element.get("name") or element.get("shortTitle") or "?"
            if kind in _CODE_ACTION_TYPES:
                found.append(f"action '{name}' runs a command")
            elif kind in _URL_ACTION_TYPES and not _WEB.match(str(element.get("action") or "").strip()):
                found.append(f"action '{name}' opens something other than a web address")
        elif tag == "attributeEditorHtmlElement":
            found.append("HTML form widget")
        elif tag == "attributeEditorQmlElement":
            found.append("QML form widget")
        elif tag == "editWidget" and _web_view_widget(element):
            found.append("web view field widget")
        elif tag == "mapTip" and _has_text(element):
            found.append("map tip (HTML shown on hover)")
        elif tag == "pythonCode" and _python_lines(element.text or ""):
            found.append("stored Python (macros or expression functions)")
        elif tag == "maplayer" and element.get("type") == "plugin":
            found.append(f"plugin layer '{(element.findtext('layername') or '?').strip()}'")
        if element.get("embedded") == "1":
            found.append("layers embedded from another project file")
        if any(_network_share(value) for value in [*element.attrib.values(), element.text]):
            found.append("a path on a network share")
    return sorted(set(found))


def _refused(label: str, type_key: str, found: list) -> dict:
    return {
        "imported": False, "name": label, "kind": "executable", "files": found,
        "page": page_url(type_key, label),
        "tell_user": (f"'{label}' carries something QGIS would run or open on its own ({'; '.join(found)}), so "
                      "the agent does not load it. The user can review it on its page and add it themselves."),
        "next_step": "Nothing was loaded.",
    }





def layer_sources(data: bytes) -> list:

    root = ET.fromstring(data)  # nosec B314
    out = []
    for layer in root.iter("maplayer"):
        source = (layer.findtext("datasource") or "").strip()
        if not source:
            continue
        provider = (layer.findtext("provider") or "").strip()
        if not provider and layer.get("type") == "vector-tile":
            provider = "vectortile"
        out.append({"layer": (layer.findtext("layername") or "?").strip(), "provider": provider, "source": source})
    for form in root.iter("editform"):
        if _has_text(form):
            out.append({"layer": "custom form", "provider": "", "source": form.text.strip(), "file": True})
    return out


def _decode_sources(sources: list) -> list:

    from qgis.core import QgsProviderRegistry

    registry = QgsProviderRegistry.instance()

    def parts(provider: str, source: str) -> dict:
        if provider == "virtual":
            from qgis.core import QgsVirtualLayerDefinition
            from qgis.PyQt.QtCore import QUrl

            definition = QgsVirtualLayerDefinition.fromUrl(QUrl.fromEncoded(source.encode("utf-8")))
            return {"layers": [parts(item.provider(), item.source()) for item in definition.sourceLayers()
                               if not item.isReferenced()]}
        try:
            decoded = dict(registry.decodeUri(provider, source) or {}) if provider else {}
        except Exception:  # noqa: BLE001
            decoded = {}
        kept = {key: str(decoded[key]) for key in ("path", "url", "styleUrl", "host", "service", "vsiPrefix")
                if decoded.get(key)}
        return kept or {"raw": source}

    return [{**entry, "parts": {"path": entry["source"]} if entry.get("file") else parts(entry["provider"],
                                                                                          entry["source"])}
            for entry in sources]


def _inside(path: str, folder: str) -> bool:
    try:
        base = os.path.normcase(os.path.abspath(folder))
        return os.path.normcase(os.path.commonpath([os.path.abspath(path), base])) == base
    except ValueError:
        return False


def _place(value: str, folder: str | None) -> tuple[str, str]:

    value = value.strip()
    if _network_share(value):
        return "refuse", f"a network share ({value})"
    if _WEB.match(value):
        return "host", urllib.parse.urlsplit(value).hostname or value
    if value.lower().startswith("file:"):
        value = urllib.request.url2pathname(urllib.parse.urlsplit(value).path)
    elif _SCHEME.match(value):
        return "host", urllib.parse.urlsplit(value).hostname or value.split("://", 1)[0]
    if re.match(r"^/[A-Za-z]:[/\\]", value):
        value = value[1:]
    if folder:
        full = os.path.abspath(os.path.join(folder, value))
        if _inside(full, folder):
            return "file", full
    return "refuse", f"a file on this computer ({value})"


def _check_sources(decoded: list, folder: str | None) -> tuple[list, list, list]:

    hosts, files, refused = set(), set(), []
    for entry in decoded:
        provider, what = entry["provider"], f"layer '{entry['layer']}'"
        if provider in ("memory", "mesh_memory"):
            continue
        parts = entry["parts"]
        for part in parts.get("layers") or [parts]:
            if part.get("host"):
                hosts.add(part["host"])
            elif provider in _DB_PROVIDERS:
                hosts.add(f"database service '{part['service']}'" if part.get("service")
                          else "a database on this computer")
            if part.get("vsiPrefix") in _CLOUD_VSI:
                hosts.add(f"cloud storage ({part['vsiPrefix'].strip('/')})")
                continue
            values = [part[key] for key in ("path", "url", "styleUrl") if part.get(key)]
            if part.get("raw") and provider not in _DB_PROVIDERS:
                urls = re.findall(r"(?:https?|ftp)://[^\s'\"&|]+", urllib.parse.unquote(part["raw"]))
                values = urls or [part["raw"]]
            for value in values:
                kind, name = _place(value, folder)
                if kind == "host":
                    hosts.add(name)
                elif kind == "file":
                    files.add(name)
                else:
                    refused.append(f"{what} reads {name}")
    return sorted(hosts), sorted(files), sorted(set(refused))


def _import_layer_definition(data: bytes, label: str, uuid: str, args: dict, archive=None, members=()) -> dict:
    code = layer_definition_code(data)
    if code == [_UNREADABLE]:
        return tool_error(f"'{label}' is not a layer definition QGIS can read.", "EXECUTION_FAILED",
                          "Other results vary.")
    if code:
        return _refused(label, "layerdefinition", code)
    sources = layer_sources(data)


    folder, saved = None, {}
    keep = [info for info in members if os.path.splitext(info.filename)[1].lower() in _DATA_EXTENSIONS]
    if archive is not None and keep:
        saved = _write_members(archive, keep, label, uuid)
        if saved.get("_error"):
            return saved
        folder = saved["folder"]
    decoded = run_on_main_thread(_decode_sources, sources, timeout=30)
    hosts, files, refused = _check_sources(decoded, folder)
    if refused:
        if folder:
            remove_tree(folder)
        return _refused(label, "layerdefinition", refused)
    text = data.decode("utf-8", "replace")

    def _on_main():
        from qgis.core import QgsLayerDefinition, QgsPathResolver, QgsProject, QgsReadWriteContext
        from qgis.PyQt.QtXml import QDomDocument

        doc = QDomDocument()
        doc.setContent(text)
        if doc.documentElement().isNull():
            return {"_error": "not XML"}
        context = QgsReadWriteContext()
        if folder:
            context.setPathResolver(QgsPathResolver(os.path.join(folder, "layers.qlr")))
        project = QgsProject.instance()
        before = set(project.mapLayers())
        ok, message = QgsLayerDefinition.loadLayerDefinition(doc, project, project.layerTreeRoot(), context)
        layers = [layer for lid, layer in project.mapLayers().items() if lid not in before]
        return {"ok": ok, "message": message,
                "layers": [{"name": layer.name(), "id": layer.id(), "valid": layer.isValid(),
                            "provider": layer.providerType()} for layer in layers]}

    done = run_on_main_thread(_on_main, timeout=60)
    if done.get("_error") or not done.get("ok"):
        if folder:
            remove_tree(folder)
        return tool_error(f"QGIS could not load '{label}': {done.get('_error') or done.get('message')}",
                          "EXECUTION_FAILED", "Other results vary.")
    layers = done["layers"]
    out = {"imported": True, "name": label, "kind": "layer_definition", "layers": layers,
           "sources": {"hosts": hosts, "files": files},
           "licence": "stated on its Hub page (CC0 unless it says otherwise)",
           "page": page_url("layerdefinition", label)}
    if folder:
        out["folder"] = folder
        _note_cleaned(out, saved)
    broken = [layer["name"] for layer in layers if not layer["valid"]]
    if broken:
        out["warning"] = ("These layers point at data that does not open here (a service that is down, or one "
                          "that needs an account): " + ", ".join(broken))
    return out





def _copy_bounded(src, dst, budget: int) -> int:




    cancelled = net.current_cancel_check()
    copied = 0
    while True:
        chunk = src.read(_COPY_CHUNK)
        if not chunk:
            return copied
        copied += len(chunk)
        if copied > budget:
            raise _TooLarge
        if cancelled is not None and cancelled():
            raise net.FetchCancelled("The run was stopped.")
        dst.write(chunk)


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    if info.file_size > cap:
        raise _TooLarge
    buffer = io.BytesIO()
    with archive.open(info) as src:
        _copy_bounded(src, buffer, cap)
    return buffer.getvalue()


def _as_bytes(value) -> bytes:
    return value if isinstance(value, bytes) else str(value or "").encode("utf-8")


def _style_code(qml, sld) -> list:

    found = layer_definition_code(_as_bytes(qml)) if qml else []
    if sld:
        found += [item for item in layer_definition_code(_as_bytes(sld)) if item != _UNREADABLE]
    return found


def _project_code(content) -> list:




    if isinstance(content, str):
        try:
            content = bytes.fromhex(content)
        except ValueError:
            return [_UNREADABLE]
    try:
        with zipfile.ZipFile(io.BytesIO(_as_bytes(content))) as project:
            qgs = next((info for info in project.infolist() if info.filename.lower().endswith(".qgs")), None)
            return layer_definition_code(_read_member(project, qgs, _TEXT_MAX_BYTES)) if qgs else [_UNREADABLE]
    except (_TooLarge, *_ZIP_ERRORS):
        return [_UNREADABLE]


def _clean_geopackage(path: str) -> dict:









    removed, projects = [], []
    try:
        with closing(sqlite3.connect(path)) as con:
            con.execute("PRAGMA trusted_schema=OFF")
            tables = {str(row[0]).lower() for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
            if "layer_styles" in tables:
                query = "SELECT rowid, f_table_name, styleName, styleQML, styleSLD FROM layer_styles"
                for rowid, table, name, qml, sld in con.execute(query).fetchall():
                    found = _style_code(qml, sld)
                    if found:
                        con.execute("DELETE FROM layer_styles WHERE rowid = ?", (rowid,))
                        removed.append({"layer": str(table), "style": str(name), "carried": found})
                con.commit()
                if removed and any(_style_code(qml, sld) for _rowid, _t, _n, qml, sld in con.execute(query)):
                    raise _Unsafe("a layer style that carries code came back after it was removed")
            if "qgis_projects" in tables:
                for name, content in con.execute("SELECT name, content FROM qgis_projects").fetchall():
                    projects.append({"name": str(name), "holds": _project_code(content)})
    except sqlite3.Error as exc:
        raise _Unsafe(f"it cannot be read as a GeoPackage ({exc})") from exc
    return {"styles_removed": removed, "projects": projects}


def _note_cleaned(out: dict, saved: dict):
    if saved.get("styles_removed"):
        out["styles_removed"] = saved["styles_removed"]
        out["note"] = ("Stored layer styles that carried code were removed before saving; the data and the other "
                       "styles are as the author shared them.")
    if saved.get("projects"):
        out["projects"] = saved["projects"]
        out["projects_note"] = ("QGIS projects stored in the GeoPackage were kept for the user to open from the "
                                "Browser panel; the agent does not open them. 'holds' names what in each would run "
                                "or open on its own.")


def _write_members(archive: zipfile.ZipFile, keep: list, label: str, uuid: str) -> dict:






    if sum(info.file_size for info in keep) > _FILE_MAX_BYTES:
        return tool_error("This Hub item unpacks to more than one download may be.", "INVALID_ARGS",
                          "Its Hub page offers a direct download.")
    base = run_on_main_thread(default_folder, timeout=10)
    stem = safe_file_name(label, fallback=uuid[:8])[:60].strip(" .") or uuid[:8]
    folder = os.path.join(base, _HUB_TAG, stem)


    if len(folder) > 160:
        folder = os.path.join(base, _HUB_TAG, uuid[:8])
    if len(folder) > 160:



        folder = os.path.join(run_on_main_thread(exports_folder, timeout=10), _HUB_TAG, stem)
    candidate, n = folder, 1
    while os.path.exists(candidate):
        n += 1
        candidate = f"{folder} {n}"
    folder = candidate
    os.makedirs(folder, exist_ok=True)
    saved, used, left = [], set(), _FILE_MAX_BYTES
    removed, projects = [], []
    try:
        for info in keep:
            stem_name, extension = os.path.splitext(os.path.basename(info.filename.replace("\\", "/")))
            name = safe_file_name(stem_name, fallback="item")[:80] + extension.lower()
            while name.lower() in used:
                name = f"{os.path.splitext(name)[0]}_{len(used)}{extension.lower()}"
            used.add(name.lower())
            path = os.path.join(folder, name)
            part = path + ".part"
            with archive.open(info) as src, open(part, "wb") as dst:
                left -= _copy_bounded(src, dst, left)
            if extension.lower() == ".gpkg":
                cleaned = _clean_geopackage(part)
                removed += [{**row, "file": name} for row in cleaned["styles_removed"]]
                projects += [{**row, "file": name} for row in cleaned["projects"]]
            os.replace(part, path)
            saved.append(path)
    except _TooLarge:
        remove_tree(folder)
        return tool_error("This Hub item unpacks to more than one download may be.", "INVALID_ARGS",
                          "Its Hub page offers a direct download.")
    except net.FetchCancelled:
        remove_tree(folder)
        return tool_error("The run was stopped.", "CANCELLED", "The user stopped the run.")
    except _Unsafe as exc:
        remove_tree(folder)
        return tool_error(f"'{label}' was not saved: {exc}.", "EXECUTION_FAILED", "The item is on its Hub page.")
    except (*_ZIP_ERRORS, OSError) as exc:
        remove_tree(folder)
        return tool_error(f"This Hub zip does not unpack ({exc}).", "EXECUTION_FAILED",
                          "The item is on its Hub page.")
    return {"folder": folder, "files": saved, "styles_removed": removed, "projects": projects}


def _save_to_disk(archive: zipfile.ZipFile, members: list, label: str, uuid: str) -> dict:
    keep = [info for info in members if os.path.splitext(info.filename)[1].lower() in _DATA_EXTENSIONS]
    written = _write_members(archive, keep, label, uuid) if keep else {"files": []}
    if written.get("_error"):
        return written
    saved = written["files"]
    if not saved:
        if written.get("folder"):
            remove_tree(written["folder"])
        return tool_error("This Hub item holds no file QGIS reads.", "INVALID_ARGS", "The item is on its Hub page.")
    gpkg = [p for p in saved if p.lower().endswith(".gpkg")]
    model = [p for p in saved if p.lower().endswith((".obj", ".gltf", ".glb"))]
    out = {"imported": True, "name": label, "kind": "geopackage" if gpkg else "3d_model",
           "folder": written["folder"], "files": saved, "licence": CC0}
    _note_cleaned(out, written)
    if gpkg:
        out["next_step"] = "add_data with the .gpkg path loads its layers."
    elif model:
        out["next_step"] = ("The model is a file on disk: a 3D point symbol uses it (layer styling, 3D View, "
                            "Model, with the .obj path).")
    return out

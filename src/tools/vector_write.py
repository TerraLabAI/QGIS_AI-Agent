# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later


























from __future__ import annotations

import os
import re
import unicodedata

from ..core.tool_registry import coded_fact



SHAPEFILE_NAME_MAX = 10



_FILE_SUFFIXES = frozenset({
    ".shp", ".dbf", ".gpkg", ".geojson", ".json", ".kml", ".kmz", ".gml", ".gpx",
    ".sqlite", ".db", ".csv", ".tab", ".mif", ".mid", ".fgb", ".parquet", ".dxf",
})


_CAPABILITY_FOR = {
    "add features": ("AddFeatures",),
    "delete features": ("DeleteFeatures",),
    "change attributes": ("ChangeAttributeValues",),
    "change geometries": ("ChangeGeometries",),
    "add fields": ("AddAttributes",),
    "edit": ("AddFeatures", "DeleteFeatures", "ChangeAttributeValues", "ChangeGeometries", "AddAttributes"),
}


def _log(message: str) -> None:
    try:
        from qgis.core import Qgis, QgsMessageLog

        QgsMessageLog.logMessage(message, "AI Agent", level=Qgis.MessageLevel.Warning)
    except Exception:  # nosec B110
        pass


def _fold(text: str) -> str:

    stripped = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in stripped if not unicodedata.combining(ch)).lower()






def storage_facts(layer) -> dict:

    facts = {"provider": "", "storage": "", "path": "", "read_only": False}
    try:
        facts["provider"] = str(layer.providerType() or "")
    except Exception as exc:  # noqa: BLE001
        _log(f"storage_facts: providerType() failed: {exc}")
    try:
        facts["read_only"] = bool(layer.readOnly())
    except Exception as exc:  # noqa: BLE001
        _log(f"storage_facts: readOnly() failed: {exc}")
    provider = None
    try:
        provider = layer.dataProvider()
    except Exception:  # noqa: BLE001
        provider = None
    if provider is not None:
        try:
            facts["storage"] = str(provider.storageType() or "")
        except Exception as exc:  # noqa: BLE001
            _log(f"storage_facts: storageType() failed: {exc}")
    facts["path"] = source_path(layer)
    return facts


def source_path(layer) -> str:





    try:
        source = str(layer.source() or "")
    except Exception:  # noqa: BLE001
        return ""
    if not source:
        return ""
    if source.lower().startswith(("http://", "https://", "ftp://", "/vsi")):
        return ""
    candidate = source.split("|", 1)[0].split("?", 1)[0].strip().strip('"')
    if candidate.startswith("file://"):
        candidate = candidate[7:]
        if re.match(r"^/[A-Za-z]:", candidate):
            candidate = candidate[1:]
    if not candidate:
        return ""
    try:
        if os.path.exists(candidate):
            return candidate
    except Exception:  # noqa: BLE001
        return ""


    if "=" in candidate:
        return ""
    return candidate if os.path.splitext(candidate)[1].lower() in _FILE_SUFFIXES else ""


def is_shapefile(layer) -> bool:
    facts = storage_facts(layer)
    if "shapefile" in _fold(facts["storage"]) or "dbf" in _fold(facts["storage"]):
        return True
    return facts["path"].lower().endswith((".shp", ".dbf"))


def field_name_limit(layer) -> int | None:

    return SHAPEFILE_NAME_MAX if is_shapefile(layer) else None


def _companions(path: str) -> list[str]:

    if not path:
        return []
    stem, ext = os.path.splitext(path)
    if ext.lower() != ".shp":
        return [path]

    return [path] + [stem + suffix for suffix in (".dbf", ".shx")]


def _write_block(path: str) -> tuple[str, str]:






    if not path:
        return "", ""
    for candidate in _companions(path):
        try:
            if not os.path.exists(candidate):


                if candidate == path:
                    return "missing", candidate
                continue
            if not os.access(candidate, os.W_OK):
                return "read_only_file", candidate
            handle = open(candidate, "r+b")  # noqa: SIM115
            handle.close()
        except PermissionError:
            return "locked", candidate
        except OSError:
            return "unreadable", candidate
    folder = os.path.dirname(path)
    try:
        if folder and not os.access(folder, os.W_OK):
            return "folder_read_only", folder
    except Exception as exc:  # noqa: BLE001
        _log(f"blocker check: os.access on {folder!r} failed: {exc}")
    return "", ""


def _looks_remote(path: str) -> bool:

    if not path:
        return False
    if path.startswith("\\\\") or path.startswith("//"):
        return True
    drive = os.path.splitdrive(path)[0].rstrip(":").upper()
    system = os.path.splitdrive(os.path.expanduser("~"))[0].rstrip(":").upper()
    return bool(drive) and bool(system) and drive != system


def _capability_names(layer) -> set:

    names = set()
    try:
        from qgis.core import QgsVectorDataProvider

        caps = layer.dataProvider().capabilities()
    except Exception:  # noqa: BLE001
        return names
    for name in ("AddFeatures", "DeleteFeatures", "ChangeAttributeValues",
                 "ChangeGeometries", "AddAttributes", "DeleteAttributes", "RenameAttributes"):
        flag = getattr(QgsVectorDataProvider, name, None)
        try:
            if flag is not None and (caps & flag):
                names.add(name)
        except Exception as exc:  # noqa: BLE001
            _log(f"_capability_names: checking {name!r} failed: {exc}")
            continue
    return names






def cannot_edit_error(layer, action: str = "edit") -> dict:





    facts = storage_facts(layer)
    name = ""
    try:
        name = layer.name()
    except Exception:  # noqa: BLE001
        name = "this layer"
    where = f" ({facts['path']})" if facts["path"] else ""
    detail = {"layer": name, "provider": facts["provider"], "storage": facts["storage"],
              "path": facts["path"], "code": "EXECUTION_FAILED"}

    if facts["read_only"]:
        detail["_error"] = (f"Layer {name!r} is marked read-only in this project, so no edit session can open on it.")
        detail["reason"] = "layer_read_only"
        detail.update(coded_fact(hint="edit_blocked", variant="layer_read_only"))
        return detail

    have = _capability_names(layer)
    wanted = _CAPABILITY_FOR.get(action, _CAPABILITY_FOR["edit"])
    if have and not have.intersection(wanted):
        detail["_error"] = (f"The {facts['provider'] or 'data'} provider behind {name!r} cannot {action}"
                            f"{(' (' + facts['storage'] + ')') if facts['storage'] else ''}; it serves "
                            "this layer for reading only.")
        detail["reason"] = "provider_read_only"
        detail["capabilities"] = sorted(have)
        detail.update(coded_fact(hint="edit_blocked", variant="provider_read_only"))
        return detail

    reason, blocker = _write_block(facts["path"])
    if reason == "missing":
        detail["_error"] = (f"The file {name!r} reads is not there any more: {blocker}.")
        detail["reason"] = "source_missing"
        detail.update(coded_fact(hint="edit_blocked", variant="source_missing"))
        return detail
    if reason == "locked":
        detail["_error"] = (f"The file behind {name!r} is open in another program, so QGIS cannot write to it: "
                            f"{blocker}.")
        detail["reason"] = "file_locked"
        detail.update(coded_fact(hint="edit_blocked",
                                 variant="file_locked_remote" if _looks_remote(facts["path"]) else "file_locked"))
        return detail
    if reason in ("read_only_file", "folder_read_only"):
        detail["_error"] = (f"There is no write permission on {blocker}, so {name!r} cannot be edited in place.")
        detail["reason"] = "no_write_permission"
        detail.update(coded_fact(hint="edit_blocked", variant="no_write_permission"))
        return detail
    if reason == "unreadable":
        detail["_error"] = f"The file behind {name!r} could not be opened for writing: {blocker}."
        detail["reason"] = "source_unreadable"
        detail.update(coded_fact(hint="edit_blocked", variant="source_unreadable"))
        return detail

    editing_elsewhere = ""
    try:
        if layer.isEditable():
            editing_elsewhere = " The layer is already in edit mode."
    except Exception as exc:  # noqa: BLE001
        _log(f"cannot_edit_error: isEditable() failed: {exc}")
    detail["_error"] = (f"QGIS refused to open an edit session on {name!r}{where}.{editing_elsewhere} "
                        f"Provider: {facts['provider'] or 'unknown'}"
                        f"{'; storage: ' + facts['storage'] if facts['storage'] else ''}.")
    detail["reason"] = "start_editing_refused"
    detail.update(coded_fact(hint="edit_blocked", variant="start_editing_refused"))
    return detail


def open_edit(layer, action: str = "edit") -> tuple[bool, dict | None]:





    try:
        if layer.isEditable():
            return False, None
    except Exception:  # noqa: BLE001
        return False, cannot_edit_error(layer, action)

    facts = storage_facts(layer)
    if facts["read_only"]:
        return False, cannot_edit_error(layer, action)
    have = _capability_names(layer)
    wanted = _CAPABILITY_FOR.get(action, _CAPABILITY_FOR["edit"])
    if have and not have.intersection(wanted):
        return False, cannot_edit_error(layer, action)

    try:
        started = bool(layer.startEditing())
    except Exception as exc:  # noqa: BLE001
        _log(f"startEditing raised on {facts['path'] or 'a layer'}: {exc}")
        started = False
    if not started:
        return False, cannot_edit_error(layer, action)
    return True, None


def force_out_of_edit(layer) -> bool:





    for _ in range(2):
        try:
            if not layer.isEditable():
                return True
        except Exception:  # noqa: BLE001
            return False
        try:
            layer.rollBack(True)
        except TypeError:
            try:
                layer.rollBack()
            except Exception as exc:  # noqa: BLE001
                _log(f"rollBack failed: {exc}")
        except Exception as exc:  # noqa: BLE001
            _log(f"rollBack failed: {exc}")
    try:
        still = bool(layer.isEditable())
    except Exception:  # noqa: BLE001
        still = True
    if still:
        _log("A layer stayed in edit mode after a failed commit; later writes on it will fail "
             "until the user saves or discards the edit in QGIS.")
    return not still






def _field_names_in(text: str) -> list[str]:

    return re.findall(r"name=([^\s;,]+)", str(text or ""))


def _shapefile_name_advice(layer, errors_text: str) -> tuple[str, dict] | None:


    folded = _fold(errors_text)
    mismatch = ("not the same" in folded or "ne sont pas les memes" in folded
                or "expected field" in folded or "champ attendu" in folded)
    if not mismatch:
        return None
    names = _field_names_in(errors_text)
    asked = names[0] if names else ""
    written = names[1] if len(names) > 1 else ""
    limit = field_name_limit(layer) or SHAPEFILE_NAME_MAX
    if asked and written and asked != written:
        shortened = shorten_field_name(layer, asked)
        return (
            f"the format behind this layer keeps field names to {limit} characters, so {asked!r} was written as "
            f"{written!r} and the save was refused on the mismatch",
            coded_fact(hint="shapefile_field_name", short_name=shortened, asked=asked, written=written),
        )
    return (
        "the field the layer added and the field the file created are not the same",
        coded_fact(hint="shapefile_field_name", variant="ascii", max_chars=limit),
    )


def commit_failure_error(layer, errors, what: str = "the change") -> dict:






    lines = [str(line) for line in (errors or []) if str(line).strip()]
    joined = "; ".join(lines) or "the provider gave no detail"
    folded = _fold(joined)
    facts = storage_facts(layer)
    try:
        name = layer.name()
    except Exception:  # noqa: BLE001
        name = "the layer"
    try:
        still_editing = bool(layer.isEditable())
    except Exception:  # noqa: BLE001
        still_editing = True
    result = {
        "code": "EXECUTION_FAILED",
        "layer": name,
        "provider": facts["provider"],
        "storage": facts["storage"],
        "rolled_back": not still_editing,
        "editing": still_editing,
        "provider_errors": lines[:8],
    }


    closed = (" Nothing was written and the layer was taken back out of edit mode, so it is exactly as it was."
              if not still_editing else
              " Nothing was written, and QGIS would not take the layer out of edit mode: no further call on it "
              "can succeed until the user discards the edit in QGIS.")

    advice = _shapefile_name_advice(layer, joined)
    if advice is not None:
        explanation, coded = advice
        result["reason"] = "field_name_truncated"
        result["_error"] = f"{what} could not be saved to {name!r}: {explanation}.{closed}"
        result.update(coded)
        return result

    if any(word in folded for word in ("permission", "denied", "locked", "verrou", "in use", "acces refuse",
                                       "could not open", "cannot open", "failed to open", "impossible d'ouvrir")):
        result["reason"] = "file_locked"
        result["_error"] = (f"{what} could not be saved to {name!r}: the file is locked or cannot be opened for "
                            f"writing ({joined}).{closed}")
        result.update(coded_fact(hint="commit_refused", variant="file_locked"))
        return result

    if any(word in folded for word in ("too long", "truncat", "trop long", "value out of range", "overflow")):
        result["reason"] = "value_does_not_fit"
        result["_error"] = (f"{what} could not be saved to {name!r}: a value does not fit the field it was written "
                            f"to ({joined}).{closed}")
        result.update(coded_fact(hint="commit_refused", variant="value_does_not_fit"))
        return result

    result["reason"] = "commit_refused"
    result["_error"] = f"{what} could not be saved to {name!r}: {joined}.{closed}"
    result.update(coded_fact(hint="commit_refused"))
    return result


def finish_edit(layer, started_here: bool, what: str = "the change") -> dict | None:





    if not started_here:
        return None
    try:
        committed = bool(layer.commitChanges())
    except Exception as exc:  # noqa: BLE001
        _log(f"commitChanges raised: {exc}")
        committed = False
    if committed:
        return None
    try:
        errors = list(layer.commitErrors() or [])
    except Exception:  # noqa: BLE001
        errors = []
    force_out_of_edit(layer)
    return commit_failure_error(layer, errors, what)


def abort_edit(layer, started_here: bool) -> None:

    if started_here:
        force_out_of_edit(layer)






def ascii_field_name(name: str) -> str:

    folded = unicodedata.normalize("NFKD", str(name or ""))
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", plain).strip("_")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    if not cleaned:
        return "field"
    if cleaned[0].isdigit():
        cleaned = "f" + cleaned
    return cleaned


def shorten_field_name(layer, requested: str, also_taken=()) -> str:






    return plan_field_name(layer, requested, also_taken)["name"]


def plan_field_name(layer, requested: str, also_taken=()) -> dict:






    requested = str(requested or "")
    plan = {"name": requested, "requested": requested, "changed": False, "reused": False,
            "limit": None, "note": ""}
    try:
        existing = [f.name() for f in layer.fields()]
    except Exception:  # noqa: BLE001
        existing = []
    if requested in existing:
        plan["reused"] = True
        return plan

    limit = field_name_limit(layer)
    plan["limit"] = limit
    if limit is None:
        return plan

    taken = {name.lower() for name in existing} | {str(name).lower() for name in also_taken}
    cleaned = ascii_field_name(requested)
    if cleaned != requested:
        plan["changed"] = True
    candidate = cleaned[:limit]
    if candidate.lower() not in taken:
        plan["name"] = candidate
        plan["changed"] = plan["changed"] or candidate != requested
    else:
        plan["name"] = candidate
        for number in range(2, 1000):
            tail = str(number)
            attempt = (cleaned[: max(limit - len(tail), 1)] + tail)[:limit]
            if attempt.lower() not in taken:
                plan["name"] = attempt
                break
        plan["changed"] = True
        plan["collision_avoided"] = True

    if plan["changed"]:
        plan["note"] = (f"This layer's format keeps field names to {limit} characters of plain ASCII, so the field "
                        f"is written as {plan['name']!r}, not {requested!r}.")
        plan["note_hint"] = coded_fact(hint="field_name_written_short")["hint"]
    return plan


def _native_types(layer) -> list:
    try:
        return list(layer.dataProvider().nativeTypes())
    except Exception:  # noqa: BLE001
        return []


def _clamp(value, low, high):
    if high is not None and high > 0 and value > high:
        return high
    if low is not None and low > 0 and value < low:
        return low
    return value


def plan_field_type(layer, qtype, length=None, precision=None) -> dict:









    plan = {"type": qtype, "type_name": "", "length": int(length or 0),
            "precision": int(precision or 0), "substituted": False, "note": ""}
    natives = _native_types(layer)
    if not natives:
        return plan

    def _pick(wanted):
        for native in natives:
            try:
                if native.mType == wanted:
                    return native
            except Exception as exc:  # noqa: BLE001
                _log(f"_pick: reading a native type row failed: {exc}")
                continue
        return None

    native = _pick(qtype)
    if native is None:
        for alternative in _fallback_chain(qtype):
            native = _pick(alternative)
            if native is not None:
                plan["substituted"] = True
                plan["type"] = alternative
                break
    if native is None:
        return plan

    try:
        plan["type_name"] = str(native.mTypeName or "")
        if plan["length"]:
            plan["length"] = _clamp(plan["length"], native.mMinLen, native.mMaxLen)
        if plan["precision"]:
            plan["precision"] = _clamp(plan["precision"], native.mMinPrec, native.mMaxPrec)
    except Exception as exc:  # noqa: BLE001
        _log(f"field_plan: reading native type bounds failed: {exc}")

    if plan["substituted"]:
        plan["note"] = (f"This layer's format has no column for the type that was asked for, so the field is "
                        f"created as {plan['type_name'] or 'the nearest type it has'}. Values are written "
                        "through that type.")
    elif plan["length"] != int(length or 0) or plan["precision"] != int(precision or 0):
        plan["note"] = (f"The width and precision were brought inside what this format allows: length "
                        f"{plan['length']}, precision {plan['precision']}.")
    return plan


def _fallback_chain(qtype) -> list:

    from ..core.qt_compat import field_type

    def _kind(name):
        try:
            return field_type(name)
        except Exception:  # noqa: BLE001
            return None

    ladders = {
        "Bool": ("Int", "LongLong", "String"),
        "DateTime": ("Date", "String"),
        "Date": ("DateTime", "String"),
        "Time": ("String",),
        "LongLong": ("Double", "Int", "String"),
        "Int": ("LongLong", "Double", "String"),
        "Double": ("String",),
        "String": (),
    }
    for name, chain in ladders.items():
        if _kind(name) == qtype:
            return [kind for kind in (_kind(step) for step in chain) if kind is not None]
    return [kind for kind in (_kind("String"),) if kind is not None]






def value_warnings(layer, attributes: dict) -> list:






    warnings: list = []
    if not attributes:
        return warnings
    try:
        fields = layer.fields()
    except Exception:  # noqa: BLE001
        return warnings
    for name, value in attributes.items():
        index = fields.indexOf(name)
        if index < 0:
            continue
        field = fields.at(index)
        try:
            limit = int(field.length())
        except Exception as exc:  # noqa: BLE001
            _log(f"value_warnings: reading length() on {name!r} failed: {exc}")
            continue
        if limit <= 0 or value is None:
            continue
        text = value if isinstance(value, str) else None
        if text is not None and len(text) > limit:
            warnings.append({"field": name, "limit": limit, "length": len(text),
                             "kept": text[:limit],
                             "note": f"{name!r} holds {limit} characters on this layer; the rest is dropped."})
    return warnings






def geometry_problem(layer, geom, check_crs: bool = True) -> dict | None:










    try:
        from qgis.core import QgsWkbTypes
    except Exception:  # noqa: BLE001
        return None
    if geom is None or geom.isNull():
        return None
    try:
        wanted = layer.geometryType()
        given = QgsWkbTypes.geometryType(geom.wkbType())
    except Exception:  # noqa: BLE001
        return None

    if wanted != given:
        return {
            "_error": (f"The geometry is {_geometry_word(given)} and layer {layer.name()!r} holds "
                       f"{_geometry_word(wanted)}, so it cannot be added to it."),
            "code": "INVALID_ARGS",
            **coded_fact(hint="geometry_kind_mismatch", layer_holds=_geometry_word(wanted),
                         geometry_is=_geometry_word(given)),
        }

    return _crs_problem(layer, geom) if check_crs else None


def _geometry_word(kind) -> str:
    name = getattr(kind, "name", None) or str(kind)
    lowered = str(name).lower()
    for word in ("point", "line", "polygon"):
        if word in lowered:
            return {"point": "points", "line": "lines", "polygon": "polygons"}[word]
    return "geometries of another kind"


def _crs_problem(layer, geom) -> dict | None:

    try:
        crs = layer.crs()
        box = geom.boundingBox()
        if box.isEmpty() and box.width() == 0 and box.height() == 0 and box.xMinimum() == 0 and box.yMinimum() == 0:
            return None
        x_max = max(abs(box.xMinimum()), abs(box.xMaximum()))
        y_max = max(abs(box.yMinimum()), abs(box.yMaximum()))
    except Exception:  # noqa: BLE001
        return None

    authid = ""
    try:
        authid = crs.authid() or ""
    except Exception as exc:  # noqa: BLE001
        _log(f"_crs_problem: crs.authid() failed: {exc}")

    try:
        geographic = bool(crs.isGeographic())
    except Exception:  # noqa: BLE001
        return None

    if geographic and (x_max > 180 or y_max > 90):
        return {
            "_error": (f"Layer {layer.name()!r} is in {authid or 'a longitude/latitude CRS'}, where coordinates run "
                       f"to 180 and 90, and the geometry reaches {x_max:.0f} and {y_max:.0f}. These look like "
                       "projected metres."),
            "code": "INVALID_ARGS",
            **coded_fact(hint="geometry_not_degrees", crs=authid, x_max=round(x_max), y_max=round(y_max)),
        }

    if not geographic and x_max <= 180 and y_max <= 90:
        try:
            extent = layer.extent()
            far = not extent.isEmpty() and (abs(extent.xMinimum()) > 1000 or abs(extent.yMinimum()) > 1000)
        except Exception:  # noqa: BLE001
            far = False
        if far:
            return {
                "_error": (f"Layer {layer.name()!r} is in {authid or 'a projected CRS'} and its own data sits around "
                           f"({extent.xMinimum():.0f}, {extent.yMinimum():.0f}), while the geometry given is at "
                           f"({box.xMinimum():.4f}, {box.yMinimum():.4f}). Those are longitude and latitude "
                           "degrees, not the layer's units, so the feature would land off the map."),
                "code": "INVALID_ARGS",
                **coded_fact(hint="geometry_not_layer_units", crs=authid or "the layer CRS",
                             layer_x=round(extent.xMinimum()), layer_y=round(extent.yMinimum()),
                             geometry_x=round(box.xMinimum(), 4), geometry_y=round(box.yMinimum(), 4)),
            }
    return None

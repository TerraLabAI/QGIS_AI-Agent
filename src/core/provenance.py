# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

import hashlib
import os

from .source_text import is_local, plain_source


_LINEAGE: dict = {}
_LINEAGE_MAX = 500


def _norm(path: str) -> str:




    main, _bar, suffix = str(path).partition("|")
    table = next((part[len("layername="):] for part in suffix.split("|") if part.startswith("layername=")), "")
    key = os.path.normcase(os.path.normpath(main))
    return f"{key}|layername={table}" if table else key


def _same_source(source: str) -> str:

    main, bar, suffix = str(source).partition("|")
    return os.path.normcase(os.path.normpath(main)) + bar + suffix


def _static(value):

    for attribute in ("source", "sink"):
        inner = getattr(value, attribute, None)
        if inner is not None and not callable(inner) and (hasattr(value, "selectedFeaturesOnly")
                                                            or hasattr(value, "destinationProject")):
            value = inner
            break
    if hasattr(value, "staticValue"):
        value = value.staticValue()
    return value


def _project_layer(value):
    from qgis.core import QgsMapLayer, QgsProject

    if isinstance(value, QgsMapLayer):
        return value
    if not isinstance(value, str) or not value:
        return None
    project = QgsProject.instance()
    layer = project.mapLayer(value)
    if layer is not None:
        return layer
    if is_local(value):
        wanted = _same_source(value)
        return next((other for other in project.mapLayers().values() if _same_source(other.source()) == wanted),
                    None)
    return None


def _walk(value):
    value = _static(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    else:
        yield value


def _credit_of(layer) -> tuple:
    from .licence import layer_attribution

    metadata = layer.metadata()
    licences = [str(text).strip() for text in metadata.licenses() if str(text).strip()]
    rights = [str(text).strip() for text in metadata.rights() if str(text).strip()]
    attribution = layer_attribution(layer)
    if attribution and attribution not in rights:
        rights.append(attribution)
    return licences, rights


def _remembered(value: str):


    found = _LINEAGE.get(_norm(value)) or _LINEAGE.get(_norm(value.split("|", 1)[0]))
    if found is None and "|" not in value and value.lower().endswith(".gpkg") and os.path.isfile(value):
        from osgeo import ogr

        dataset = ogr.Open(value)
        try:
            table = dataset.GetLayer(0).GetName() if dataset is not None and dataset.GetLayerCount() else ""
        finally:
            dataset = None
        if table:
            found = _LINEAGE.get(_norm(f"{value}|layername={table}"))
    return found


def inputs_of(parameters: dict, algorithm=None) -> dict:






    inputs, licences, rights, seen = [], [], [], set()

    def add(label, found_licences, found_rights):
        inputs.append(label)
        licences.extend(item for item in found_licences if item not in licences)
        rights.extend(item for item in found_rights if item not in rights)

    layers = []
    read = {}
    for name, value in (parameters or {}).items():
        definition = algorithm.parameterDefinition(name) if algorithm is not None else None

        if definition is not None and definition.isDestination():
            continue
        read[name] = value
    for value in _walk(read):
        layer = _project_layer(value)
        if layer is not None:
            if layer.id() not in seen:
                seen.add(layer.id())
                layers.append(layer)
            continue
        if not isinstance(value, str) or not value or value in seen:
            continue
        remembered = _remembered(value) if is_local(value) else None
        if remembered is not None:
            seen.add(value)
            add(remembered["label"], remembered["licences"], remembered["rights"])
        elif is_local(value) or value.startswith(("/vsi", "http://", "https://")):
            seen.add(value)
            add(plain_source(value), [], [])
    from .licence import parent_facts

    names = [layer.name() for layer in layers]
    parents = []
    for layer in layers:
        label = layer.name()
        if names.count(label) > 1:
            label = f"{label} [{plain_source(layer.source())}]"
        add(label, *_credit_of(layer))

        parents.append(parent_facts(layer))
    return {"inputs": inputs, "licences": licences, "rights": rights, "parents": parents}


def command_text(algorithm_id: str, parameters: dict) -> str:





    originals: dict = {}
    clashes: set = set()

    def plain(value):
        value = _static(value)
        if isinstance(value, (list, tuple)):
            return [plain(item) for item in value]
        if isinstance(value, dict):
            return {str(key): plain(item) for key, item in value.items()}
        if value is None or isinstance(value, (bool, int, float)):
            return value
        layer = _project_layer(value)
        if layer is not None:
            return layer.name()
        text = str(value)
        shown = plain_source(text)
        originals.setdefault(shown, set()).add(text)
        if shown in clashes:
            shown += " #" + hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
        return shown

    shown = plain(dict(parameters or {}))
    clashes.update(name for name, sources in originals.items() if len(sources) > 1)
    if clashes:
        shown = plain(dict(parameters or {}))
    return f'processing.run("{algorithm_id}", {shown!r})'


def _table_exists(path: str, table: str) -> bool:
    from osgeo import ogr

    dataset = ogr.Open(path)
    try:
        return dataset is not None and dataset.GetLayerByName(table) is not None
    finally:
        dataset = None


def existing_files(parameters: dict) -> set:






    found: set = set()
    for value in _walk(dict(parameters or {})):
        layer = _project_layer(value)
        text = str(layer.source()) if layer is not None else value
        if not isinstance(text, str) or not text or not is_local(text):
            continue
        main, _bar, suffix = text.partition("|")
        if not os.path.isfile(main):
            continue
        found.add(_norm(main))
        table = next((part[len("layername="):] for part in suffix.split("|") if part.startswith("layername=")), "")
        try:
            if table and _table_exists(main, table):
                found.add(_norm(text))
        except Exception:  # noqa: BLE001
            found.add(_norm(text))
    return found


def was_there(path: str, table: str, existed) -> bool:

    return _norm(f"{path}|layername={table}" if table else path) in (existed or ())


def remember_output(value, provenance: dict) -> None:

    if not isinstance(value, str) or not is_local(value) or not provenance:
        return
    inputs = ", ".join(provenance.get("inputs") or [])
    label = f"{plain_source(value)} (from {provenance.get('algorithm') or 'Processing'}"
    label += f" of {inputs})" if inputs else ")"
    if len(_LINEAGE) >= _LINEAGE_MAX:
        _LINEAGE.pop(next(iter(_LINEAGE)))
    _LINEAGE[_norm(value)] = {"label": label, "licences": list(provenance.get("licences") or []),
                              "rights": list(provenance.get("rights") or [])}

# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import os

from ..core.logger import log_warning

_PROVENANCE_COMMAND_MAX = 2000


def _stamp_provenance(layer, provenance: dict) -> None:











    try:
        from datetime import datetime, timezone

        from ..core.licence import ORIGIN_PROPERTY, credit_layer, modified_rights, write_derived

        when = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        algorithm = provenance.get("algorithm") or "a Processing algorithm"
        command = str(provenance.get("command") or "")
        if len(command) > _PROVENANCE_COMMAND_MAX:
            command = command[:_PROVENANCE_COMMAND_MAX] + " ..."
        md = layer.metadata()


        own = any(str(text).strip() for text in md.licenses())
        inputs = ", ".join(str(name) for name in provenance.get("inputs") or [])
        md.addHistoryItem(f"{when}: produced by the TerraLab AI Agent with {algorithm}"
                          + (f". Inputs: {inputs}" if inputs else "")
                          + (f". Command: {command}" if command else ""))
        if not md.abstract():
            md.setAbstract(f"Output of {algorithm}, produced by the TerraLab AI Agent on {when}. "
                           "The exact command is in the metadata history.")
        layer.setMetadata(md)
        layer.setCustomProperty(ORIGIN_PROPERTY, True)
        if own:
            credit_layer(layer)
        else:
            credit_layer(layer, list(provenance.get("licences") or []),
                         modified_rights(provenance.get("rights") or []))

            write_derived(layer, list(provenance.get("parents") or []))
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Provenance not written on {layer.name()}: {exc}")
        return
    write_metadata_to_file(layer, provenance.get("existed"))


def _gpkg_table(path: str) -> str:

    from osgeo import ogr

    dataset = ogr.Open(path)
    try:
        return dataset.GetLayer(0).GetName() if dataset is not None and dataset.GetLayerCount() else ""
    finally:
        dataset = None


def write_metadata_to_file(layer, existed=None) -> None:











    from qgis.core import QgsProviderRegistry

    from ..core.provenance import was_there

    try:
        registry = QgsProviderRegistry.instance()
        parts = registry.decodeUri(layer.providerType(), layer.source()) or {}
        path = str(parts.get("path") or "")
        if not path or not os.path.isfile(path):
            return
        if layer.providerType() == "ogr" and path.lower().endswith(".gpkg"):
            table = str(parts.get("layerName") or "") or _gpkg_table(path)
            if not table:
                log_warning(f"Provenance not saved in {os.path.basename(path)}: no table found in it")
                return
            if was_there(path, table, existed):
                return
            parts["layerName"] = table
            parts.pop("layerId", None)
            saved = registry.saveLayerMetadata("ogr", registry.encodeUri("ogr", parts), layer.metadata())
            ok, error = saved if isinstance(saved, tuple) else (bool(saved), "")
        else:
            if was_there(path, "", existed) or os.path.exists(layer.metadataUri()):
                return
            error, ok = layer.saveDefaultMetadata()
        if not ok:
            log_warning(f"Provenance not saved with {os.path.basename(path)}: {error}")
    except Exception as exc:  # noqa: BLE001
        log_warning(f"Provenance not saved with the layer's file: {exc}")

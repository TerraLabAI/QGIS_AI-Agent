# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later








from __future__ import annotations

import html
import os
import uuid
import zipfile
from io import BytesIO

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import output_paths, security
from ..core.feature_requests import feature_request
from ..core.host_platform import retry_file_op
from ..core.tool_registry import Tool, ToolRegistry, tool_error

MAX_ROWS = 1000
MAX_FIELDS = 50
MAX_CELL_CHARS = 500


def register_document_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="export_document_report",
        danger="destructive",
        label=QT_TRANSLATE_NOOP("AIAgent", "Export a document report"),
        input_schema={
            "type": "object",
            "properties": {
                "layer": {"type": "string", "minLength": 1},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string", "minLength": 1},
                           "maxItems": 50},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
                "output_path": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["layer"],
            "additionalProperties": False,
        },
        handler=_export_document_report,
        background=True,
    ))


def _output_target(args: dict, layer_name: str):
    asked = str(args.get("output_path") or "").strip()
    target = security.expand_path(asked) if asked else ""
    if target and os.path.isdir(target):
        target = os.path.join(target, output_paths.safe_file_name(layer_name, "report") + ".docx")
    elif target and not os.path.splitext(target)[1]:
        target += ".docx"
    if not target:
        folder = output_paths.default_folder()
        stem = output_paths.safe_file_name(layer_name, "report") + "_report"
        target = os.path.join(folder, stem + ".docx")
        for index in range(2, 1000):
            if not os.path.exists(target):
                break
            target = os.path.join(folder, f"{stem}_{index}.docx")
    elif not target.lower().endswith(".docx"):
        return None, tool_error("output_path must end in .docx.", "INVALID_ARGS",
                                "A Word document path ending in .docx.")
    error = security.validate_path(target, write=True)
    if error:
        return None, tool_error(error, "PERMISSION_DENIED",
                                "Allowed: the project folder, your home folder or the temp folder.")
    if os.path.exists(target) and args.get("overwrite") is not True:
        return None, tool_error(f"{target} already exists.", "INVALID_ARGS",
                                "overwrite:true replaces it; else a new output_path.")
    return target, None


def _clean(value) -> str:
    text = "" if value is None else str(value)
    return text[:MAX_CELL_CHARS] + ("..." if len(text) > MAX_CELL_CHARS else "")


def _prepare(args: dict) -> dict:
    from qgis.core import QgsVectorLayer

    from .layer_lookup import _field_not_found_error, _find_layer, _layer_not_found_error

    ref = str(args.get("layer") or "").strip()
    layer = _find_layer(ref)
    if layer is None:
        return _layer_not_found_error(ref)
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{layer.name()} is not a vector layer.", "INVALID_ARGS",
                          "a vector layer with an attribute table.")
    requested = args.get("fields")
    names = [f.name() for f in layer.fields()] if requested is None else [str(f).strip() for f in requested]
    if not names or len(names) > MAX_FIELDS:
        return tool_error(f"A report needs 1 to {MAX_FIELDS} fields.", "INVALID_ARGS",
                          "a shorter fields list.")
    selected = []
    for name in names:
        index = layer.fields().indexOf(name)
        if index < 0:
            return _field_not_found_error(layer, name)
        selected.append((layer.fields().at(index).name(), index))
    max_rows = int(args.get("max_rows") or MAX_ROWS)
    max_rows = min(max_rows, MAX_ROWS)




    request = feature_request(attributes=[index for _name, index in selected], geometry=False, limit=max_rows)
    rows = []
    for feature in layer.getFeatures(request):
        rows.append([_clean(feature.attribute(index)) for _name, index in selected])
        if len(rows) >= max_rows:
            break
    target, refusal = _output_target(args, layer.name())
    if refusal:
        return refusal
    metadata = {
        "Layer": layer.name(),
        "Provider": layer.providerType(),
        "Geometry": str(layer.geometryType()),
        "CRS": layer.crs().authid() or layer.crs().description() or "unknown",
        "Source": layer.source(),
    }
    return {"layer": layer.name(), "fields": [name for name, _index in selected],
            "rows": rows, "row_count": layer.featureCount(), "shown_rows": len(rows),
            "title": _clean(args.get("title") or f"Attribute report: {layer.name()}"),
            "summary": _clean(args.get("summary") or ""), "metadata": metadata,
            "target": target, "cut": layer.featureCount() > len(rows)}


def _p(text: str, bold: bool = False) -> str:
    weight = "<w:b/>" if bold else ""
    return f'<w:p><w:r><w:rPr>{weight}</w:rPr><w:t xml:space="preserve">{html.escape(str(text))}</w:t></w:r></w:p>'


def _build_docx(*, title: str, summary: str, fields: list[str], rows: list[list[str]], metadata: dict,
                row_count: int, cut: bool = False) -> bytes:
    body = [_p(title, True)]
    if summary:
        body.append(_p(summary))
    body.append(_p(f"Rows: {row_count:,}" + (f" (showing {len(rows):,}; limit {MAX_ROWS:,})" if cut else "")))
    for key, value in metadata.items():
        body.append(_p(f"{key}: {value}"))
    body.append("<w:tbl><w:tr>" + "".join(f"<w:tc>{_p(field, True)}</w:tc>" for field in fields) + "</w:tr>")
    for row in rows:
        body.append("<w:tr>" + "".join(f"<w:tc>{_p(value)}</w:tc>" for value in row) + "</w:tr>")
    body.append("</w:tbl>")
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + "".join(body) + "<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    out = BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in (
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", rels),
            ("word/document.xml", document),
        ):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value.encode("utf-8"))
    return out.getvalue()


def _export_document_report(args: dict) -> dict:
    from ..core.background import run_on_main_thread

    try:
        plan = run_on_main_thread(_prepare, args, timeout=60)
    except InterruptedError:
        return tool_error("The document report was stopped.", "STOPPED", "It can be retried.")
    if not isinstance(plan, dict) or "_error" in plan:
        return plan
    target = plan.pop("target")
    folder = os.path.dirname(target)
    part = os.path.join(folder, f".{os.path.basename(target)}.{uuid.uuid4().hex[:12]}.part")
    try:
        os.makedirs(folder, exist_ok=True)
        document_fields = {
            key: plan[key]
            for key in ("title", "summary", "fields", "rows", "metadata", "row_count", "cut")
        }
        with open(part, "wb") as stream:
            stream.write(_build_docx(**document_fields))
        retry_file_op(os.replace, part, target)
    except PermissionError as exc:
        try:
            os.remove(part)
        except OSError:
            pass
        return tool_error(f"The document could not replace {target}: another program holds it open ({exc}).",
                          "EXECUTION_FAILED", "Closing it there, or a new path, frees the write.")
    except OSError as exc:
        try:
            os.remove(part)
        except OSError:
            pass
        return tool_error(f"The document could not be written: {str(exc)[:200]}", "EXECUTION_FAILED",
                          "The folder may lack room, or need another output_path.")
    return {"output_path": target, "layer": plan["layer"], "row_count": plan["row_count"],
            "shown_rows": plan["shown_rows"], "fields": plan["fields"],
            "format": "docx", "cut": plan["cut"]}

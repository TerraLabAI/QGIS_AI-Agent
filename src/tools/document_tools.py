# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later







from __future__ import annotations

import html
import os
import uuid
import zipfile
from io import BytesIO

from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP
from qgis.PyQt.QtGui import QImageReader

from ..core import output_paths, security
from ..core.background import run_on_main_thread
from ..core.feature_requests import feature_request
from ..core.host_platform import retry_file_op
from ..core.layer_order import feature_count_of
from ..core.provider_uri import scrub_uri_secrets
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from ._layers import layer_not_found, resolve_layer
from .layer_lookup import _field_not_found_error

MAX_ROWS = 1000
MAX_FIELDS = 50
MAX_CELL_CHARS = 500
MAX_SECTIONS = 200
MAX_TEXT_CHARS = 20000
MAX_IMAGES = 50
MAX_IMAGE_BYTES = 30 * 1024 * 1024

_TEXT_WIDTH_EMU = int(16 / 2.54 * 914400)
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


_NOT_XML = dict.fromkeys([*(c for c in range(32) if c not in (9, 10, 13)), *range(0xD800, 0xE000), 0xFFFE, 0xFFFF])


def register_document_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="export_document_report",
        danger="destructive",
        writes_at=("output_path",),
        label=QT_TRANSLATE_NOOP("AIAgent", "Export a document report"),
        input_schema={
            "type": "object",
            "properties": {
                "layer": {"type": "string", "minLength": 1},
                "title": {"type": "string"},
                "sections": {"type": "array", "maxItems": MAX_SECTIONS, "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "level": {"type": "integer", "minimum": 1, "maximum": 3},
                        "text": {"type": "string"},
                        "image": {"type": "string", "minLength": 1},
                        "caption": {"type": "string"},
                        "table": {"type": "object", "properties": {
                            "columns": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FIELDS},
                            "rows": {"type": "array", "maxItems": MAX_ROWS,
                                     "items": {"type": "array", "maxItems": MAX_FIELDS}},
                        }, "required": ["rows"], "additionalProperties": False},
                    },
                    "additionalProperties": False,
                }},
                "summary": {"type": "string"},
                "fields": {"type": "array", "items": {"type": "string", "minLength": 1},
                           "maxItems": 50},
                "max_rows": {"type": "integer", "minimum": 1, "maximum": 1000},
                "output_path": {"type": "string"},
                "overwrite": {"type": "boolean"},
            },
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
        return None, tool_error("output_path must end in .docx.", "INVALID_ARGS")
    error = security.validate_path(target, write=True)
    if error:
        return None, tool_error(error, "PERMISSION_DENIED",
                                hint="georef_write_scope")
    if os.path.exists(target) and args.get("overwrite") is not True:
        return None, tool_error(f"{target} already exists.", "INVALID_ARGS",
                                hint="report_path_exists")
    return target, None


def _clean(value) -> str:
    text = "" if value is None else str(value).translate(_NOT_XML)
    return text[:MAX_CELL_CHARS] + ("..." if len(text) > MAX_CELL_CHARS else "")


def _sections(args: dict):


    sections, pictures = [], 0
    for section in args.get("sections") or ():
        section = dict(section)
        asked = str(section.get("image") or "").strip()
        if asked:
            pictures += 1
            path = security.expand_path(asked)
            ext = os.path.splitext(path)[1].lower()
            error = security.validate_read(path)
            if error:
                return None, tool_error(error, "PERMISSION_DENIED", hint="document_image_scope")
            if ext not in _IMAGE_TYPES:
                return None, tool_error(f"{asked}: a document picture is a .png or .jpg file.", "INVALID_ARGS",
                                        hint="document_image_kind")
            if not os.path.isfile(path):
                return None, tool_error(f"{asked} does not exist.", "INVALID_ARGS", hint="document_image_missing")
            if pictures > MAX_IMAGES:
                return None, tool_error(f"A document holds at most {MAX_IMAGES} pictures.", "INVALID_ARGS")
            section["image"] = path
        sections.append(section)
    return sections, None


def _prepare(args: dict) -> dict:
    from qgis.core import QgsVectorLayer, QgsWkbTypes

    sections, refusal = _sections(args)
    if refusal:
        return refusal
    ref = str(args.get("layer") or "").strip()
    if not ref:
        if not args.get("sections"):
            return tool_error("A document needs a layer, sections, or both.", "INVALID_ARGS",
                              hint="document_empty")
        title = _clean(args.get("title") or "Report")
        target, refusal = _output_target(args, title)
        if refusal:
            return refusal
        return {"layer": None, "title": title, "summary": _clean(args.get("summary") or ""), "target": target,
                "sections": sections}
    layer = resolve_layer(ref)
    if layer is None:
        return layer_not_found(ref)
    if not isinstance(layer, QgsVectorLayer):
        return tool_error(f"{layer.name()} is not a vector layer.", "INVALID_ARGS",
                          hint="document_layer_kind")
    requested = args.get("fields")
    names = [f.name() for f in layer.fields()] if requested is None else [str(f).strip() for f in requested]
    if not names or len(names) > MAX_FIELDS:
        return tool_error(f"A report needs 1 to {MAX_FIELDS} fields.", "INVALID_ARGS")
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
        "Geometry": QgsWkbTypes.geometryDisplayString(layer.geometryType()),
        "CRS": layer.crs().authid() or layer.crs().description() or "unknown",

        "Source": scrub_uri_secrets(layer.source()),
    }
    count = feature_count_of(layer)
    return {"layer": layer.name(), "fields": [name for name, _index in selected],
            "rows": rows, "row_count": count, "shown_rows": len(rows),
            "title": _clean(args.get("title") or f"Attribute report: {layer.name()}"),
            "summary": _clean(args.get("summary") or ""), "metadata": metadata,
            "target": target, "cut": len(rows) >= max_rows if count is None else count > len(rows),
            "sections": sections}


def _p(text: str, bold: bool = False) -> str:
    weight = "<w:b/>" if bold else ""
    return f'<w:p><w:r><w:rPr>{weight}</w:rPr><w:t xml:space="preserve">{html.escape(str(text))}</w:t></w:r></w:p>'


def _styled(text: str, style: str) -> str:
    return (f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r>'
            f'<w:t xml:space="preserve">{html.escape(str(text))}</w:t></w:r></w:p>')


def _table(columns: list[str], rows: list[list[str]]) -> str:
    lines = ['<w:tbl><w:tblPr><w:tblStyle w:val="Grid"/><w:tblW w:w="0" w:type="auto"/></w:tblPr>']
    if columns:
        lines.append("<w:tr>" + "".join(f"<w:tc>{_p(c, True)}</w:tc>" for c in columns) + "</w:tr>")
    lines.extend("<w:tr>" + "".join(f"<w:tc>{_p(v)}</w:tc>" for v in row) + "</w:tr>" for row in rows)
    return "".join(lines) + "</w:tbl>"


def _picture(number: int, width: int, height: int) -> str:

    return (
        '<w:p><w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{width}" cy="{height}"/><wp:docPr id="{number}" name="Picture {number}"/>'
        '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        f'<pic:nvPicPr><pic:cNvPr id="{number}" name="image{number}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="rIdImg{number}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic>'
        "</a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>")


_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
    '<w:sz w:val="22"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120"/></w:pPr>'
    '</w:pPrDefault></w:docDefaults>'
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
    '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
    '<w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:sz w:val="40"/></w:rPr></w:style>'
    + "".join(
        f'<w:style w:type="paragraph" w:styleId="Heading{n}"><w:name w:val="heading {n}"/>'
        f'<w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240"/>'
        f'<w:outlineLvl w:val="{n - 1}"/></w:pPr><w:rPr><w:b/><w:sz w:val="{size}"/></w:rPr></w:style>'
        for n, size in ((1, 32), (2, 28), (3, 24)))
    + '<w:style w:type="paragraph" w:styleId="Caption"><w:name w:val="caption"/><w:basedOn w:val="Normal"/>'
    '<w:rPr><w:i/><w:sz w:val="18"/></w:rPr></w:style>'
    '<w:style w:type="table" w:styleId="Grid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders>'
    + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
              for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
    + '</w:tblBorders><w:tblCellMar><w:left w:w="80" w:type="dxa"/><w:right w:w="80" w:type="dxa"/>'
    '</w:tblCellMar></w:tblPr></w:style></w:styles>')


def _section_body(section: dict, images: list) -> list[str]:

    body = []
    if section.get("heading"):
        body.append(_styled(_clean(section["heading"]), f"Heading{int(section.get('level') or 1)}"))
    text = str(section.get("text") or "").translate(_NOT_XML)[:MAX_TEXT_CHARS]
    body.extend(_p(line) for line in text.split("\n") if line.strip())
    if section.get("image_data"):
        data, ext, width, height = section["image_data"]
        number = len(images) + 1
        images.append((f"image{number}{ext}", data))
        scale = min(1.0, _TEXT_WIDTH_EMU / max(width * 9525, 1))
        body.append(_picture(number, int(width * 9525 * scale), int(height * 9525 * scale)))
    if section.get("caption"):
        body.append(_styled(_clean(section["caption"]), "Caption"))
    table = section.get("table")
    if table:
        columns = [_clean(c) for c in (table.get("columns") or [])][:MAX_FIELDS]
        rows = [[_clean(v) for v in row[:MAX_FIELDS]] for row in (table.get("rows") or [])[:MAX_ROWS]]
        body.append(_table(columns, rows))
    return body


def _build_docx(*, title: str, summary: str, fields: list[str] | None = None, rows: list[list[str]] | None = None,
                metadata: dict | None = None, row_count: int | None = None, cut: bool = False,
                sections: list[dict] | None = None) -> bytes:
    body = [_styled(title, "Title")]
    if summary:
        body.append(_p(summary))
    images: list = []
    for section in sections or ():
        body.extend(_section_body(section, images))
    if fields is not None:
        rows = rows or []
        total = f"at least {len(rows):,}" if row_count is None else f"{row_count:,}"
        body.append(_p(f"Rows: {total}" + (f" (showing {len(rows):,}; limit {MAX_ROWS:,})" if cut else "")))
        for key, value in (metadata or {}).items():
            body.append(_p(f"{key}: {value}"))
        body.append(_table(fields, rows))
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
        ' xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
        "<w:body>" + "".join(body) + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708"'
        ' w:gutter="0"/></w:sectPr></w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(f'<Default Extension="{ext[1:]}" ContentType="{kind}"/>' for ext, kind in _IMAGE_TYPES.items())
        + '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
        'officedocument.wordprocessingml.styles+xml"/>'
        "</Types>"
    )
    document_rels = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/styles" Target="styles.xml"/>'
        + "".join(f'<Relationship Id="rIdImg{n}" Type="http://schemas.openxmlformats.org/officeDocument/'
                  f'2006/relationships/image" Target="media/{name}"/>'
                  for n, (name, _data) in enumerate(images, 1))
        + "</Relationships>"
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
            ("word/styles.xml", _STYLES),
            ("word/_rels/document.xml.rels", document_rels),
            *((f"word/media/{name}", data) for name, data in images),
        ):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value if isinstance(value, bytes) else value.encode("utf-8"))
    return out.getvalue()


def _read_pictures(sections: list[dict]):


    for section in sections:
        path = section.get("image")
        if not path:
            continue
        if os.path.getsize(path) > MAX_IMAGE_BYTES:
            return tool_error(f"{path} is over {MAX_IMAGE_BYTES // 2**20} MB.", "INVALID_ARGS",
                              hint="document_image_size")
        size = QImageReader(path).size()
        if size.width() <= 0 or size.height() <= 0:
            return tool_error(f"{path} is not a picture Qt can read.", "INVALID_ARGS", hint="document_image_kind")
        with open(path, "rb") as stream:
            data = stream.read()
        ext = os.path.splitext(path)[1].lower()
        section["image_data"] = (data, ".jpg" if ext == ".jpeg" else ext, size.width(), size.height())
    return None


def _export_document_report(args: dict) -> dict:

    try:
        plan = run_on_main_thread(_prepare, args, timeout=60)
    except InterruptedError:
        return tool_error("The document report was stopped.", "STOPPED", hint="document_stopped")
    if not isinstance(plan, dict) or "_error" in plan:
        return plan
    target = plan.pop("target")
    refusal = _read_pictures(plan["sections"])
    if refusal:
        return refusal
    folder = os.path.dirname(target)
    part = os.path.join(folder, f".{os.path.basename(target)}.{uuid.uuid4().hex[:12]}.part")
    try:
        os.makedirs(folder, exist_ok=True)
        document_fields = {
            key: plan[key]
            for key in ("title", "summary", "fields", "rows", "metadata", "row_count", "cut", "sections")
            if key in plan
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
                          "EXECUTION_FAILED", hint="document_file_open")
    except OSError as exc:
        try:
            os.remove(part)
        except OSError:
            pass
        return tool_error(f"The document could not be written: {str(exc)[:200]}", "EXECUTION_FAILED",
                          hint="report_write_failed")
    written = {"output_path": target, "layer": plan["layer"], "format": "docx",
               "sections": len(plan["sections"]),
               "pictures": sum(1 for section in plan["sections"] if section.get("image"))}
    if plan["layer"] is not None:
        written.update(row_count=plan["row_count"], shown_rows=plan["shown_rows"], fields=plan["fields"],
                       cut=plan["cut"])
    return written

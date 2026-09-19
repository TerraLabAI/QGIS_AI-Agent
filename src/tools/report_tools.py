# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later






















from __future__ import annotations

import os
import shutil
import tempfile
import time

from qgis.core import (
    QgsFeatureRequest,
    QgsLayoutExporter,
    QgsLayoutItemMap,
    QgsProject,
    QgsReport,
    QgsReportSectionFieldGroup,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import QT_TRANSLATE_NOOP

from ..core import limits
from ..core.qt_compat import enum_member
from ..core.tool_registry import Tool, ToolRegistry, tool_error
from .layer_lookup import _field_not_found_error, _find_layer, _layer_not_found_error

MAX_LEVELS = 3


_DRIVE_MARGIN = 0.1



_INFO_WALK_SECONDS = 2.0


_PLAN_WALK_SHARE = 0.15
_IMAGE_FORMATS = ("png", "jpg", "tif")


def register_report_tools(registry: ToolRegistry):
    registry.register(Tool(
        name="create_report",
        danger="write",
        label=QT_TRANSLATE_NOOP("AIAgent", "Build the report {name}"),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "levels": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "layer": {"type": "string"},
                            "field": {"type": "string"},
                            "ascending": {"type": "boolean"},
                            "body_layout": {"type": "string"},
                            "header_layout": {"type": "string"},
                            "footer_layout": {"type": "string"},
                        },
                        "required": ["layer", "field"],
                    },
                },
                "cover_layout": {"type": "string"},
                "closing_layout": {"type": "string"},
                "replace": {"type": "boolean"},
            },
            "required": ["name", "levels"],
        },
        handler=_create_report,
    ))






def _print_layout(name, role: str):

    manager = QgsProject.instance().layoutManager()
    layout = manager.layoutByName(str(name))
    if layout is not None and getattr(layout, "pageCollection", None) is not None:
        return layout, None
    existing = [item.name() for item in manager.printLayouts()]
    what = "is a report, and a report cannot hold another report" if layout is not None else "is not a print layout"
    return None, tool_error(
        f"{role} '{name}' {what}. Print layouts in this project: {existing[:20]}.",
        "INVALID_ARGS",
        "Pass the name of a print layout (list_layouts), or make one with create_print_layout first.")


def _level_specs(args: dict):

    levels = args.get("levels") or []
    if not isinstance(levels, list) or not 1 <= len(levels) <= MAX_LEVELS:
        return None, tool_error(f"levels takes 1 to {MAX_LEVELS} levels, outermost first.", "INVALID_ARGS",
                                "Pass [{layer, field, body_layout}] for one level, then add the level inside it.")
    specs = []
    for index, level in enumerate(levels, start=1):
        if not isinstance(level, dict):
            return None, tool_error(f"Level {index} is not an object.", "INVALID_ARGS",
                                    "Each level is {layer, field, body_layout}.")
        layer = _find_layer(str(level.get("layer") or ""))
        if layer is None:
            return None, _layer_not_found_error(str(level.get("layer") or ""))
        if not isinstance(layer, QgsVectorLayer):
            return None, tool_error(f"Level {index}: {layer.name()} is not a vector layer, and a report section "
                                    "walks the features of one.", "INVALID_ARGS",
                                    "Pass a vector layer (list_layers).")
        field_index = layer.fields().lookupField(str(level.get("field") or ""))
        if field_index < 0:
            return None, _field_not_found_error(layer, str(level.get("field") or ""))
        spec = {"level": index, "layer": layer, "field": layer.fields().at(field_index).name(),
                "ascending": bool(level.get("ascending", True))}
        for role in ("body", "header", "footer"):
            name = str(level.get(f"{role}_layout") or "").strip()
            spec[role] = None
            if name:
                layout, error = _print_layout(name, f"Level {index} {role}_layout")
                if error:
                    return None, error
                spec[role] = layout
        specs.append(spec)
    if specs[-1]["body"] is None:
        return None, tool_error(
            f"The innermost level ({specs[-1]['layer'].name()} by {specs[-1]['field']}) has no body_layout, so "
            "the report would print no page for it.", "INVALID_ARGS",
            "Pass body_layout on the last level: the print layout each of its features gets a page of.")
    return specs, None


def _nesting_error(specs: list):






    for parent, child in zip(specs, specs[1:]):
        if child["layer"].fields().lookupField(parent["field"]) >= 0:
            continue
        return tool_error(
            f"Level {child['level']} ({child['layer'].name()}) has no field named {parent['field']}. QGIS matches "
            f"a report's inner level to the level above only through a field of the same name in both layers "
            f"(no relation or expression can stand in), so without it every feature of "
            f"{child['layer'].name()} would print under every {parent['field']}.",
            "INVALID_ARGS",
            f"Give {child['layer'].name()} a {parent['field']} field holding the value of the level above (for "
            f"features inside the parent's shapes, run_processing native:joinattributesbylocation), then pass "
            f"that layer here.")
    return None


def _repeat_error(specs: list):







    request_flag = enum_member(QgsFeatureRequest, "Flag", "NoGeometry")
    for position, spec in enumerate(specs[:-1]):
        if spec["body"] is None:
            continue
        layer = spec["layer"]
        fields = [ancestor["field"] for ancestor in specs[:position]
                  if layer.fields().lookupField(ancestor["field"]) >= 0] + [spec["field"]]
        indexes = [layer.fields().lookupField(name) for name in fields]
        request = QgsFeatureRequest().setFlags(request_flag).setSubsetOfAttributes(indexes)
        seen = set()
        repeated = None
        for feature in layer.getFeatures(request):
            key = tuple(str(feature.attribute(i)) for i in indexes)
            if key in seen:
                repeated = key[-1]
                break
            seen.add(key)
        if repeated is None:
            continue
        return tool_error(
            f"Level {spec['level']} has a body_layout, and more than one feature of {layer.name()} has "
            f"{spec['field']} = {repeated}: each of them would get its own page, with every page of the level "
            f"inside printed again after each one.",
            "INVALID_ARGS",
            f"Leave body_layout out of level {spec['level']} (it then walks each {spec['field']} value once, and "
            f"header_layout on the level inside gives each value its opening page), or use a layer with one "
            f"feature per {spec['field']}.")
    return None


def _follow_feature(layout) -> int:






    from .layout_tools import _is_inset

    maps = [item for item in layout.items() if isinstance(item, QgsLayoutItemMap)]
    driven = [item for item in maps if item.atlasDriven()]
    if driven:
        return len(driven)
    auto = enum_member(QgsLayoutItemMap, "AtlasScalingMode", "Auto", None)
    for item in maps:
        if _is_inset(item):
            continue
        item.setAtlasDriven(True)
        if auto is not None:
            item.setAtlasScalingMode(auto)
        item.setAtlasMargin(_DRIVE_MARGIN)
        driven.append(item)
    return len(driven)


def _create_report(args: dict) -> dict:
    name = str(args.get("name") or "").strip()
    if not name:
        return tool_error("name is empty.", "INVALID_ARGS", "Pass the name the report gets in the Layout Manager.")
    manager = QgsProject.instance().layoutManager()
    existing = manager.layoutByName(name)
    if existing is not None:
        if getattr(existing, "pageCollection", None) is not None:
            return tool_error(f"A print layout is already called '{name}', and a report never replaces one.",
                              "INVALID_ARGS", "Pick another name for the report.")
        if not args.get("replace"):
            return tool_error(f"A report called '{name}' already exists.", "INVALID_ARGS",
                              "Pass replace:true to rebuild it (only if the user wants it rebuilt), or pick "
                              "another name.")



    specs, error = _level_specs(args)
    if error:
        return error
    error = _nesting_error(specs) or _repeat_error(specs)
    if error:
        return error
    ends = {}
    for role in ("cover", "closing"):
        wanted = str(args.get(f"{role}_layout") or "").strip()
        ends[role] = None
        if wanted:
            layout, error = _print_layout(wanted, f"{role}_layout")
            if error:
                return error
            ends[role] = layout

    project = QgsProject.instance()
    report = QgsReport(project)
    report.setName(name)
    if ends["cover"] is not None:
        report.setHeader(ends["cover"].clone())
        report.setHeaderEnabled(True)
    if ends["closing"] is not None:
        report.setFooter(ends["closing"].clone())
        report.setFooterEnabled(True)
    parent = report
    described = []
    for spec in specs:
        section = QgsReportSectionFieldGroup(parent)
        section.setLayer(spec["layer"])
        section.setField(spec["field"])
        section.setSortAscending(spec["ascending"])
        level = {"level": spec["level"], "layer": spec["layer"].name(), "field": spec["field"],
                 "ascending": spec["ascending"], "body_layout": None}
        if spec["body"] is not None:
            body = spec["body"].clone()
            level["body_layout"] = spec["body"].name()
            level["maps_following_feature"] = _follow_feature(body)
            section.setBody(body)
            section.setBodyEnabled(True)
        else:
            section.setBodyEnabled(False)
        if spec["header"] is not None:
            section.setHeader(spec["header"].clone())
            section.setHeaderEnabled(True)
            level["header_layout"] = spec["header"].name()
        if spec["footer"] is not None:
            section.setFooter(spec["footer"].clone())
            section.setFooterEnabled(True)
            level["footer_layout"] = spec["footer"].name()
        parent.appendChild(section)
        parent = section
        described.append(level)

    if existing is not None:
        manager.removeLayout(existing)
    if not manager.addLayout(report):
        return tool_error(f"QGIS did not add the report '{name}' to the project.", "EXECUTION_FAILED",
                          "Call list_layouts: a layout of that name may have appeared meanwhile.")

    counted = walk(report, _INFO_WALK_SECONDS)
    out = {"report": name, "levels": described, "pages": counted["pages"]}
    if not counted["complete"]:
        out["pages"] = f"more than {counted['pages']}"
    out["pages_by_part"] = counted["by_part"]
    if counted["preview"]:
        out["first_pages"] = counted["preview"]
    if ends["cover"] is not None:
        out["cover_layout"] = ends["cover"].name()
    if ends["closing"] is not None:
        out["closing_layout"] = ends["closing"].name()
    if existing is not None:
        out["replaced"] = True
    notes = []
    for spec, level in zip(specs, described):

        if level.get("maps_following_feature") == 0 and spec["layer"].isSpatial():
            notes.append(f"No map of level {level['level']}'s body follows its feature: add_layout_map to "
                         f"{level['body_layout']}, then create_report again with replace:true.")
    for position, (outer, inner) in enumerate(zip(specs, specs[1:])):


        deeper = specs[position + 1:]
        if not any(spec[role] is not None for spec in deeper for role in ("header", "body", "footer")):
            break
        printed = sum(count for part, count in counted["by_part"].items()
                      if any(part.startswith(f"level {spec['level']} ") for spec in deeper))
        if counted["complete"] and outer["layer"].featureCount() > 0 and not printed:
            notes.append(f"No feature of {inner['layer'].name()} matched a {outer['field']} of level "
                         f"{outer['level']}: the two {outer['field']} fields must hold the same values.")
            break
    if notes:
        out["notes"] = notes
    out["next"] = f"export_layout with layout_name '{name}' and a .pdf output_path writes every page into one PDF."
    return out






def _parts(report) -> list:

    out = []

    def visit(section, depth):
        label = f"level {depth}" if depth else "report"
        for role, enabled, getter in (("header", "headerEnabled", "header"),
                                      ("body", "bodyEnabled", "body"),
                                      ("footer", "footerEnabled", "footer")):
            has = getattr(section, enabled, None)
            layout = getattr(section, getter, lambda: None)()
            if layout is not None and (has is None or has()):
                name = {"report header": "cover", "report footer": "closing"}.get(f"{label} {role}",
                                                                                 f"{label} {role}")
                out.append((name, layout))
        for child in section.childSections():
            visit(child, depth + 1)

    visit(report, 0)
    return out


def walk(report, seconds: float, on_step=None) -> dict:









    listed = _parts(report)
    parts = {id(layout): label for label, layout in listed}
    started = time.monotonic()
    pages = 0
    by_part: dict = {}
    preview: list = []
    complete = True
    if not report.beginRender():
        return {"pages": 0, "complete": True, "by_part": {}, "preview": []}
    try:
        while report.next():
            layout = report.layout()
            count = layout.pageCollection().pageCount()
            if on_step is not None and on_step(layout, count) is False:
                complete = False
                break
            pages += count
            label = parts.get(id(layout), "section")
            by_part[label] = by_part.get(label, 0) + count
            if len(preview) < 6:
                preview.append(_step_text(layout, label))
            if time.monotonic() - started > seconds:
                complete = not report.next()
                break
    finally:
        report.endRender()
    del listed
    return {"pages": pages, "complete": complete, "by_part": by_part, "preview": preview}


def _step_text(layout, label: str) -> str:

    try:
        context = layout.reportContext()
        feature = context.feature()
        fields = feature.fields()
        shown = ", ".join(f"{fields.at(i).name()} {feature.attribute(i)}" for i in range(min(2, fields.count())))
        return f"{label}: {shown}" if shown else label
    except (AttributeError, RuntimeError):
        return label


def _section_tree(section, depth: int = 1) -> list:

    from .advanced_layouts import _page_text

    out = []
    for child in section.childSections():
        node = {"level": depth, "type": child.type()}
        if isinstance(child, QgsReportSectionFieldGroup):
            layer = child.layer()
            node.update({"layer": layer.name() if layer is not None else None, "field": child.field(),
                         "ascending": child.sortAscending()})
            if child.bodyEnabled() and child.body() is not None:
                body = child.body()
                node["body"] = {"pages_per_feature": body.pageCollection().pageCount(), "page": _page_text(body),
                                "maps_following_feature": sum(
                                    1 for item in body.items()
                                    if isinstance(item, QgsLayoutItemMap) and item.atlasDriven())}
        elif getattr(child, "body", None) is not None and child.body() is not None:
            node["body"] = {"pages": child.body().pageCollection().pageCount()}
        node["header"] = bool(child.headerEnabled() and child.header() is not None)
        node["footer"] = bool(child.footerEnabled() and child.footer() is not None)
        inner = _section_tree(child, depth + 1)
        if inner:
            node["sections"] = inner
        out.append(node)
    return out


def report_summary(report) -> dict:

    counted = walk(report, _INFO_WALK_SECONDS)
    out = {"name": report.name(), "kind": "report",
           "cover": bool(report.headerEnabled() and report.header() is not None),
           "closing": bool(report.footerEnabled() and report.footer() is not None),
           "sections": _section_tree(report),
           "pages": counted["pages"] if counted["complete"] else f"more than {counted['pages']}",
           "pages_by_part": counted["by_part"]}
    if counted["preview"]:
        out["first_pages"] = counted["preview"]
    out["note"] = ("A report's pages are copies of print layouts, made when it was created: change the print "
                   "layout, then create_report again with replace:true.")
    return out






def _sample_seconds(layout, fmt: str, dpi: int, folder: str) -> float:

    exporter = QgsLayoutExporter(layout)
    started = time.monotonic()
    if fmt == "pdf":
        settings = QgsLayoutExporter.PdfExportSettings()
        settings.dpi = dpi
        exporter.exportToPdf(os.path.join(folder, "page.pdf"), settings)
    else:
        settings = QgsLayoutExporter.ImageExportSettings()
        settings.dpi = dpi
        exporter.exportToImage(os.path.join(folder, f"page.{fmt}"), settings)
    return time.monotonic() - started


def _plan(report, fmt: str, dpi: int, base: str, ext: str, budget: float, started: float) -> dict:








    scratch = tempfile.mkdtemp(prefix="terralab_report_")
    cost: dict = {}
    held: list = []
    state = {"seconds": 0.0, "fit": None, "names": [], "pages": 0}

    def step(layout, count):
        key = id(layout)
        if key not in cost:
            held.append(layout)
            cost[key] = _sample_seconds(layout, fmt, dpi, scratch)
        elapsed = time.monotonic() - started
        if state["fit"] is None and elapsed + state["seconds"] + cost[key] > budget:
            state["fit"] = state["pages"]
        state["seconds"] += cost[key]
        state["pages"] += count
        if base:


            first = report.filePath(base, ext)
            stem, suffix = os.path.splitext(first)
            state["names"].extend([first] + [f"{stem}_{page + 1}{suffix}" for page in range(1, count)])

        return state["fit"] is None or elapsed <= budget * _PLAN_WALK_SHARE

    try:
        counted = walk(report, budget, on_step=step)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    counted.update(state)
    return counted


def _refuse_over_budget(report, plan: dict, fmt: str, dpi: int, budget: float) -> dict:
    pages = plan["pages"]
    total = f"{pages}" if plan["complete"] else f"more than {pages}"
    per_page = plan["seconds"] / max(1, pages)
    fit = max(0, int(plan["fit"] or 0))
    first_field = ""
    sections = report.childSections()
    if sections and isinstance(sections[0], QgsReportSectionFieldGroup):
        first_field = sections[0].field()
    advice = (f"About {fit} pages fit in one export here. " if fit else
              "Not even the first pages fit in one export here. ")
    advice += (f"A dpi under {dpi} draws each page faster (pages drawn from rasters or tiles most of all), or "
               f"run create_report on a layer holding fewer {first_field or 'groups'} "
               "(native:extractbyexpression) and export each part.")
    return limits.refusal(
        f"Report '{report.name()}'", f"{total} pages at about {per_page:.2f} s each, measured on its first "
        f"pages, so about {plan['seconds']:.0f} s of export", f"{budget:.0f} s that one export may hold QGIS",
        advice)


def export_report(report, args: dict, fmt: str, output_path: str, dpi: int) -> dict:

    from .advanced_layouts import (
        _add_credit_label,
        _export_failure,
        _fit_dpi,
        _layout_credits,
        _output_folder,
        _page_text,
        _remove_credit_label,
    )

    started = time.monotonic()
    name = report.name()
    if fmt not in ("pdf",) + _IMAGE_FORMATS:
        return tool_error(f"A report exports to pdf, png, jpg or tif, not {fmt}.", "INVALID_ARGS",
                          "Pass a .pdf output_path for one file with every page.")
    for key, why in (("scale", "each body map follows its own feature"),
                     ("meters_per_pixel", "each body map follows its own feature, at its own scale"),
                     ("georeference", "its pages show different places")):
        if args.get(key) not in (None, False):
            return tool_error(f"{key} does not apply to a report: {why}.", "INVALID_ARGS",
                              f"Export the report without {key}; for one page at an exact scale or ground "
                              "resolution, export that print layout itself.")
    parts = _parts(report)
    if not parts:
        return tool_error(f"The report '{name}' has no section to print.", "INVALID_ARGS",
                          "Build it with create_report, which gives every level a body.")


    asked_dpi = dpi
    dpi_note = ""
    for _label, layout in parts:
        fitted, note = _fit_dpi(layout, int(dpi), fmt)
        if fitted < dpi:
            dpi, dpi_note = fitted, note

    created_folder, folder_error = _output_folder(output_path, args)
    if folder_error:
        return folder_error

    ext = os.path.splitext(output_path)[1].lstrip(".") or fmt
    base = "" if fmt == "pdf" else os.path.splitext(output_path)[0]
    budget = float(limits.main_budget("export_layout"))



    labels = []
    credits_seen = []
    try:
        for _label, layout in parts:
            credits = _layout_credits(layout)
            label = _add_credit_label(layout, credits)
            if label is not None:
                labels.append((layout, label))
                if credits not in credits_seen:
                    credits_seen.append(credits)

        plan = _plan(report, fmt, dpi, base, ext, budget, started)
        if plan["pages"] == 0:
            return tool_error(f"The report '{name}' has no page: its coverage layers have no feature.",
                              "INVALID_ARGS", "Check the layers with get_layout_info on the report.")
        if plan["fit"] is not None or not plan["complete"]:
            return _refuse_over_budget(report, plan, fmt, dpi, budget)
        names = plan["names"]
        overwrite = args.get("overwrite", False)
        taken = [path for path in names if os.path.exists(path)]
        if taken and not overwrite:
            return tool_error(f"{len(taken)} of the {len(names)} page files already exist, the first "
                              f"{taken[0]}.", "INVALID_ARGS",
                              "Pass overwrite:true to replace them, or another output_path.")

        if fmt == "pdf":
            settings = QgsLayoutExporter.PdfExportSettings()
            settings.dpi = dpi
            if args.get("force_vector"):
                try:
                    settings.forceVectorOutput = True
                except AttributeError:
                    pass
            result, detail = QgsLayoutExporter.exportToPdf(report, output_path, settings)
        else:
            settings = QgsLayoutExporter.ImageExportSettings()
            settings.dpi = dpi
            result, detail = QgsLayoutExporter.exportToImage(report, base, ext, settings)
    finally:
        for layout, label in labels:
            _remove_credit_label(layout, label)

    if result != enum_member(QgsLayoutExporter, "ExportResult", "Success"):
        failure = _export_failure(result)
        if detail:
            failure["_error"] += f" {detail}"
        return failure

    out = {"report": name, "format": fmt, "dpi": dpi, "pages": plan["pages"],
           "export_seconds": round(time.monotonic() - started, 1)}
    if fmt == "pdf":
        if not os.path.isfile(output_path):
            return tool_error(f"Export reported success but no file was found at {output_path}.")
        out["exported"] = output_path
        out["file_size"] = os.path.getsize(output_path)
    else:
        written = [path for path in names if os.path.isfile(path)]
        if not written:
            return tool_error(f"Export reported success but no page file was found next to {output_path}.")
        out["exported"] = written[0]
        out["files"] = [os.path.basename(path) for path in written[:20]]
        out["file_count"] = len(written)
        out["folder"] = os.path.dirname(written[0])
    first_body = next((layout for label, layout in parts if label.endswith("body")), parts[0][1])
    page_text = _page_text(first_body)
    if page_text:
        out["page_size"] = page_text
    if dpi_note:
        out["dpi_note"] = dpi_note
        out["dpi_lowered_from"] = int(asked_dpi)
    if credits_seen:
        out["attribution"] = " | ".join(credits_seen)
    if created_folder:
        out["created_folder"] = created_folder
    return out

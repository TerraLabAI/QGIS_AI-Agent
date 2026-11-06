# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later




from __future__ import annotations

import time

from qgis.core import QgsLayoutItemMap, QgsReportSectionFieldGroup

from .layout_facts import _page_text




_INFO_WALK_SECONDS = 2.0


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
    out["note_hint"] = "report_pages_are_copies"
    return out

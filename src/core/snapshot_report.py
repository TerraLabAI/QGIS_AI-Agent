# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A snapshot diff, said in words: the run summary lines and the layer chips."""




from __future__ import annotations


def QT_TRANSLATE_NOOP(context: str, text: str) -> str:  # noqa: N802 - Qt's marker name, read by scripts/extract_i18n.py
    """Qt's marker without Qt: the extractor files ``text`` under ``context``, and the text comes back unchanged for the caller's tr() to."""

    return text


def describe_diff(diff: dict) -> list:
    """The diff as short sentences, one per thing that changed, nothing for what did not."""



    if not isinstance(diff, dict):
        return []
    lines = []
    names = lambda items: ", ".join(str(i.get("name") or i.get("id") or "?") for i in items)  # noqa: E731
    added = diff.get("layers_added") or []
    if added:
        lines.append(f"{len(added)} layer{'s' if len(added) > 1 else ''} added: {names(added)}")
    removed = diff.get("layers_removed") or []
    if removed:
        lines.append(f"{len(removed)} layer{'s' if len(removed) > 1 else ''} removed: {names(removed)}")
    for c in diff.get("feature_count_changes") or []:
        lines.append(f"{c.get('name')}: {c.get('before')} to {c.get('after')} features")
    for c in diff.get("crs_changes") or []:
        lines.append(f"{c.get('name')}: CRS {c.get('before')} to {c.get('after')}")
    files = diff.get("files_changed") or []
    if files:
        lines.append(f"{len(files)} file{'s' if len(files) > 1 else ''} written on disk: {names(files)}")
    styled = diff.get("style_changes") or []
    if styled:
        lines.append(f"{len(styled)} layer{'s' if len(styled) > 1 else ''} restyled: {names(styled)}")
    for c in diff.get("visibility_changes") or []:
        lines.append(f"{c.get('name')}: {'shown' if c.get('visible') else 'hidden'}")
    for c in diff.get("project_changes") or []:
        if c.get("what") == "crs":
            lines.append(f"Project CRS {c.get('before')} to {c.get('after')}")
        elif c.get("what") == "layer_tree":
            lines.append(QT_TRANSLATE_NOOP("AgentController", "Layer order or groups changed"))
    orphans = diff.get("orphan_temporary_layers") or []
    if orphans:
        lines.append(f"Temporary layer{'s' if len(orphans) > 1 else ''} left outside the layer tree: {names(orphans)}")
    return lines


def diff_changed(diff: dict) -> bool:
    """True when the diff saw anything a restore would put back: a layer, a file, a style, a visibility flip, the project CRS or the layer tree."""

    if not isinstance(diff, dict):
        return False
    return bool(int(diff.get("changed_layers") or 0) > 0 or diff.get("project_changes"))


def changed_layer_items(diff: dict) -> list[dict]:
    """One entry per layer the run touched: ``{"id", "name", "what"}``, in the order added, counts, CRS, files, style, visibility, removed."""






    if not isinstance(diff, dict):
        return []
    items: list[dict] = []
    seen: set[str] = set()

    def add(entry: dict, what: str, **extra) -> None:
        lid = str(entry.get("id") or "")
        name = str(entry.get("name") or lid or "?")
        key = lid or name
        if key in seen:
            return
        seen.add(key)
        items.append({"id": lid, "name": name, "what": what, **extra})

    for entry in diff.get("layers_added") or []:
        add(entry, "added")
    for entry in diff.get("feature_count_changes") or []:


        fine = {k: int(entry[k]) for k in ("added", "removed", "changed")
                if isinstance(entry.get(k), int) and not isinstance(entry.get(k), bool)}
        add(entry, "features", delta=int(entry.get("delta") or 0), **fine)
    for entry in diff.get("crs_changes") or []:
        add(entry, "crs")
    for entry in diff.get("files_changed") or []:
        add(entry, "file")
    for entry in diff.get("style_changes") or []:
        add(entry, "style")
    for entry in diff.get("visibility_changes") or []:
        add(entry, "visibility", visible=bool(entry.get("visible")))
    for entry in diff.get("layers_removed") or []:
        add(entry, "removed")
    return items



_CHIP_ORDER = ("added", "features", "crs", "style", "visibility", "file", "removed")


def _whole(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def run_change_items(report, touched=None) -> dict:
    """What the chips under a run's answer show, as plain JSON a thread file keeps."""















    try:
        return _run_change_items(report if isinstance(report, dict) else {},
                                 [x for x in touched if isinstance(x, dict)]
                                 if isinstance(touched, (list, tuple)) else [])
    except Exception:  # noqa: BLE001 - chips that cannot be built are no chips, never a failed run end
        return {}


def _run_change_items(report: dict, touched: list) -> dict:
    def listed(key: str) -> list:
        value = report.get(key)
        return [x for x in value if isinstance(x, dict)] if isinstance(value, list) else []

    has_report = any(isinstance(report.get(key), list)
                     for key in ("layers_added", "layers_changed", "layers_removed", "files_written"))
    counted = {str(x.get("id") or ""): x for x in touched if x.get("what") == "features"}
    buckets: dict[str, list] = {what: [] for what in _CHIP_ORDER}
    seen: set[str] = set()

    def add(entry: dict, what: str, **extra) -> None:
        lid = str(entry.get("id") or "")
        name = str(entry.get("name") or lid or "?")
        if (lid or name) in seen:
            return
        seen.add(lid or name)
        buckets[what].append({"id": lid, "name": name, "what": what,
                              **{key: value for key, value in extra.items() if value is not None}})

    for entry in listed("layers_added"):
        add(entry, "added", kind=entry.get("kind") if isinstance(entry.get("kind"), str) else None,
            features=_whole(entry.get("features")))
    for entry in listed("layers_changed"):
        before, after = _whole(entry.get("features_before")), _whole(entry.get("features_after"))
        if before is not None and after is not None:
            fine = counted.get(str(entry.get("id") or ""), {})
            add(entry, "features", delta=after - before,
                **{key: _whole(fine.get(key)) for key in ("added", "removed", "changed")})
        elif entry.get("crs_before") or entry.get("crs_after"):
            add(entry, "crs")
    for entry in listed("layers_removed"):
        add(entry, "removed")
    for entry in touched:
        what = str(entry.get("what") or "")
        if what not in buckets or (has_report and what == "file"):
            continue
        extra = {key: entry[key] for key in ("delta", "added", "removed", "changed")
                 if _whole(entry.get(key)) is not None}
        if what == "visibility":
            extra["visible"] = bool(entry.get("visible"))
        add(entry, what, **extra)

    raw = report.get("warnings")
    warnings = [text for text in raw if isinstance(text, str) and text.strip()] if isinstance(raw, list) else []

    out: dict = {}
    layers = [item for what in _CHIP_ORDER for item in buckets[what]]
    if layers:
        out["layers"] = layers
    if warnings:
        out["warnings"] = warnings
    return out

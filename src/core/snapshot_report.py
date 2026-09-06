# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""A snapshot diff, said in words: the run summary lines and the layer chips."""




from __future__ import annotations


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
            lines.append("Layer order or groups changed")
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

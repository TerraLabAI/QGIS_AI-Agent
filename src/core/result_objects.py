# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later










from __future__ import annotations

from . import layout_show

KINDS = ("layout", "view3d", "table", "theme")


def text_of(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def result_objects(name: str, args, result) -> list[dict]:

    try:
        if not isinstance(result, dict) or "_error" in result:
            return []
        out: list[dict] = []

        def add(kind, label, ident="") -> None:
            label = text_of(label)
            if kind in KINDS and label:
                out.append({"kind": kind, "name": label, **({"id": ident} if text_of(ident) else {})})

        add("layout", layout_show.built_layout(name, args if isinstance(args, dict) else {}))
        stated = result.get("object")
        if isinstance(stated, dict):
            add(stated.get("kind"), stated.get("name"), stated.get("id"))
        changed = result.get("changed")
        for key in ("layouts_added", "layouts_changed"):
            names = changed.get(key) if isinstance(changed, dict) else None
            for label in (names if isinstance(names, dict) else ()):
                add("layout", label)
        return out
    except Exception:  # noqa: BLE001
        return []


def merge(kept: list, found: list) -> None:

    known = {(entry["kind"], entry.get("id") or entry["name"]) for entry in kept}
    for entry in found:
        key = (entry["kind"], entry.get("id") or entry["name"])
        if key not in known:
            known.add(key)
            kept.append(entry)

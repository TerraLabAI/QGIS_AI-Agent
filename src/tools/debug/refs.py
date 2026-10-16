# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later











from __future__ import annotations

from typing import Any

from .widgets import is_alive

_PARENT_LEVELS = 3


def structural_sig(widget) -> tuple | None:






    try:
        cls = widget.metaObject().className()
        obj_name = widget.objectName() or ""
    except Exception:
        return None
    parent = None
    try:
        parent = widget.parentWidget()
    except Exception:
        parent = None
    index = -1
    if parent is not None:
        try:
            siblings = [c for c in parent.children() if c.metaObject().className() == cls]
            index = siblings.index(widget)
        except Exception:
            index = -1
    chain = []
    node = parent
    for _ in range(_PARENT_LEVELS):
        if node is None:
            break
        try:
            chain.append((node.metaObject().className(), node.objectName() or ""))
            node = node.parentWidget()
        except Exception:
            break
    label = ""
    if not obj_name and index < 0:
        from .widgets import widget_label

        try:
            label = widget_label(widget)
        except Exception:
            label = ""
    return (cls, obj_name, index, tuple(chain), label)


class SessionRefStore:
    def __init__(self):
        self._by_ref: dict[str, Any] = {}
        self._counter = 0
        self._sig: dict[str, tuple] = {}


        self._items: dict[str, tuple] = {}
        self._item_keys: dict[tuple, str] = {}
        self._item_counter = 0

    def assign(self, widgets) -> dict[str, Any]:













        for existing_ref in list(self._by_ref.keys()):
            if not is_alive(self._by_ref.get(existing_ref)):
                self._by_ref.pop(existing_ref, None)
        self._prune_items()

        current: dict[str, Any] = {}
        for widget in widgets:
            sig = structural_sig(widget)
            cached = None
            try:
                cached = widget.property("_mcpRef")
            except Exception:
                cached = None
            bound = self._by_ref.get(cached) if cached else None
            if cached and self._sig.get(cached) == sig and (bound is widget or not is_alive(bound)):


                ref = cached
            else:
                self._counter += 1
                ref = f"@w{self._counter}"
                try:
                    widget.setProperty("_mcpRef", ref)
                except Exception:  # nosec B110
                    pass
                self._sig[ref] = sig
            self._by_ref[ref] = widget
            current[ref] = widget
        return dict(current)



    def _prune_items(self):
        for ref in list(self._items.keys()):
            view, persistent = self._items[ref]
            if not is_alive(view) or not persistent.isValid():
                self._items.pop(ref, None)
        live = set(self._items.keys())
        for key, ref in list(self._item_keys.items()):
            if ref not in live:
                self._item_keys.pop(key, None)

    def assign_item(self, view, index) -> str:






        from qgis.PyQt.QtCore import QPersistentModelIndex

        from .widgets import item_path_of

        try:
            view_key = view.property("_mcpRef") or f"obj{id(view)}"
        except Exception:
            view_key = f"obj{id(view)}"
        key = (str(view_key), item_path_of(index), index.column())
        ref = self._item_keys.get(key)
        if ref is None or ref not in self._items:
            self._item_counter += 1
            ref = f"@i{self._item_counter}"
            self._item_keys[key] = ref
        self._items[ref] = (view, QPersistentModelIndex(index))
        return ref


_STORE = SessionRefStore()


def get_ref_store() -> SessionRefStore:
    return _STORE

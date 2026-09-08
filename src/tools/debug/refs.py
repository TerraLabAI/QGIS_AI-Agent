# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stable widget refs for the accessibility snapshot (Playwright-style)."""










from __future__ import annotations

from typing import Any

from .widgets import is_alive

_PARENT_LEVELS = 3


def normalize_ref(ref: str) -> str:
    text = (ref or "").strip()
    if not text:
        return ""
    return text if text.startswith("@") else f"@{text}"


def is_item_ref(ref: str) -> bool:
    """True for an item-view row ref (@i7), false for a widget ref (@w7)."""
    normalized = normalize_ref(ref)
    return len(normalized) > 2 and normalized[1] == "i" and normalized[2:].isdigit()


def structural_sig(widget) -> tuple | None:
    """Identity of a widget by its place in the tree, not by what it says."""





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


def _index_from_persistent(persistent):
    """QModelIndex from a QPersistentModelIndex, or None when it died."""
    from qgis.PyQt.QtCore import QModelIndex

    if persistent is None or not persistent.isValid():
        return None
    try:
        index = QModelIndex(persistent)
        if index.isValid():
            return index
    except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
        pass
    model = persistent.model()
    if model is None:
        return None
    index = model.index(persistent.row(), persistent.column(), persistent.parent())
    return index if index.isValid() else None


class SessionRefStore:
    def __init__(self):
        self._by_ref: dict[str, Any] = {}
        self._generation = 0
        self._counter = 0
        self._sig: dict[str, tuple] = {}
        self._baseline: dict[str, list[dict]] = {}
        self._last_action_key: str | None = None
        self._last_fingerprint: str | None = None
        self._noop_count = 0


        self._items: dict[str, tuple] = {}
        self._item_keys: dict[tuple, str] = {}
        self._item_counter = 0

        self.last_rebound: str | None = None

    def reset(self):
        self._by_ref.clear()
        self._baseline.clear()
        self._sig.clear()
        self._items.clear()
        self._item_keys.clear()
        self._item_counter = 0
        self._generation = 0
        self._counter = 0
        self._last_action_key = None
        self._last_fingerprint = None
        self._noop_count = 0
        self.last_rebound = None

    def note_action(self, action_key: str, fingerprint: str) -> int:
        """Track repeated no-effect actions."""






        if action_key == self._last_action_key and fingerprint == self._last_fingerprint:
            self._noop_count += 1
        else:
            self._noop_count = 0
        self._last_action_key = action_key
        self._last_fingerprint = fingerprint
        return self._noop_count

    def assign(self, widgets) -> dict[str, Any]:
        """Map widgets to STABLE @wN refs, MERGING across snapshots."""












        self._generation += 1


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
                except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
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
        """Mint or reuse an @iN ref for one (view, model index) row."""





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

    def resolve_item(self, ref: str):
        """Return (view, index, None) or (None, None, error) for an @iN row ref."""
        normalized = normalize_ref(ref)
        entry = self._items.get(normalized)
        if entry is None:
            return None, None, {
                "_error": f"Unknown row ref {normalized}. Call accessibility_snapshot to list the "
                          "view's rows again.",
                "_code": "UNKNOWN_REF",
            }
        view, persistent = entry
        if not is_alive(view):
            self._items.pop(normalized, None)
            return None, None, {
                "_error": f"Stale row ref {normalized}: the view it belonged to is gone.",
                "_code": "STALE_REF",
            }
        index = _index_from_persistent(persistent)
        if index is None:
            self._items.pop(normalized, None)
            return None, None, {
                "_error": f"Stale row ref {normalized}: the row no longer exists (the model changed).",
                "_code": "STALE_REF",
                "_suggestion": "Re-run accessibility_snapshot on the view to get current row refs.",
            }
        return view, index, None

    def resolve(self, ref: str):
        """Return (widget, None) or (None, error_dict) for a @wN ref."""








        self.last_rebound = None
        if not ref:
            return None, {"_error": "Empty ref"}
        normalized = normalize_ref(ref)
        if is_item_ref(normalized):
            view, _index, error = self.resolve_item(normalized)
            if error is not None:
                return None, error



            return view, None
        widget = self._by_ref.get(normalized)
        if widget is not None and is_alive(widget):
            return widget, None

        known = widget is not None or normalized in self._sig
        if widget is not None:
            self._by_ref.pop(normalized, None)

        sig = self._sig.get(normalized)
        if sig is not None:
            rebound = self._rebind_from_sig(normalized, sig)
            if rebound is not None:
                return rebound, None

        if not known:
            return None, {
                "_error": f"Unknown ref {normalized}. Call accessibility_snapshot to (re)assign refs.",
            }
        return None, {
            "_error": f"Stale ref {normalized}: the widget no longer exists and no unique replacement "
                      "was found. Call accessibility_snapshot again.",
        }

    def _rebind_from_sig(self, ref: str, sig):
        """Find the unique current widget matching a dead ref's signature and re-bind `ref` to it."""





        try:
            cls, obj_name = sig[0], sig[1]
        except Exception:
            return None
        from .widgets import descendants, top_level_roots

        exact = []
        by_name = []
        seen = set()
        for root in top_level_roots():
            try:
                candidates = descendants(root)
            except Exception:  # nosec B112 - the debug report reads what answers and skips what does not
                continue
            for w in candidates:
                key = id(w)
                if key in seen or not is_alive(w):
                    continue
                seen.add(key)
                try:
                    if w.metaObject().className() != cls:
                        continue
                except Exception:  # nosec B112 - the debug report reads what answers and skips what does not
                    continue
                if structural_sig(w) == sig:
                    exact.append(w)
                    continue
                try:
                    if obj_name and w.objectName() == obj_name:
                        by_name.append(w)
                except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
                    pass
        match = exact[0] if len(exact) == 1 else (by_name[0] if len(by_name) == 1 else None)
        if match is None:
            return None
        self._by_ref[ref] = match
        self._sig[ref] = sig
        try:
            match.setProperty("_mcpRef", ref)
        except Exception:  # nosec B110 - the debug report reads what answers and skips what does not
            pass
        self.last_rebound = ref
        return match



    def set_baseline(self, root_key: str, nodes: list[dict]):
        self._baseline[root_key] = nodes




    _DIFF_FIELDS = ("text", "value", "checked", "enabled", "visible", "current", "tabs")
    _DIFF_DEFAULTS = {"enabled": True, "visible": True}

    def diff(self, root_key: str, nodes: list[dict]) -> dict:
        """Diff current nodes vs the stored baseline for root_key."""






        previous = self._baseline.get(root_key)
        self._baseline[root_key] = nodes
        if previous is None:
            return {"baseline_set": True, "nodes": len(nodes)}

        prev_map = {n.get("ref"): n for n in previous}
        cur_map = {n.get("ref"): n for n in nodes}
        added = [cur_map[k] for k in cur_map.keys() - prev_map.keys()]
        removed = [prev_map[k] for k in prev_map.keys() - cur_map.keys()]
        changed = []
        for key in cur_map.keys() & prev_map.keys():
            now, before = cur_map[key], prev_map[key]
            fields = {}
            for field in self._DIFF_FIELDS:
                default = self._DIFF_DEFAULTS.get(field, None)
                was, is_now = before.get(field, default), now.get(field, default)
                if was != is_now:
                    fields[field] = [was, is_now]
            if fields:
                entry = dict(now)
                entry["changed"] = fields
                changed.append(entry)
        return {"added": added, "removed": removed, "changed": changed}


_STORE = SessionRefStore()


def get_ref_store() -> SessionRefStore:
    return _STORE


def reset_ref_store():
    _STORE.reset()

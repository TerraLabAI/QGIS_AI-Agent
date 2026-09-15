# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layer resolution by name or id, forgiving about spelling."""











from __future__ import annotations

import difflib
import os
import re
import unicodedata

_STOPWORDS = {"the", "a", "an", "of", "layer", "in", "for", "data", "dataset"}
MATCH_THRESHOLD = 0.6


def wfs_feature_cap(layer) -> int | None:
    """The most features a WFS layer downloads (its maxNumFeatures), or None for any other layer."""
    try:
        if str(layer.providerType()).lower() != "wfs":
            return None
        from qgis.core import QgsDataSourceUri

        cap = str(QgsDataSourceUri(layer.source()).param("maxNumFeatures") or "")
    except Exception:  # noqa: BLE001 - a layer that cannot be read has no cap we know
        return None
    return int(cap) if cap.isdigit() and int(cap) > 0 else None


def loaded_feature_count(layer, count):
    """*count* (``featureCount``) no higher than what the layer holds."""




    cap = wfs_feature_cap(layer)
    if cap is None or not isinstance(count, int) or count < 0:
        return count
    return min(count, cap)


def normalize_name(name: str) -> str:
    """Lowercase, fold accents, collapse separators to spaces, drop filler words."""
    name = (name or "").lower()





    name = "".join(c for c in unicodedata.normalize("NFKD", name)
                   if not unicodedata.combining(c))
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    tokens = [t for t in name.split() if t not in _STOPWORDS]
    return " ".join(tokens)


def match_names(query: str, names: list[str], threshold: float = MATCH_THRESHOLD) -> tuple[list[str], str]:
    """Names matching ``query`` and the stage that produced them."""




    if not names:
        return [], "none"
    query_lower = (query or "").lower().strip()
    if not query_lower:
        return [], "none"

    exact = [n for n in names if n == query]
    if len(exact) == 1:
        return exact, "exact"
    if len(exact) > 1:
        return exact, "exact"

    ci = [n for n in names if n.lower() == query_lower]
    if ci:
        return ci, "case"

    normalized_query = normalize_name(query)
    if not normalized_query:
        return [], "none"
    normalized = {n: normalize_name(n) for n in names}

    same = [n for n, norm in normalized.items() if norm == normalized_query]
    if same:
        return same, "normalized"

    substring = [
        n for n, norm in normalized.items()
        if norm and (normalized_query in norm or norm in normalized_query)
    ]
    if substring:
        return substring, "substring"

    scored = sorted(
        ((difflib.SequenceMatcher(None, normalized_query, norm).ratio(), n) for n, norm in normalized.items()),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] >= threshold:
        best = scored[0][0]

        close = [n for score, n in scored if score >= threshold and score >= best - 0.05]
        return close, "fuzzy"
    return [], "none"


def closest_names(query: str, names: list[str], limit: int = 3) -> list[str]:
    """The best few candidates for a "did you mean" hint, most similar first."""
    normalized_query = normalize_name(query)
    scored = []
    for name in names:
        ratio = difflib.SequenceMatcher(None, normalized_query, normalize_name(name)).ratio()
        raw = difflib.SequenceMatcher(None, (query or "").lower(), name.lower()).ratio()
        scored.append((max(ratio, raw), name))
    scored.sort(key=lambda item: item[0], reverse=True)
    out = []
    for score, name in scored:
        if score < 0.4 or name in out:
            continue
        out.append(name)
        if len(out) >= limit:
            break
    return out


def resolve_layer(name_or_id: str):
    """The project layer for ``name_or_id`` (id first, then the matcher), or None."""



    from qgis.core import QgsProject

    project = QgsProject.instance()
    layer = project.mapLayer(name_or_id)
    if layer:
        return layer
    layers = list(project.mapLayers().values())
    by_name: dict[str, list] = {}
    for candidate in layers:
        by_name.setdefault(candidate.name(), []).append(candidate)
    matched, _stage = match_names(name_or_id, list(by_name.keys()))
    if len(matched) != 1:
        return None
    hits = by_name[matched[0]]
    if len(hits) != 1:




        in_tree = [layer for layer in hits if _has_tree_node(project, layer)]
        if len(in_tree) == 1:
            return in_tree[0]





        same_source = in_tree or hits
        if len(same_source) > 1 and _one_source(same_source):
            return same_source[0]
        return None
    return hits[0]


def _one_source(layers) -> bool:
    """Whether every layer in ``layers`` reads exactly the same data source."""







    sources = set()
    for layer in layers:
        try:
            sources.add(_source_key(layer.source()))
        except Exception:  # noqa: BLE001 - a layer that cannot say what it reads is not a twin
            return False
    return len(sources) == 1 and "" not in sources


def _source_key(source: str) -> str:
    """One spelling of a data source, so two spellings of one file match."""






    text = str(source or "").strip()
    if not text or "=" in text.split("|", 1)[0]:
        return text
    head, sep, tail = text.partition("|")
    try:
        head = os.path.normcase(os.path.abspath(head)).replace("\\", "/")
    except (OSError, ValueError):
        return text
    return head + sep + tail


def _has_tree_node(project, layer) -> bool:
    try:
        return project.layerTreeRoot().findLayer(layer.id()) is not None
    except Exception:  # noqa: BLE001 - a project without a tree tells us nothing either way
        return False


def layer_not_found(name_or_id: str) -> dict:
    """An error result with a code, the closest names, and the next tool to call."""
    from qgis.core import QgsProject

    project = QgsProject.instance()
    layers = list(project.mapLayers().values())
    names = sorted({layer.name() for layer in layers})
    same_name = [layer for layer in layers if layer.name() == name_or_id]
    matched, stage = match_names(name_or_id, names)
    if len(same_name) > 1 or (
        len(matched) == 1 and len([lyr for lyr in layers if lyr.name() == matched[0]]) > 1
    ):
        target = name_or_id if len(same_name) > 1 else matched[0]
        candidates = [
            {"id": layer.id(), "name": layer.name(), "source": layer.source()}
            for layer in layers if layer.name() == target
        ]
        listing = ", ".join(f"{c['id']} ({c['source']})" for c in candidates)
        return {
            "_error": f"Layer name {target!r} is ambiguous ({len(candidates)} layers). Use a layer id: [{listing}]",
            "code": "LAYER_NOT_FOUND",
            "suggestion": "Pass one of the listed layer ids instead of the name.",
            "_ambiguous": True,
            "_candidates": candidates,
        }
    if len(matched) > 1:
        shown = ", ".join(repr(n) for n in matched[:6])
        return {
            "_error": f"Layer {name_or_id!r} matches several layers: {shown}.",
            "code": "LAYER_NOT_FOUND",
            "suggestion": f"Pass the exact name of one of them: {shown}. list_layers shows every layer.",
            "_suggestions": matched[:6],
        }
    suggestions = closest_names(name_or_id, names)
    msg = f"Layer not found: {name_or_id!r}."
    if suggestions:
        msg += f" Did you mean: {', '.join(repr(s) for s in suggestions)}?"
        suggestion = f"Closest layers: {', '.join(repr(s) for s in suggestions)}. Call list_layers to see them all."
    elif names:
        msg += f" Available: {', '.join(repr(n) for n in names[:8])}"
        if len(names) > 8:
            msg += f" (+{len(names) - 8} more)"
        suggestion = "Call list_layers and pass the exact layer name or id."
    else:
        msg += " No layers in project."
        suggestion = "Load a layer first (add_data), then call the tool again."
    return {"_error": msg, "code": "LAYER_NOT_FOUND", "suggestion": suggestion, "_suggestions": suggestions}






PINNED_LAYER_KEYS = ("layer_name", "layer", "layer_id", "target_layer")

OUTPUT_LAYER_KEYS = {"execute_sql": ("layer_name",)}


def pin_layer_names(args: dict, tool: str = "") -> dict | None:
    """Point a data-changing call's layer arguments at one layer id, or refuse it."""















    if not isinstance(args, dict):
        return None
    from qgis.core import QgsProject

    project = QgsProject.instance()
    skipped = OUTPUT_LAYER_KEYS.get(tool, ())
    for key in PINNED_LAYER_KEYS:
        if key in skipped:
            continue
        value = args.get(key)
        if not isinstance(value, str) or not value.strip() or project.mapLayer(value) is not None:
            continue
        layers = [layer for layer in project.mapLayers().values() if layer is not None]
        matched, stage = match_names(value, sorted({layer.name() for layer in layers}))
        if stage in ("none", "exact"):
            continue
        if stage in ("case", "normalized"):
            layer = resolve_layer(value)
            if layer is not None:
                args[key] = layer.id()
                continue
        candidates = [{"id": layer.id(), "name": layer.name()} for layer in layers if layer.name() in matched]
        return _loose_name_refusal(value, candidates)
    return None


def _loose_name_refusal(value: str, candidates: list[dict]) -> dict:
    """LAYER_NOT_FOUND for a name a data-changing call may not guess from."""
    shown = candidates[:6]
    listing = ", ".join(f"{c['name']!r} (id {c['id']})" for c in shown)
    if len(candidates) > len(shown):
        listing += f" and {len(candidates) - len(shown)} more"
    if len(candidates) == 1:
        message = (f"No layer is named {value!r}; the closest is {listing}. This call changes layer data, "
                   "so it does not act on a guessed layer.")
    else:

        message = (f"Layer {value!r} matches several layers: {listing}. This call changes layer data, "
                   "so it does not pick one.")
    return {
        "_error": message,
        "code": "LAYER_NOT_FOUND",
        "suggestion": ("Pass the id of the layer you mean. When the user's request does not say which one, "
                       "ask them with ask_user before changing anything."),
        "_ambiguous": len(candidates) > 1,
        "_candidates": candidates,
    }


def unique_layer_name(name: str, keep_id: str = "") -> str:
    """``name``, or the first "name (2)", "name (3)" no other layer answers to."""







    from qgis.core import QgsProject

    wanted = (name or "").strip()
    if not wanted:
        return wanted
    try:
        taken = {layer.name() for layer in QgsProject.instance().mapLayers().values()
                 if layer is not None and layer.id() != keep_id}
    except Exception:  # noqa: BLE001 - no project to collide with
        return wanted
    if wanted not in taken:
        return wanted
    for number in range(2, 100):
        candidate = f"{wanted} ({number})"
        if candidate not in taken:
            return candidate
    return wanted


def is_scratch_layer(layer) -> bool:
    """A layer holding a run's intermediate: in memory, or a file in a temporary directory."""
    import tempfile

    from ..core.policy import AGENT_TMP_DIR

    try:
        if layer.providerType() == "memory":
            return True
        source = os.path.realpath(layer.source().split("|", 1)[0])
    except Exception:  # noqa: BLE001 - a layer we cannot read is not ours to rename
        return False
    roots = {os.path.realpath(AGENT_TMP_DIR), os.path.realpath(tempfile.gettempdir())}
    return any(source.startswith(root + os.sep) for root in roots if root and root != os.sep)


def take_layer_name(name: str, keep_id: str) -> tuple[str, list[dict]]:
    """The name for the layer ``keep_id``, taking it from intermediates that hold it."""










    from qgis.core import QgsProject

    wanted = (name or "").strip()
    if not wanted:
        return wanted, []
    try:
        holders = [layer for layer in QgsProject.instance().mapLayers().values()
                   if layer is not None and layer.id() != keep_id and layer.name() == wanted]
    except Exception:  # noqa: BLE001 - no project to collide with
        return wanted, []
    if not holders:
        return wanted, []
    if not all(is_scratch_layer(layer) for layer in holders):
        return unique_layer_name(wanted, keep_id=keep_id), []
    renamed = []
    for layer in holders:
        previous = unique_layer_name(f"{wanted} (previous)", keep_id=layer.id())
        layer.setName(previous)
        renamed.append({"layer_id": layer.id(), "now_named": previous})
    return wanted, renamed

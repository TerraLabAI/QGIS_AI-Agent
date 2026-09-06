# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
























from __future__ import annotations

import difflib
import functools
import os
import re

from ..core.logger import log_debug


_FILLER_WORDS = frozenset(["a", "an", "the", "of", "in", "for", "layer", "data", "dataset"])
MATCH_THRESHOLD = 0.6

MATCH_MARGIN = 0.15

INSIDE_COVER = 0.6





_ID_TAIL = re.compile(r"_(?:[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{12}|\d{17,20})$",
                      re.IGNORECASE)









_NAME_SUFFIX = re.compile(
    r"(?:\s+\d+\s*/\s*\d+"
    r"|\s*\[\s*\d+\s*\]"
    r"|\s*\(\s*\d[^()]{0,24}\)"
    r"|\s*(?:[-_]\s*)?\(\s*(?:previous|copy|copie|kopie|duplicate|styled|categorized|categorised|"
    r"graduated|classified|filtered|clipped|buffered)\s*\))+$",
    re.IGNORECASE)


def wfs_feature_cap(layer) -> int | None:

    try:
        if str(layer.providerType()).lower() != "wfs":
            return None
        from qgis.core import QgsDataSourceUri

        cap = str(QgsDataSourceUri(layer.source()).param("maxNumFeatures") or "")
    except Exception:  # noqa: BLE001
        return None
    return int(cap) if cap.isdigit() and int(cap) > 0 else None


def loaded_feature_count(layer, count):





    cap = wfs_feature_cap(layer)
    if cap is None or not isinstance(count, int) or count < 0:
        return count
    return min(count, cap)


@functools.lru_cache(maxsize=1024)
def normalize_name(name: str) -> str:










    from ..core.layer_rank import normalized

    return " ".join(token for token in normalized(name).split() if token not in _FILLER_WORDS)


@functools.lru_cache(maxsize=1024)
def strip_id_tail(text: str) -> str:

    return _ID_TAIL.sub("", str(text or "")).strip()


def strip_suffix(text: str) -> str:

    return _NAME_SUFFIX.sub("", str(text or "")).strip()


@functools.lru_cache(maxsize=1024)
def bare_name(text: str) -> str:

    return strip_suffix(strip_id_tail(text)) or strip_id_tail(text)


def _squashed(norm: str) -> str:

    return norm.replace(" ", "")


def _ratio(query: str, name: str) -> float:
    return difflib.SequenceMatcher(None, query, name).ratio()


def _best_alone(query: str, pool: list[str], table: dict[str, str]) -> str | None:






    scored = sorted(((_ratio(query, table.get(name, "")), name) for name in pool), reverse=True)
    if len(scored) == 1:
        return scored[0][1]
    if scored and scored[0][0] - scored[1][0] >= MATCH_MARGIN:
        return scored[0][1]
    return None


def match_names(query: str, names: list[str], threshold: float = MATCH_THRESHOLD) -> tuple[list[str], str]:








    if not names:
        return [], "none"
    raw = str(query or "").strip()
    if not raw:
        return [], "none"
    query_lower = raw.lower()

    exact = [n for n in names if n == query]
    if exact:
        return exact, "exact"
    if len(raw) > 1024:
        return [], "none"

    ci = [n for n in names if n.lower().strip() == query_lower]
    if ci:
        return ci, "case"

    normalized = {n: normalize_name(n) for n in names}
    normalized_query = normalize_name(raw)
    if normalized_query:
        same = [n for n, norm in normalized.items() if norm == normalized_query]
        if same:
            return same, "normalized"


        wanted = frozenset(normalized_query.split())
        if len(wanted) > 1:
            reordered = [n for n, norm in normalized.items() if norm and frozenset(norm.split()) == wanted]
            if reordered:
                return reordered, "tokens"

    bare = {n: normalize_name(bare_name(n)) for n in names}
    bare_query = normalize_name(bare_name(raw))
    if not bare_query:
        return [], "none"
    if bare_query != normalized_query or any(bare[n] != normalized[n] for n in names):
        trimmed = [n for n, norm in bare.items() if norm and norm == bare_query]
        if trimmed:
            return trimmed, "trimmed"

    squashed_query = _squashed(bare_query)
    squashed = [n for n, norm in bare.items() if norm and _squashed(norm) == squashed_query]
    if squashed:
        return squashed, "squashed"




    holds = [n for n, norm in bare.items() if norm and bare_query in norm]



    inside = [n for n, norm in bare.items()
              if norm and norm in bare_query and len(norm) >= INSIDE_COVER * len(bare_query)]
    pool = holds or inside
    if pool:
        if len(pool) == 1:
            return pool, "substring"
        best = _best_alone(bare_query, pool, bare)
        return ([best] if best else pool), "substring"

    scored = sorted(
        ((max(_ratio(bare_query, norm), _ratio(bare_name(raw).lower(), n.lower())), n)
         for n, norm in bare.items()),
        key=lambda item: item[0],
        reverse=True,
    )
    if scored and scored[0][0] >= threshold:
        best = scored[0][0]

        close = [n for score, n in scored if score >= threshold and score >= best - 0.05]
        return close, "fuzzy"
    return [], "none"


def _closeness(query: str, name: str) -> float:

    if max(len(str(query or "")), len(name)) > 1024:
        return 1.0 if query == name else 0.0
    bare_query, bare = normalize_name(bare_name(query)), normalize_name(bare_name(name))
    score = max(_ratio(bare_query, bare), _ratio(str(query or "").lower(), name.lower()))
    if bare_query and bare and (bare_query in bare or bare in bare_query):
        score = max(score, 0.75)
    shared = frozenset(bare_query.split()) & frozenset(bare.split())
    if shared and any(len(word) >= 4 for word in shared):
        score = max(score, 0.55)
    return score


def closest_names(query: str, names: list[str], limit: int = 3) -> list[str]:

    scored = sorted(((_closeness(query, name), name) for name in set(names)),
                    key=lambda item: (-item[0], item[1]))
    out: list[str] = []
    for score, name in scored:
        if score < 0.4 or name in out:
            continue
        out.append(name)
        if len(out) >= limit:
            break
    return out


def closest_layers(query: str, layers: list, limit: int = 3, floor: float = 0.4) -> list[dict]:






    scored = []
    for layer in layers:
        try:
            scored.append((_closeness(query, layer.name()), layer.name(), layer.id()))
        except Exception as exc:  # noqa: BLE001
            log_debug(f"closest_layers: reading a layer's name/id failed: {exc}")
            continue
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"name": name, "id": layer_id} for score, name, layer_id in scored[:limit] if score >= floor]


def resolve_layer(name_or_id: str):







    layer, _note = resolve_layer_note(name_or_id)
    return layer


def resolve_layer_note(name_or_id: str) -> tuple:







    from qgis.core import QgsProject

    project = QgsProject.instance()
    text = str(name_or_id or "")
    if not text.strip():
        return None, ""
    layer = project.mapLayer(text)
    if layer:
        return layer, ""
    layers = [candidate for candidate in project.mapLayers().values() if candidate is not None]
    if not layers:
        return None, ""
    layer = _same_id(layers, text)
    if layer is not None:
        return layer, f"Read {text!r} as the id of {layer.name()!r} (id {layer.id()})."
    by_name: dict[str, list] = {}
    for candidate in layers:
        by_name.setdefault(candidate.name(), []).append(candidate)
    if text not in by_name:


        layer = _by_cut_id(layers, text)
        if layer is not None:
            return layer, (f"No layer has the id {text!r}; read it as the cut id of {layer.name()!r} "
                           f"(id {layer.id()}). The full id names it.")
    matched, _stage = match_names(text, list(by_name.keys()))
    if len(matched) != 1:




        layer = _by_id_head(layers, text)
        if layer is not None:
            return layer, (f"Nothing is named {text!r}; read it as the id of {layer.name()!r} "
                           f"(id {layer.id()}), which was renamed since.")
        return None, ""
    hits = by_name[matched[0]]
    chosen, why = _pick_one(project, hits)
    if chosen is None:
        return None, ""
    notes = []
    if chosen.name() != text:
        notes.append(f"No layer is named {text!r}; used {chosen.name()!r} (id {chosen.id()}).")
    if len(hits) > 1:
        notes.append(f"{len(hits)} layers are named {chosen.name()!r}; used {why} (id {chosen.id()}). "
                     "That id selects this one from the rest.")
    return chosen, " ".join(notes)


def _as_layer_id(text: str) -> str:








    return "".join(char if char == "_" or (char.isascii() and char.isalnum()) else "_"
                   for char in text.strip())


def _same_id(layers: list, text: str):

    lowered = _as_layer_id(text).lower()
    if not lowered:
        return None
    for layer in layers:
        try:
            if str(layer.id()).lower() == lowered:
                return layer
        except Exception as exc:  # noqa: BLE001
            log_debug(f"_same_id: reading a layer's id failed: {exc}")
            continue
    return None


def _by_id_head(layers: list, text: str):

    lowered = text.strip().lower()
    if not lowered or strip_id_tail(text) != text.strip():

        return None
    starts = [layer for layer in layers if str(layer.id()).lower().startswith(lowered + "_")]
    return starts[0] if len(starts) == 1 else None


def _by_cut_id(layers: list, text: str):







    lowered = _as_layer_id(text).lower()
    if not lowered:
        return None
    hits = []
    for layer in layers:
        layer_id = str(layer.id()).lower()
        if layer_id.startswith(lowered) and len(lowered) > len(strip_id_tail(layer_id)):
            hits.append(layer)
    return hits[0] if len(hits) == 1 else None


def _pick_one(project, hits: list) -> tuple:








    if not hits:
        return None, ""
    if len(hits) == 1:
        return hits[0], ""




    in_tree = [layer for layer in hits if _has_tree_node(project, layer)]
    pool = in_tree or hits
    if len(pool) == 1:
        return pool[0], "the one in the layer tree"



    if _one_source(pool):
        return _topmost(project, pool), "either, both read the same source"
    selected = [layer for layer in pool if _selected_count(layer) > 0]
    if len(selected) == 1:
        return selected[0], "the one with a selection"
    active = _active_layer()
    if active is not None:
        for layer in pool:
            if layer.id() == active.id():
                return layer, "the active one"
    visible = [layer for layer in pool if _is_drawn(project, layer)]
    if len(visible) == 1:
        return visible[0], "the visible one"
    pool = visible or pool
    fresh = _newest_added(project, pool)
    if fresh is not None:
        return fresh, "the newest, the one this conversation added"
    return _topmost(project, pool), "the top one in the layer tree"


def _selected_count(layer) -> int:
    try:
        return int(layer.selectedFeatureCount())
    except Exception:  # noqa: BLE001
        return 0


def _active_layer():
    try:
        from qgis.utils import iface

        return iface.activeLayer() if iface is not None else None
    except Exception:  # noqa: BLE001
        return None


def _is_drawn(project, layer) -> bool:

    try:
        node = project.layerTreeRoot().findLayer(layer.id())
        return node is not None and bool(node.isVisible())
    except Exception:  # noqa: BLE001
        return False


def _newest_added(project, pool: list):

    try:
        from ..core.layer_order import recent_layer_ids

        recent = recent_layer_ids(project)
    except Exception:  # noqa: BLE001
        return None
    by_id = {layer.id(): layer for layer in pool}
    for layer_id in recent:
        if layer_id in by_id:
            return by_id[layer_id]
    return None


def _topmost(project, pool: list):

    try:
        from ..core.layer_order import positions

        where = positions(project.layerTreeRoot())
    except Exception:  # noqa: BLE001
        where = {}
    return min(pool, key=lambda layer: (where.get(layer.id(), {}).get("index", 10 ** 6), layer.id()))


def _one_source(layers) -> bool:








    sources = set()
    for layer in layers:
        try:
            sources.add(_source_key(layer.source()))
        except Exception:  # noqa: BLE001
            return False
    return len(sources) == 1 and "" not in sources


def _source_key(source: str) -> str:







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
    except Exception:  # noqa: BLE001
        return False


def layer_not_found(name_or_id: str) -> dict:






    from qgis.core import QgsProject

    project = QgsProject.instance()
    layers = [layer for layer in project.mapLayers().values() if layer is not None]
    names = sorted({layer.name() for layer in layers})
    text = str(name_or_id or "")
    if len(text) > 1024:
        return {"_error": f"The layer reference has {len(text):,} characters; use the layer's name or id, "
                         "not its provider URI or geometry.",
                "code": "LAYER_NOT_FOUND",
                "suggestion": "list_layers gives every layer's id.",
                "_candidates": [{"id": layer.id(), "name": layer.name()} for layer in layers[:6]]}
    if not text.strip():
        return {"_error": "No layer name was given.", "code": "INVALID_ARGS",
                "suggestion": "layer_name takes the layer's name or its id; list_layers shows both."}
    if not layers:
        return {"_error": f"Layer not found: {text!r}. The project holds no layer.",
                "code": "LAYER_NOT_FOUND",
                "suggestion": "The project holds no layer; add_data loads one."}
    matched, _stage = match_names(text, names)
    if len(matched) > 1:


        candidates = [{"id": layer.id(), "name": layer.name()}
                      for layer in layers if layer.name() in matched][:6]
        listing = ", ".join(f"{c['name']!r} (id {c['id']})" for c in candidates)
        return {
            "_error": f"Layer {text!r} matches several layers: {listing}.",
            "code": "LAYER_NOT_FOUND",
            "suggestion": f"One of those ids, for example {candidates[0]['id']!r}, names a specific layer.",
            "_suggestions": [c["name"] for c in candidates],
            "_candidates": candidates,
        }
    close = closest_layers(text, layers)
    msg = f"Layer not found: {text!r}."
    if close:
        listing = ", ".join(f"{c['name']!r} (id {c['id']})" for c in close)
        msg += f" Did you mean: {listing}?"
        suggestion = f"The id {close[0]['id']!r} names {close[0]['name']!r} specifically."
    else:


        shown = closest_layers(text, layers, limit=6, floor=0)
        msg += " Available: " + ", ".join(f"{c['name']!r} (id {c['id']})" for c in shown)
        msg += f" (+{len(layers) - len(shown)} more)." if len(layers) > len(shown) else "."
        suggestion = "One of the ids here, or list_layers, names a specific layer."
    added = _added_here(project)
    if added and not close:


        msg += (" This conversation added: "
                + ", ".join(f"{c['name']!r} (id {c['id']})" for c in added) + ".")
    return {"_error": msg, "code": "LAYER_NOT_FOUND", "suggestion": suggestion,
            "_suggestions": [c["name"] for c in close], "_candidates": close}


def _added_here(project, limit: int = 4) -> list[dict]:

    try:
        from ..core.layer_order import recent_layer_ids

        recent = recent_layer_ids(project)[:limit]
    except Exception:  # noqa: BLE001
        return []
    out = []
    for layer_id in recent:
        layer = project.mapLayer(layer_id)
        if layer is not None:
            out.append({"id": layer_id, "name": layer.name()})
    return out






PINNED_LAYER_KEYS = ("layer_name", "layer", "layer_id", "target_layer")

LAYER_LIST_KEYS = ("layers", "layer_names")

OUTPUT_LAYER_KEYS = {"execute_sql": ("layer_name",)}


def pin_layer_names(args: dict, tool: str = "", pins: dict | None = None) -> dict | None:

























    if not isinstance(args, dict):
        return None
    from qgis.core import QgsProject

    project = QgsProject.instance()
    skipped = OUTPUT_LAYER_KEYS.get(tool, ())
    pins = pins if pins is not None else {}
    for key in PINNED_LAYER_KEYS:
        if key in skipped:
            continue
        value = args.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        if project.mapLayer(value) is not None:
            pins[key] = value
            continue
        layers = [layer for layer in project.mapLayers().values() if layer is not None]
        same = _same_id(layers, value)
        if same is not None:


            args[key], pins[key] = same.id(), value
            continue
        matched, stage = match_names(value, sorted({layer.name() for layer in layers}))
        if stage == "none":
            continue
        if stage == "exact":





            twins = [layer for layer in layers if layer.name() == value]
            chosen = twins[0] if len(twins) == 1 else _pick_one(project, twins)[0]
            if chosen is not None:
                args[key], pins[key] = chosen.id(), value
            continue
        if stage in ("case", "normalized", "tokens"):

            layer = resolve_layer(value)
            if layer is not None:
                args[key], pins[key] = layer.id(), value
                continue



        candidates = [{"id": layer.id(), "name": layer.name()} for layer in layers if layer.name() in matched]
        seen = {entry["id"] for entry in candidates}
        for extra in closest_layers(value, layers, limit=4):
            if extra["id"] not in seen:
                candidates.append(extra)
                seen.add(extra["id"])
        return _loose_name_refusal(value, candidates)
    return None


def pinned_layer_gone(args: dict, pins: dict) -> dict | None:








    if not isinstance(args, dict) or not pins:
        return None
    from qgis.core import QgsProject

    project = QgsProject.instance()
    for key, asked in pins.items():
        value = args.get(key)
        if not isinstance(value, str) or project.mapLayer(value) is not None:
            continue
        layers = [layer for layer in project.mapLayers().values() if layer is not None]
        near = closest_layers(str(asked), layers, limit=4)
        holds = near or [{"id": layer.id(), "name": layer.name()} for layer in layers[:4]]
        listing = ", ".join(f"{c['name']!r} (id {c['id']})" for c in holds)
        shown = str(asked) if len(str(asked)) <= 200 else f"[{len(str(asked)):,} character reference]"
        message = (f"Layer {shown!r} (id {value}) is no longer in the project: it was removed while this call "
                   "waited, so nothing was changed.")
        if listing:
            message += f" Closest layers the project holds now: {listing}."
        return {
            "_error": message,
            "code": "LAYER_NOT_FOUND",
            "suggestion": ("The layer this call named is gone; a candidate above needs the user's own "
                           "choice before it changes anything."),
            "_candidates": holds,
        }
    return None


def _loose_name_refusal(value: str, candidates: list[dict]) -> dict:

    if len(value) > 1024:
        value = f"[{len(value):,} character reference]"
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
        "suggestion": ("The id of the layer you mean names the target; changing an unnamed layer needs "
                       "the user's own choice through ask_user first."),
        "_ambiguous": len(candidates) > 1,
        "_candidates": shown,
    }


def unique_layer_name(name: str, keep_id: str = "") -> str:








    from qgis.core import QgsProject

    wanted = (name or "").strip()
    if not wanted:
        return wanted
    try:
        taken = {layer.name() for layer in QgsProject.instance().mapLayers().values()
                 if layer is not None and layer.id() != keep_id}
    except Exception:  # noqa: BLE001
        return wanted
    if wanted not in taken:
        return wanted
    for number in range(2, 100):
        candidate = f"{wanted} ({number})"
        if candidate not in taken:
            return candidate
    return wanted


def is_scratch_layer(layer) -> bool:

    import tempfile

    from ..core.policy import AGENT_TMP_DIR

    try:
        if layer.providerType() == "memory":
            return True
        source = os.path.realpath(layer.source().split("|", 1)[0])
    except Exception:  # noqa: BLE001
        return False
    roots = {os.path.realpath(AGENT_TMP_DIR), os.path.realpath(tempfile.gettempdir())}
    return any(source.startswith(root + os.sep) for root in roots if root and root != os.sep)


def take_layer_name(name: str, keep_id: str) -> tuple[str, list[dict]]:











    from qgis.core import QgsProject

    wanted = (name or "").strip()
    if not wanted:
        return wanted, []
    try:
        holders = [layer for layer in QgsProject.instance().mapLayers().values()
                   if layer is not None and layer.id() != keep_id and layer.name() == wanted]
    except Exception:  # noqa: BLE001
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

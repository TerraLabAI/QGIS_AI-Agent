# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later
"""Layer resolution by name or id, forgiving about spelling."""


























from __future__ import annotations

import difflib
import functools
import os
import re

from ..core.logger import log_debug

_STOPWORDS = {"the", "a", "an", "of", "layer", "in", "for", "data", "dataset"}
MATCH_THRESHOLD = 0.6

MATCH_MARGIN = 0.15

INSIDE_COVER = 0.6





_ID_TAIL = re.compile(r"_(?:[0-9a-f]{8}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{4}_[0-9a-f]{12}|\d{17,20})$",
                      re.IGNORECASE)



_NAME_SUFFIX = re.compile(
    r"(?:\s+\d+\s*/\s*\d+"
    r"|\s*[\[(]\s*\d+\s*[\])]"
    r"|\s*\(\s*\d[^()]{0,24}\)"
    r"|\s*[-_]?\s*\(\s*(?:previous|copy|copie|kopie|duplicate|styled|categorized|categorised|"
    r"graduated|classified|filtered|clipped|buffered)\s*\))+$",
    re.IGNORECASE)


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


@functools.lru_cache(maxsize=1024)
def normalize_name(name: str) -> str:
    """Case folded, marks removed, separators collapsed to spaces, filler words dropped."""









    from ..core.layer_rank import normalized

    return " ".join(token for token in normalized(name).split() if token not in _STOPWORDS)


@functools.lru_cache(maxsize=1024)
def strip_id_tail(text: str) -> str:
    """A QGIS layer id without its uuid tail, so the head can be matched as a name."""
    return _ID_TAIL.sub("", str(text or "")).strip()


def strip_suffix(text: str) -> str:
    """A name without a trailing count, duplicate number or style word."""
    return _NAME_SUFFIX.sub("", str(text or "")).strip()


@functools.lru_cache(maxsize=1024)
def bare_name(text: str) -> str:
    """A name reduced to what a later lookup should still match: no id tail, no suffix."""
    return strip_suffix(strip_id_tail(text)) or strip_id_tail(text)


def _squashed(norm: str) -> str:
    """A normalised name with its spaces gone, so "NDVI 2020" and "NDVI_2020" are one word."""
    return norm.replace(" ", "")


def _ratio(query: str, name: str) -> float:
    return difflib.SequenceMatcher(None, query, name).ratio()


def _best_alone(query: str, pool: list[str], table: dict[str, str]) -> str | None:
    """The one candidate clearly closer to the query than every other, or None."""





    scored = sorted(((_ratio(query, table.get(name, "")), name) for name in pool), reverse=True)
    if len(scored) == 1:
        return scored[0][1]
    if scored and scored[0][0] - scored[1][0] >= MATCH_MARGIN:
        return scored[0][1]
    return None


def match_names(query: str, names: list[str], threshold: float = MATCH_THRESHOLD) -> tuple[list[str], str]:
    """Names matching ``query`` and the stage that produced them."""







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
    """How close a layer name is to what was asked for, 0 to 1, over several readings."""
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
    """The best few candidates for a "did you mean" hint, most similar first."""
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


def closest_layers(query: str, layers: list, limit: int = 3) -> list[dict]:
    """The best few layers for a "did you mean" hint, each with the id to pass next."""




    scored = []
    for layer in layers:
        try:
            scored.append((_closeness(query, layer.name()), layer.name(), layer.id()))
        except Exception as exc:  # noqa: BLE001 - a layer that cannot name itself is no suggestion
            log_debug(f"closest_layers: reading a layer's name/id failed: {exc}")
            continue
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [{"name": name, "id": layer_id} for score, name, layer_id in scored[:limit] if score >= 0.4]


def resolve_layer(name_or_id: str):
    """The project layer for ``name_or_id`` (id first, then the matcher), or None."""






    layer, _note = resolve_layer_note(name_or_id)
    return layer


def resolve_layer_note(name_or_id: str) -> tuple:
    """``(layer, note)``: the layer for ``name_or_id`` and a sentence when it was not literal."""






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
                     "Pass that id to act on a different one.")
    return chosen, " ".join(notes)


def _same_id(layers: list, text: str):
    """The layer whose id is ``text`` written in another case."""
    lowered = text.strip().lower()
    if not lowered:
        return None
    for layer in layers:
        try:
            if str(layer.id()).lower() == lowered:
                return layer
        except Exception as exc:  # noqa: BLE001 - a layer that cannot say its id is not this one
            log_debug(f"_same_id: reading a layer's id failed: {exc}")
            continue
    return None


def _by_id_head(layers: list, text: str):
    """The only layer whose id begins with ``text``, once no name matched it."""
    lowered = text.strip().lower()
    if not lowered or strip_id_tail(text) != text.strip():

        return None
    starts = [layer for layer in layers if str(layer.id()).lower().startswith(lowered + "_")]
    return starts[0] if len(starts) == 1 else None


def _pick_one(project, hits: list) -> tuple:
    """One layer out of the ones sharing a name, and the words for why it was taken."""







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
    except Exception:  # noqa: BLE001 - a raster has no selection
        return 0


def _active_layer():
    try:
        from qgis.utils import iface

        return iface.activeLayer() if iface is not None else None
    except Exception:  # noqa: BLE001 - no GUI, no active layer
        return None


def _is_drawn(project, layer) -> bool:
    """Whether the layer's tree node is ticked, and every group above it too."""
    try:
        node = project.layerTreeRoot().findLayer(layer.id())
        return node is not None and bool(node.isVisible())
    except Exception:  # noqa: BLE001 - a project without a tree draws nothing we can read
        return False


def _newest_added(project, pool: list):
    """The layer of ``pool`` this conversation's calls added last, or None."""
    try:
        from ..core.layer_order import recent_layer_ids

        recent = recent_layer_ids(project)
    except Exception:  # noqa: BLE001 - nothing remembered is not an error
        return None
    by_id = {layer.id(): layer for layer in pool}
    for layer_id in recent:
        if layer_id in by_id:
            return by_id[layer_id]
    return None


def _topmost(project, pool: list):
    """The layer of ``pool`` drawn over the others, by the layer tree's own order."""
    try:
        from ..core.layer_order import positions

        where = positions(project.layerTreeRoot())
    except Exception:  # noqa: BLE001 - no tree to read, so any order will do
        where = {}
    return min(pool, key=lambda layer: (where.get(layer.id(), {}).get("index", 10 ** 6), layer.id()))


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
    """An error result with a code, the closest layers with their ids, and the next call to make."""





    from qgis.core import QgsProject

    project = QgsProject.instance()
    layers = [layer for layer in project.mapLayers().values() if layer is not None]
    names = sorted({layer.name() for layer in layers})
    text = str(name_or_id or "")
    if len(text) > 1024:
        return {"_error": f"The layer reference has {len(text):,} characters; use the layer's name or id, "
                         "not its provider URI or geometry.",
                "code": "LAYER_NOT_FOUND",
                "suggestion": "Call list_layers and pass the id of the intended layer.",
                "_candidates": [{"id": layer.id(), "name": layer.name()} for layer in layers[:6]]}
    if not text.strip():
        return {"_error": "No layer name was given.", "code": "INVALID_ARGS",
                "suggestion": "Pass layer_name, the layer's name or its id. list_layers shows both."}
    if not layers:
        return {"_error": f"Layer not found: {text!r}. The project holds no layer.",
                "code": "LAYER_NOT_FOUND",
                "suggestion": "Load a layer first (add_data), then call the tool again."}
    matched, _stage = match_names(text, names)
    if len(matched) > 1:


        candidates = [{"id": layer.id(), "name": layer.name()}
                      for layer in layers if layer.name() in matched][:6]
        listing = ", ".join(f"{c['name']!r} (id {c['id']})" for c in candidates)
        return {
            "_error": f"Layer {text!r} matches several layers: {listing}.",
            "code": "LAYER_NOT_FOUND",
            "suggestion": f"Call the tool again with one of those ids, for example {candidates[0]['id']!r}.",
            "_suggestions": [c["name"] for c in candidates],
            "_candidates": candidates,
        }
    close = closest_layers(text, layers)
    msg = f"Layer not found: {text!r}."
    if close:
        listing = ", ".join(f"{c['name']!r} (id {c['id']})" for c in close)
        msg += f" Did you mean: {listing}?"
        suggestion = f"Call the tool again with the id {close[0]['id']!r}, which is {close[0]['name']!r}."
    else:
        shown = [{"id": layer.id(), "name": layer.name()} for layer in layers[:6]]
        msg += " Available: " + ", ".join(f"{c['name']!r} (id {c['id']})" for c in shown)
        msg += f" (+{len(layers) - len(shown)} more)." if len(layers) > len(shown) else "."
        suggestion = "Pass one of the ids in this message, or call list_layers for the rest."
    added = _added_here(project)
    if added and not close:


        msg += (" This conversation added: "
                + ", ".join(f"{c['name']!r} (id {c['id']})" for c in added) + ".")
    return {"_error": msg, "code": "LAYER_NOT_FOUND", "suggestion": suggestion,
            "_suggestions": [c["name"] for c in close], "_candidates": close}


def _added_here(project, limit: int = 4) -> list[dict]:
    """The layers this conversation's calls added that the project still holds, newest first."""
    try:
        from ..core.layer_order import recent_layer_ids

        recent = recent_layer_ids(project)[:limit]
    except Exception:  # noqa: BLE001 - nothing remembered is not an error
        return []
    out = []
    for layer_id in recent:
        layer = project.mapLayer(layer_id)
        if layer is not None:
            out.append({"id": layer_id, "name": layer.name()})
    return out






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
        if stage == "none":
            continue
        if stage == "exact":





            twins = [layer for layer in layers if layer.name() == value]
            if len(twins) > 1:
                chosen, _why = _pick_one(project, twins)
                if chosen is not None:
                    args[key] = chosen.id()
            continue
        if stage in ("case", "normalized", "tokens"):

            layer = resolve_layer(value)
            if layer is not None:
                args[key] = layer.id()
                continue



        candidates = [{"id": layer.id(), "name": layer.name()} for layer in layers if layer.name() in matched]
        seen = {entry["id"] for entry in candidates}
        for extra in closest_layers(value, layers, limit=4):
            if extra["id"] not in seen:
                candidates.append(extra)
                seen.add(extra["id"])
        return _loose_name_refusal(value, candidates)
    return None


def _loose_name_refusal(value: str, candidates: list[dict]) -> dict:
    """LAYER_NOT_FOUND for a name a data-changing call may not guess from."""
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
        "suggestion": ("Pass the id of the layer you mean. When the user's request does not say which one, "
                       "ask them with ask_user before changing anything."),
        "_ambiguous": len(candidates) > 1,
        "_candidates": shown,
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

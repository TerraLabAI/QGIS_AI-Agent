# SPDX-FileCopyrightText: 2026 TerraLab <yvann.barbot@terra-lab.ai>
# SPDX-License-Identifier: GPL-2.0-or-later



















from __future__ import annotations

import math
import random
from bisect import bisect_left, bisect_right


CHECK_EVERY = 2_000


class TooManyLinks(ValueError):


    def __init__(self, links: int, limit: int):
        super().__init__(f"{links} links over {limit}")
        self.links, self.limit = links, limit


def _check(index: int, cancelled) -> None:
    if index % CHECK_EVERY == 0 and cancelled is not None and cancelled():
        raise InterruptedError("Stopped while the statistics were computed")




def contiguity(features: list, rook: bool, tolerance: float, cancelled=None) -> list:







    owners: dict = {}
    for index, rings in enumerate(features):
        _check(index, cancelled)
        keys = set()
        for ring in rings:
            previous = None
            for x, y in ring:
                key = (round(x / tolerance), round(y / tolerance))
                if rook:
                    if previous is not None and previous != key:
                        keys.add((previous, key) if previous < key else (key, previous))
                else:
                    keys.add(key)
                previous = key
        for key in keys:
            owners.setdefault(key, []).append(index)
    found = [set() for _ in features]
    for position, members in enumerate(owners.values()):
        _check(position, cancelled)
        if len(members) > 1:
            for a in members:
                found[a].update(members)
    for index, neighbours in enumerate(found):
        neighbours.discard(index)
    return [sorted(neighbours) for neighbours in found]


def _grid(points: list, cell: float) -> dict:
    grid: dict = {}
    for index, (x, y) in enumerate(points):
        grid.setdefault((math.floor(x / cell), math.floor(y / cell)), []).append(index)
    return grid


def _cell_size(points: list, per_cell: float) -> float:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    width, height = max(xs) - min(xs), max(ys) - min(ys)
    area = width * height
    if area <= 0:
        area = max(width, height, 1e-9) ** 2
    return max(math.sqrt(area * per_cell / max(len(points), 1)), 1e-12)


def k_nearest(points: list, k: int, cancelled=None) -> tuple:





    n = len(points)
    k = min(k, n - 1)
    if k <= 0:
        return [[] for _ in points], [[] for _ in points]
    cell = _cell_size(points, max(k, 1))
    grid = _grid(points, cell)
    neighbours, distances = [], []
    for index, (x, y) in enumerate(points):
        _check(index, cancelled)
        cx, cy = math.floor(x / cell), math.floor(y / cell)
        found: list = []
        ring = 0
        while True:
            for gx in range(cx - ring, cx + ring + 1):
                for gy in range(cy - ring, cy + ring + 1):
                    if max(abs(gx - cx), abs(gy - cy)) != ring:
                        continue
                    for other in grid.get((gx, gy), ()):
                        if other != index:
                            ox, oy = points[other]
                            found.append((math.hypot(ox - x, oy - y), other))
            if len(found) >= k:
                found.sort()
                if found[k - 1][0] <= ring * cell:
                    break
            ring += 1
            if len(found) >= n - 1:
                found.sort()
                break
        kept = found[:k]
        neighbours.append(sorted(other for _d, other in kept))
        distances.append([d for d, _other in kept])
    return neighbours, distances


def band_for_one_neighbour(points: list, cancelled=None) -> float:


    _neighbours, distances = k_nearest(points, 1, cancelled)
    return max((d[0] for d in distances if d), default=0.0)


def distance_band(points: list, distance: float, max_links: int, cancelled=None) -> list:

    if distance <= 0:
        return [[] for _ in points]
    grid = _grid(points, distance)
    neighbours = []
    links = 0


    limit = distance * (1.0 + 1e-12)
    for index, (x, y) in enumerate(points):
        _check(index, cancelled)
        cx, cy = math.floor(x / distance), math.floor(y / distance)
        near = []
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for other in grid.get((gx, gy), ()):
                    if other != index:
                        ox, oy = points[other]
                        if math.hypot(ox - x, oy - y) <= limit:
                            near.append(other)
        near.sort()
        links += len(near)
        if links > max_links:

            raise TooManyLinks(int(links * len(points) / (index + 1)), max_links)
        neighbours.append(near)
    return neighbours




def gi_star(values: list, neighbours: list) -> list:






    n = len(values)
    origin = values[0]
    shifted = [value - origin for value in values]
    mean = math.fsum(shifted) / n
    centered = [value - mean for value in shifted]



    spread = math.sqrt(math.fsum(value * value for value in centered) / n)
    out: list = []
    for index, near in enumerate(neighbours):
        if not near or spread == 0:
            out.append(None)
            continue
        weight = len(near) + 1
        denominator = spread * math.sqrt(max(n * weight - weight * weight, 0) / (n - 1))
        if denominator == 0:
            out.append(None)
            continue
        total = math.fsum([centered[index]] + [centered[j] for j in near])
        out.append(total / denominator)
    return out


def two_sided_p(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2.0))


LEVELS = ((3, 0.01), (2, 0.05), (1, 0.10))


def fdr_thresholds(p_values: list) -> dict:

    ordered = sorted(p_values)
    m = len(ordered)
    out = {}
    for level, alpha in LEVELS:
        threshold = 0.0
        for rank, p in enumerate(ordered, 1):
            if p <= alpha * rank / m:
                threshold = p
        out[level] = threshold
    return out


def gi_bin(z: float | None, p: float | None, thresholds: dict | None = None) -> int | None:

    if z is None or p is None:
        return None
    for level, alpha in LEVELS:
        limit = thresholds[level] if thresholds is not None else alpha
        significant = p <= limit if thresholds is not None else p < alpha
        if significant:
            return level if z > 0 else -level
    return 0




def standardise(values: list) -> list:
    n = len(values)
    mean = sum(values) / n
    spread = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
    if spread == 0:
        return []
    return [(v - mean) / spread for v in values]


def lags(z: list, neighbours: list) -> list:

    return [sum(z[j] for j in near) / len(near) if near else None for near in neighbours]


def global_moran(z: list, neighbours: list) -> dict:


    n = len(z)
    lag = lags(z, neighbours)
    s0 = float(sum(1 for near in neighbours if near))
    sum_z2 = sum(v * v for v in z)
    moran = n / s0 * sum(z[i] * lag[i] for i in range(n) if lag[i] is not None) / sum_z2
    expected = -1.0 / (n - 1)
    sets = [set(near) for near in neighbours]
    column = [0.0] * n
    s1 = 0.0
    for i, near in enumerate(neighbours):
        if not near:
            continue
        w_ij = 1.0 / len(near)
        for j in near:
            column[j] += w_ij
            w_ji = 1.0 / len(neighbours[j]) if i in sets[j] else 0.0
            both = (w_ij + w_ji) ** 2
            s1 += both if w_ji else 2.0 * both
    s1 *= 0.5
    s2 = sum(((1.0 if neighbours[i] else 0.0) + column[i]) ** 2 for i in range(n))
    var_norm = (n * n * s1 - n * s2 + 3 * s0 * s0) / ((n * n - 1) * s0 * s0) - expected ** 2
    b2 = n * sum(v ** 4 for v in z) / (sum_z2 ** 2)
    var_rand = ((n * ((n * n - 3 * n + 3) * s1 - n * s2 + 3 * s0 * s0)
                 - b2 * ((n * n - n) * s1 - 2 * n * s2 + 6 * s0 * s0))
                / ((n - 1) * (n - 2) * (n - 3) * s0 * s0) - expected ** 2)
    out = {"I": moran, "expected_I": expected, "s0": s0, "s1": s1, "s2": s2}
    for name, variance in (("randomisation", var_rand), ("normality", var_norm)):
        if variance > 0:
            score = (moran - expected) / math.sqrt(variance)
            out[name] = {"variance": variance, "z": score, "p": two_sided_p(score)}
        else:
            out[name] = {"variance": variance, "z": None, "p": None}
    return out


def local_moran(z: list, neighbours: list, permutations: int, seed: int, cancelled=None) -> tuple:



















    n = len(z)
    denominator = sum(v * v for v in z)
    lag = lags(z, neighbours)
    local = [None if lag[i] is None else (n - 1) * z[i] * lag[i] / denominator for i in range(n)]
    cards = sorted({len(near) for near in neighbours if near})
    p_values: list = [None] * n
    if not cards:
        return local, lag, p_values
    take = min(cards[-1] + 1, n)
    rng = random.Random(seed)  # nosec B311
    prefixes = []
    draws = []
    holders: list = [[] for _ in range(n)]
    for p in range(permutations):
        _check(p, cancelled)
        drawn = rng.sample(range(n), take)
        running, prefix = 0.0, [0.0]
        for position, index in enumerate(drawn):
            running += z[index]
            prefix.append(running)
            holders[index].append((p, position))
        prefixes.append(prefix)
        draws.append(drawn)
    simulated = {}
    for k in cards:
        simulated[k] = sorted(prefix[k] / k for prefix in prefixes)
    for i in range(n):
        _check(i, cancelled)
        k = len(neighbours[i])
        if not k:
            continue
        zi, observed = z[i], lag[i]
        if zi == 0:
            p_values[i] = 1.0
            continue
        ordered = simulated[k]


        greater = permutations - bisect_left(ordered, observed)
        lesser = bisect_right(ordered, observed)
        for p, position in holders[i]:
            if position < k and k < len(draws[p]):
                prefix = prefixes[p]
                base = prefix[k] / k
                fixed = (prefix[k] - zi + z[draws[p][k]]) / k
                greater += int(fixed >= observed) - int(base >= observed)
                lesser += int(fixed <= observed) - int(base <= observed)
        p_values[i] = min(2.0 * (min(greater, lesser) + 1) / (permutations + 1), 1.0)
    return local, lag, p_values


def quadrant(z: float, lag: float | None) -> str | None:
    if lag is None:
        return None
    if z > 0:
        return "HH" if lag > 0 else "HL"
    return "LH" if lag > 0 else "LL"




def ellipse(points: list, weights: list | None = None) -> dict | None:








    if weights is None:
        weights = [1.0] * len(points)
    total = sum(weights)
    if total <= 0 or len(points) < 3:
        return None
    mx = sum(w * x for (x, _y), w in zip(points, weights)) / total
    my = sum(w * y for (_x, y), w in zip(points, weights)) / total
    centered = [(x - mx, y - my) for x, y in points]
    sxx = math.fsum(w * x * x for (x, _y), w in zip(centered, weights)) / total
    syy = math.fsum(w * y * y for (_x, y), w in zip(centered, weights)) / total
    sxy = math.fsum(w * x * y for (x, y), w in zip(centered, weights)) / total
    angle = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    ca, sa = math.cos(angle), math.sin(angle)


    long_var = math.fsum(w * (x * ca + y * sa) ** 2 for (x, y), w in zip(centered, weights)) / total
    short_var = math.fsum(w * (-x * sa + y * ca) ** 2 for (x, y), w in zip(centered, weights)) / total
    bearing = (90.0 - math.degrees(angle)) % 180.0
    return {"mean_x": mx, "mean_y": my, "sigma_long": math.sqrt(long_var), "sigma_short": math.sqrt(short_var),
            "angle_east_rad": angle, "rotation_deg": bearing, "count": len(points), "weight_sum": total}


def ellipse_scale(std_devs: int | None, confidence: float | None) -> tuple:






    if confidence is not None:
        k = math.sqrt(-2.0 * math.log(1.0 - confidence))
    else:
        k = float(std_devs or 1) * math.sqrt(2.0)
    return k, 1.0 - math.exp(-k * k / 2.0)


def ellipse_ring(shape: dict, k: float, vertices: int = 90) -> list:

    a, b = k * shape["sigma_long"], k * shape["sigma_short"]
    ca, sa = math.cos(shape["angle_east_rad"]), math.sin(shape["angle_east_rad"])
    ring = []
    for step in range(vertices):
        t = 2.0 * math.pi * step / vertices
        u, v = a * math.cos(t), b * math.sin(t)
        ring.append((shape["mean_x"] + u * ca - v * sa, shape["mean_y"] + u * sa + v * ca))
    ring.append(ring[0])
    return ring


def share_inside(points: list, shape: dict, k: float) -> float:
    a, b = k * shape["sigma_long"], k * shape["sigma_short"]
    if a <= 0:
        return 0.0
    ca, sa = math.cos(shape["angle_east_rad"]), math.sin(shape["angle_east_rad"])
    inside = 0
    for x, y in points:
        dx, dy = x - shape["mean_x"], y - shape["mean_y"]
        u, v = dx * ca + dy * sa, -dx * sa + dy * ca
        if b <= 0:
            inside += int(abs(v) <= 1e-12 and abs(u) <= a)
        elif (u / a) ** 2 + (v / b) ** 2 <= 1.0:
            inside += 1
    return inside / len(points) if points else 0.0

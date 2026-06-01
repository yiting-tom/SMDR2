"""Post-match arbitration between geometrically indistinguishable classes.

The matcher in `app.matching` is class-agnostic: two classes that share a
circle template of the same diameter (e.g., `BGABall` and `FiducialCircle`)
will return the same handles in both classes' results. This module routes
each handle to exactly one class by counting its spatial neighbours within
an auto-derived pitch radius.

Contract: `arbitrate(out, shapes, groups)` takes the dict produced by
`save_match_json` (keys `<view>.<class_snake>.<idx>` or `<class_snake>.<idx>`)
and returns a rewritten dict plus a per-group counts payload. See the
`class-arbitration` spec for the algorithm and edge cases.

Pure module: no FastAPI / DB / IO dependencies, so it stays unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

import numpy as np
from scipy.spatial import cKDTree

from app.library import (
    CLASS_JSON_KEY,
    ArbitrationGroup,
    is_allowed_view,
)


_VIEW_PREFIXES: frozenset[str] = frozenset(
    {"top_view", "bottom_view", "side_view"}
)


def _parse_key(key: str) -> tuple[str | None, str, int] | None:
    """Split a Match-JSON key into (view_prefix, class_snake, idx).

    Accepts either `<view>.<class>.<idx>` or `<class>.<idx>`. The class
    portion can itself contain a dot (e.g., the literal `2d_barcode`
    snake_case key has no dot, but the parser stays robust by anchoring
    the idx as a trailing integer). Returns None if the key does not
    match either shape.
    """
    parts = key.split(".")
    if len(parts) < 2:
        return None
    # Last segment must be a non-negative integer.
    try:
        idx = int(parts[-1])
    except ValueError:
        return None
    head = parts[:-1]
    if len(head) >= 2 and head[0] in _VIEW_PREFIXES:
        return head[0], ".".join(head[1:]), idx
    return None, ".".join(head), idx


@dataclass
class _Instance:
    """One match instance, normalised so arbitration can act on it."""

    sort_key: tuple                # for deterministic ordering
    view_prefix: str | None        # "top_view" / "bottom_view" / "side_view" / None
    original_class: str            # display ID, e.g. "BGABall"
    original_idx: int              # the .<idx> from the source key
    handles: list[str]
    centroid: np.ndarray           # shape (2,)


def _instance_centroid(
    handles: Iterable[str],
    shapes: Mapping[str, object],
) -> np.ndarray | None:
    """Bbox centre over every entity's points, matching the side-region
    helper's notion of an instance's location. Returns None when the
    instance has no resolvable points (e.g., empty handle list)."""
    xmin = ymin = float("inf")
    xmax = ymax = float("-inf")
    found = False
    for h in handles:
        shape = shapes.get(h)
        if shape is None:
            continue
        pts = getattr(shape, "points", None)
        if pts is None or len(pts) == 0:
            continue
        xs = pts[:, 0]
        ys = pts[:, 1]
        xmin = min(xmin, float(xs.min()))
        xmax = max(xmax, float(xs.max()))
        ymin = min(ymin, float(ys.min()))
        ymax = max(ymax, float(ys.max()))
        found = True
    if not found:
        return None
    return np.array([(xmin + xmax) * 0.5, (ymin + ymax) * 0.5])


def pool_instances(
    out: Mapping[str, list[list[str]]],
    group: ArbitrationGroup,
    shapes: Mapping[str, object],
) -> list[_Instance]:
    """Collect every instance keyed under any of `group.members` from `out`,
    across all view prefixes. Each *physical* instance — identified by its
    sorted handle set — yields exactly one pool entry even when the matcher
    cross-fired across multiple member classes (the bug arbitration exists
    to resolve). Each entry gets a deterministic sort key so downstream
    computation is reproducible regardless of dict order."""
    instances: list[_Instance] = []
    seen: set[tuple[str, ...]] = set()
    member_snakes: dict[str, str] = {
        m: CLASS_JSON_KEY.get(m, m) for m in group.members
    }
    snake_to_display: dict[str, str] = {v: k for k, v in member_snakes.items()}
    # Iterate keys in sorted order so dedup is deterministic — the *first*
    # observed (view_prefix, class, idx) for a handle set defines that
    # instance's original_class / original_idx for accounting purposes.
    for key in sorted(out.keys()):
        parsed = _parse_key(key)
        if parsed is None:
            continue
        prefix, cls_snake, idx = parsed
        if cls_snake not in snake_to_display:
            continue
        cls_display = snake_to_display[cls_snake]
        for inner_pos, handles in enumerate(out[key]):
            dedup_key = tuple(sorted(handles))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            centroid = _instance_centroid(handles, shapes)
            if centroid is None:
                continue
            sort_key = (
                prefix or "",        # view_prefix; None sorts before "top_view"
                cls_display,
                idx,
                dedup_key,           # handles tuple — order-invariant
            )
            instances.append(
                _Instance(
                    sort_key=sort_key,
                    view_prefix=prefix,
                    original_class=cls_display,
                    original_idx=idx,
                    handles=list(handles),
                    centroid=centroid,
                )
            )
    instances.sort(key=lambda i: i.sort_key)
    return instances


def derive_pitch(centroids: np.ndarray) -> float | None:
    """Median of per-centroid nearest-neighbour distances. Returns None
    when there are < 2 centroids."""
    if centroids.shape[0] < 2:
        return None
    tree = cKDTree(centroids)
    # k=2 returns each point's nearest neighbour after itself (col 0 is self,
    # distance 0; col 1 is the actual NN).
    dists, _ = tree.query(centroids, k=2)
    nn = dists[:, 1]
    return float(np.median(nn))


def count_neighbors(centroids: np.ndarray, radius: float) -> np.ndarray:
    """For each centroid, count how many *other* centroids lie within
    `radius`. Self is always excluded."""
    if centroids.shape[0] == 0:
        return np.zeros(0, dtype=np.int64)
    tree = cKDTree(centroids)
    # query_ball_point with return_length avoids materialising index lists.
    counts = tree.query_ball_point(centroids, r=radius, return_length=True)
    return np.asarray(counts, dtype=np.int64) - 1  # subtract self


def classify(
    counts: np.ndarray,
    group: ArbitrationGroup,
) -> list[str]:
    """Assign each instance a target class. Apply member rules in the
    order they appear in `group.rules`; first match wins. Falls back to
    `group.default_class` when no rule matches."""
    targets: list[str] = []
    ordered_rules = list(group.rules.items())
    for c in counts:
        chosen: str | None = None
        n = int(c)
        for cls, rule in ordered_rules:
            if rule.matches(n):
                chosen = cls
                break
        targets.append(chosen if chosen is not None else group.default_class)
    return targets


def apply_population_fallback(
    targets: list[str],
    group: ArbitrationGroup,
) -> list[str]:
    """If any *non-default* member class has fewer than `group.min_population`
    instances, collapse the entire pool to `group.default_class`.

    The default_class is the "safe direction" — only the harder-to-believe
    class (e.g., BGABall, which requires a real grid) gets the floor. The
    default class itself (e.g., FiducialCircle) is allowed to be small,
    since 4 fiducials per substrate is realistic and shouldn't trigger
    the fallback."""
    if not targets:
        return targets
    counts: dict[str, int] = {m: 0 for m in group.members}
    for t in targets:
        if t in counts:
            counts[t] += 1
    non_default = (m for m in group.members if m != group.default_class)
    if any(counts[m] < group.min_population for m in non_default):
        return [group.default_class] * len(targets)
    return targets


def _make_key(prefix: str | None, cls_display: str, idx: int) -> str:
    cls_snake = CLASS_JSON_KEY.get(cls_display, cls_display)
    base = f"{cls_snake}.{idx}"
    return f"{prefix}.{base}" if prefix else base


@dataclass
class GroupCounts:
    pool_size: int = 0
    derived_pitch: float | None = None
    assigned: dict[str, int] = None  # type: ignore[assignment]
    reassigned_from_match: int = 0
    population_fallback_triggered: bool = False
    dropped_by_view: int = 0

    def __post_init__(self) -> None:
        if self.assigned is None:
            self.assigned = {}

    def to_dict(self) -> dict[str, object]:
        return {
            "pool_size": self.pool_size,
            "derived_pitch": self.derived_pitch,
            "assigned": dict(self.assigned),
            "reassigned_from_match": self.reassigned_from_match,
            "population_fallback_triggered": self.population_fallback_triggered,
            "dropped_by_view": self.dropped_by_view,
        }


def _group_label(group: ArbitrationGroup) -> str:
    return "|".join(sorted(group.members))


def arbitrate(
    out: dict[str, list[list[str]]],
    shapes: Mapping[str, object],
    groups: Iterable[ArbitrationGroup],
    *,
    enforce_view_constraints: bool = True,
) -> tuple[
    dict[str, list[list[str]]],
    dict[str, dict[str, object]],
    dict[str, dict[str | None, int]],
]:
    """Rewrite `out` so each instance appears under at most one class
    per arbitration group, using neighbour-count + auto-pitch + population
    fallback. Returns:

    - `new_out`: the rewritten Match-JSON dict.
    - `group_counts`: per-group diagnostics keyed by sorted-members label.
    - `view_drops`: per-group, per-view-prefix count of instances dropped
      by the view-constraint re-validation; the caller folds this into
      its `side_counts["dropped"]` bucket.

    `enforce_view_constraints` (default True) — at save_match / scan-all
    time, an arbitrated instance whose new class is view-constrained
    and whose `view_prefix` doesn't satisfy the constraint is dropped
    ("strict mode"). At preprocess time, view rects aren't drawn yet
    so every instance has `view_prefix=None`; the prematch caller
    passes False to keep the cross-fire resolution without throwing
    out every constrained-class match. `view_drops` stays empty when
    False.

    Production callers should use the stage-specific entry points
    `arbitrate_for_prematch` / `arbitrate_for_match` rather than passing
    `enforce_view_constraints` directly; this low-level form stays for
    unit tests and explicit-mode control. See the `class-arbitration` spec.
    """
    new_out: dict[str, list[list[str]]] = dict(out)  # shallow copy
    group_counts: dict[str, dict[str, object]] = {}
    view_drops: dict[str, dict[str | None, int]] = {}

    for group in groups:
        instances = pool_instances(new_out, group, shapes)
        label = _group_label(group)
        gc = GroupCounts(pool_size=len(instances))

        # Single-class short-circuit: only one group member contributed
        # keys to `out` (e.g. library has templates for one class only).
        # `pool_instances` dedupes by handle set and would surface only
        # the lex-first source key's class, which would make a true
        # cross-fire (matcher fires N templates on the same handles)
        # indistinguishable from a single-template library. So check
        # the RAW key set here, not the deduped instances list.
        member_snakes = {m: CLASS_JSON_KEY.get(m, m) for m in group.members}
        snake_to_display = {v: k for k, v in member_snakes.items()}
        classes_with_keys: set[str] = set()
        for key in new_out:
            parsed = _parse_key(key)
            if parsed is None:
                continue
            _prefix, cls_snake, _idx = parsed
            display = snake_to_display.get(cls_snake)
            if display is not None:
                classes_with_keys.add(display)
        if len(classes_with_keys) == 1 and instances:
            sole_class = next(iter(classes_with_keys))
            # No cross-class competition possible in the source keys —
            # classify() could only mis-label individual instances by
            # neighbour density. Leave source keys in `new_out` untouched.
            gc.assigned = {sole_class: len(instances)}
            group_counts[label] = gc.to_dict()
            continue

        if len(instances) < 2:
            # Spec: empty / singleton pool → arbitration is a no-op.
            if instances:
                gc.assigned = {instances[0].original_class: 1}
            group_counts[label] = gc.to_dict()
            continue

        centroids = np.vstack([i.centroid for i in instances])
        pitch = derive_pitch(centroids)
        gc.derived_pitch = pitch
        if pitch is None or pitch <= 0:
            # Degenerate (all-coincident) → no-op.
            for inst in instances:
                gc.assigned[inst.original_class] = (
                    gc.assigned.get(inst.original_class, 0) + 1
                )
            group_counts[label] = gc.to_dict()
            continue

        radius = group.pitch_multiplier * pitch
        counts = count_neighbors(centroids, radius)
        raw_targets = classify(counts, group)

        # Population fallback: explicit count instead of a same-reference
        # check so a pool that was already all-default still records the
        # trigger when min_population is breached. The floor applies only
        # to non-default members (see apply_population_fallback docstring).
        per_class_pre = {m: 0 for m in group.members}
        for t in raw_targets:
            if t in per_class_pre:
                per_class_pre[t] += 1
        # Precondition: the safe direction (default_class) is only a valid
        # fallback target when its templates actually produced at least one
        # match in this pool. Otherwise the fallback would invent labels
        # under a class key backed by no library template.
        default_in_pool = any(
            inst.original_class == group.default_class for inst in instances
        )
        fallback_triggered = default_in_pool and any(
            per_class_pre[m] < group.min_population
            for m in group.members if m != group.default_class
        )
        gc.population_fallback_triggered = fallback_triggered
        targets = (
            [group.default_class] * len(raw_targets)
            if fallback_triggered else raw_targets
        )

        # Every pooled instance came from a key whose snake-class is a
        # member. Drop those keys wholesale; we'll re-emit under resolved
        # class keys in deterministic order below.
        member_snakes = {CLASS_JSON_KEY.get(m, m) for m in group.members}
        for key in list(new_out.keys()):
            parsed = _parse_key(key)
            if parsed is None:
                continue
            _prefix, cls_snake, _idx = parsed
            if cls_snake in member_snakes:
                del new_out[key]

        # Re-emit each instance under its resolved class, re-checking the
        # view constraint of the new class. The `.idx` in the output key
        # represents the *template index* of the resolved class (matching
        # the non-arbitration path in `save_match_json`, which uses
        # `f"{json_cls}.{idx}"` with `idx = template position in
        # lib.templates_of(cls)`). All instances belonging to the same
        # (view, class, template_idx) are collapsed into a single key
        # whose value is a list of handle arrays — one per match
        # instance. This matches how non-arbitration classes like
        # `c4_ball` emit (e.g. `top_view.c4_ball.0` carries every
        # matched C4Ball instance under one key when the class has only
        # one template).
        #
        # For instances whose class did NOT change, the original
        # template index is preserved. For reassigned instances, we
        # default to 0 — `original_idx` (a template index in the SOURCE
        # class) is meaningless under the new class, and 0 is a valid
        # template index for any class that has at least one template
        # (a precondition for the class to participate in matching at
        # all).
        bucket: dict[str, list[list[str]]] = {}
        for inst, new_cls in zip(instances, targets):
            if new_cls != inst.original_class:
                gc.reassigned_from_match += 1
            if enforce_view_constraints and not is_allowed_view(
                new_cls, inst.view_prefix,
            ):
                gc.dropped_by_view += 1
                view_drops.setdefault(label, {})
                view_drops[label][inst.view_prefix] = (
                    view_drops[label].get(inst.view_prefix, 0) + 1
                )
                continue
            idx = inst.original_idx if new_cls == inst.original_class else 0
            out_key = _make_key(inst.view_prefix, new_cls, idx)
            bucket.setdefault(out_key, []).append(list(inst.handles))
            gc.assigned[new_cls] = gc.assigned.get(new_cls, 0) + 1

        for k, hls in bucket.items():
            new_out.setdefault(k, []).extend(hls)

        # Ensure every member shows up in `assigned` even at zero so
        # downstream readers don't need a None-guard.
        for m in group.members:
            gc.assigned.setdefault(m, 0)

        group_counts[label] = gc.to_dict()

    return new_out, group_counts, view_drops


def arbitrate_for_prematch(
    out: dict[str, list[list[str]]],
    shapes: Mapping[str, object],
    groups: Iterable[ArbitrationGroup],
) -> tuple[
    dict[str, list[list[str]]],
    dict[str, dict[str, object]],
    dict[str, dict[str | None, int]],
]:
    """Arbitration entry point for the preprocess / prematch stage.

    Side-region rects aren't drawn yet, so every instance has
    `view_prefix=None`; enforcing view constraints here would drop every
    match of a view-constrained class (BGABall, C4Ball, ...). This entry
    point resolves matcher cross-fire WITHOUT view-constraint dropping, so
    the returned `view_drops` is always empty. Once the operator draws side
    regions and runs scan-all / save-match, `arbitrate_for_match`
    re-arbitrates with strict view enforcement. See the `class-arbitration`
    spec.
    """
    return arbitrate(out, shapes, groups, enforce_view_constraints=False)


def arbitrate_for_match(
    out: dict[str, list[list[str]]],
    shapes: Mapping[str, object],
    groups: Iterable[ArbitrationGroup],
) -> tuple[
    dict[str, list[list[str]]],
    dict[str, dict[str, object]],
    dict[str, dict[str | None, int]],
]:
    """Arbitration entry point for the save-match and scan-all stages.

    Side regions are drawn, so each reassigned instance's new class is
    re-validated against its view prefix; instances whose new class
    disallows that view are dropped into `dropped_by_view` / `view_drops`
    (strict mode). See the `class-arbitration` spec.
    """
    return arbitrate(out, shapes, groups, enforce_view_constraints=True)

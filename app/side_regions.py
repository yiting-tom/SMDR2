"""Per-file top_view / bottom_view / side_view side-region helpers.

A side region is an axis-aligned, world-space rectangle the engineer paints
on the viewer to tag one of the three views (top / bottom / side) on a
DXF sheet. The match-JSON serializer uses these to rewrite each instance's
key from ``smd_2t.0`` to ``top_view.smd_2t.0`` etc., based on the
instance's bbox-center containment. Any subset of the three rectangles
may be set; an unset rectangle never contains anything.

This module is intentionally pure: it has no DB or filesystem dependencies
so it stays trivially unit-testable.
"""

from __future__ import annotations

from typing import Iterable, Mapping, Optional, TypedDict

from app.library import is_allowed_view


_VIEW_PREFIXES: frozenset[str] = frozenset(
    {"top_view", "bottom_view", "side_view"}
)


def parse_match_key(key: str) -> tuple[str | None, str, int] | None:
    """Split a match-JSON key into ``(view_prefix, class_snake, idx)``.

    Inverse of the ``<prefix>.<base_key>`` keys ``split_matches_by_side``
    emits. Accepts either ``<view>.<class>.<idx>`` or ``<class>.<idx>``; the
    idx is anchored as the trailing integer so a class snake-key containing
    no dot stays robust. Returns ``None`` if the key matches neither shape.
    """
    parts = key.split(".")
    if len(parts) < 2:
        return None
    try:
        idx = int(parts[-1])
    except ValueError:
        return None
    head = parts[:-1]
    if len(head) >= 2 and head[0] in _VIEW_PREFIXES:
        return head[0], ".".join(head[1:]), idx
    return None, ".".join(head), idx


# Default-on master switch for contained-match suppression. This is a
# source-level module constant — it is NOT registered in the developer-override
# HTTP store, so changing it in a running deployment means editing source and
# restarting (or setting the attribute in-process, e.g. from tests).
# ``suppress_contained_matches`` reads it live on each call (a bare global
# reference resolves to the current module value), so an in-process ``setattr``
# takes effect on the very next call without re-importing.
CONTAINED_SUPPRESSION_ENABLED = True


def suppress_contained_matches(
    out: dict[str, list[list[str]]],
) -> dict[str, list[list[str]]]:
    """Drop match instances contained within a larger same-class instance.

    Within each class — pooled across every view prefix — an instance whose
    consumed DXF-handle set is a *proper subset* of another instance's set is
    removed; the superset (more handles) wins. This resolves the case where a
    class holds both a partial template (e.g. an SMD mask-only pattern: two
    rectangles) and a fuller one (the masks *plus* the centre body): a location
    that has the body fires both, recording one physical feature twice.

    Instances with identical handle sets collapse to one (kept: earliest
    template ``idx``, then key, then position). Suppression is evaluated
    non-iteratively over the full representative set, so a containment chain
    ``A ⊊ B ⊊ C`` keeps only ``C``.

    ``out`` is the prefixed Match-JSON dict
    ``{"<view>.<class>.<idx>": [[handle, ...], ...]}``. Returns a new dict of
    the same shape, preserving key order and within-key instance order minus
    removed instances; keys left empty are dropped. Different classes are never
    compared, so cross-class containment (e.g. a fiducial circle whose handle
    sits inside a multi-entity pattern) is left untouched. Unparseable keys and
    empty (no-handle) instances pass through unchanged.

    Reads the live :data:`CONTAINED_SUPPRESSION_ENABLED` flag on each call;
    when ``False`` it returns ``out`` unchanged.
    """
    if not CONTAINED_SUPPRESSION_ENABLED:
        return out

    # Pool instances by snake class, ignoring the view prefix. Each pooled
    # entry remembers its origin (key, position), its template idx (for the
    # equal-set tie-break) and its handle set. Pooling across prefixes is safe:
    # two *different* physical instances have disjoint handles and can never be
    # subsets, while a feature straddling a view-region boundary could have its
    # partial and fuller matches land under different prefixes.
    pools: dict[str, list[tuple[str, int, int, frozenset[str]]]] = {}
    for key, instances in out.items():
        parsed = parse_match_key(key)
        if parsed is None:
            continue  # unparseable keys pass through untouched
        _prefix, snake, idx = parsed
        for pos, handles in enumerate(instances):
            if not handles:
                continue  # defensive: an empty match is left in place
            pools.setdefault(snake, []).append(
                (key, pos, idx, frozenset(handles))
            )

    removed: set[tuple[str, int]] = set()
    for items in pools.values():
        # Collapse exact-duplicate handle sets to one representative; keep the
        # earliest (idx, key, pos) and mark the rest removed.
        by_set: dict[frozenset[str], list[tuple[str, int, int, frozenset[str]]]] = {}
        for it in items:
            by_set.setdefault(it[3], []).append(it)
        reps: list[tuple[str, int, int, frozenset[str]]] = []
        for group in by_set.values():
            group.sort(key=lambda t: (t[2], t[0], t[1]))
            reps.append(group[0])
            for dup in group[1:]:
                removed.add((dup[0], dup[1]))
        # Proper-subset suppression over the distinct representatives. A
        # superset of X must contain ALL of X's handles, so it appears in the
        # inverted index of every handle of X — in particular the rarest one.
        # Checking only that bucket (instead of every rep) prunes the naive
        # O(R²) scan to near-linear when matches are mostly disjoint: a BGA grid
        # of single-handle instances shares no handles, so each rep's
        # rarest-handle bucket is just itself. Sets are all distinct here, so
        # ``<`` is strict subset; evaluating against the full rep set (not
        # survivors) is order-independent and collapses transitive chains.
        handle_to_reps: dict[str, list[int]] = {}
        for ri, (_k, _p, _i, fset) in enumerate(reps):
            for h in fset:
                handle_to_reps.setdefault(h, []).append(ri)
        for i, (key_i, pos_i, _idx_i, set_i) in enumerate(reps):
            rarest = min(set_i, key=lambda h: len(handle_to_reps[h]))
            for j in handle_to_reps[rarest]:
                if j != i and set_i < reps[j][3]:
                    removed.add((key_i, pos_i))
                    break

    if not removed:
        return out

    result: dict[str, list[list[str]]] = {}
    for key, instances in out.items():
        kept = [
            handles
            for pos, handles in enumerate(instances)
            if (key, pos) not in removed
        ]
        if kept:
            result[key] = kept
    return result


class Rect(TypedDict):
    x0: float
    y0: float
    x1: float
    y1: float


def normalise_rect(rect: Mapping[str, float]) -> Rect:
    """Return a Rect with x0<=x1 and y0<=y1, regardless of input order."""
    x0 = float(rect["x0"])
    y0 = float(rect["y0"])
    x1 = float(rect["x1"])
    y1 = float(rect["y1"])
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def point_in_rect(p: tuple[float, float], rect: Optional[Mapping[str, float]]) -> bool:
    """Closed-interval containment. ``rect=None`` is never containing."""
    if rect is None:
        return False
    x, y = p
    return (
        rect["x0"] <= x <= rect["x1"]
        and rect["y0"] <= y <= rect["y1"]
    )


def _bbox_center(handles: Iterable[str], shapes: Mapping[str, object]) -> Optional[tuple[float, float]]:
    """Combined bbox center of every point in every handle's EntityShape.

    Returns None if no handle resolves to a shape with non-empty points.
    """
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
        # pts is an (N, 2) numpy array; use vectorised min/max.
        xs = pts[:, 0]
        ys = pts[:, 1]
        xmin = min(xmin, float(xs.min()))
        xmax = max(xmax, float(xs.max()))
        ymin = min(ymin, float(ys.min()))
        ymax = max(ymax, float(ys.max()))
        found = True
    if not found:
        return None
    return ((xmin + xmax) * 0.5, (ymin + ymax) * 0.5)


def split_matches_by_side(
    base_key: str,
    matches: Iterable,
    shapes: Mapping[str, object],
    top_view: Optional[Mapping[str, float]],
    bottom_view: Optional[Mapping[str, float]],
    side_view: Optional[Mapping[str, float]],
    class_name: str,
) -> tuple[dict[str, list[list[str]]], dict[str, int]]:
    """Group a single template's match instances by side prefix.

    Returns ``(out, counts)`` where:
    - ``out`` maps ``"<prefix>.<base_key>"`` (or ``base_key`` for unassigned)
      to a list of handle-lists, preserving instance order within each side.
    - ``counts`` is ``{"top_view": N, "bottom_view": M, "side_view": K,
      "unassigned": U, "dropped": D}``; the ``dropped`` bucket counts
      instances filtered out by the class-view constraint registry, the
      other four buckets count surviving instances only.

    ``class_name`` is the **display ID** of the class this template
    belongs to (e.g., ``"BGABall"``, ``"Substrate"``). Instances of
    constrained classes (those listed in
    :data:`library.CLASS_VIEW_CONSTRAINTS`) whose computed prefix is
    not in the allowed set — including the "unassigned" position —
    are dropped, not emitted under any key.

    Each match instance is expected to expose a ``.handles`` attribute (a
    ``MatchResult`` from :mod:`app.matching`).
    """
    out: dict[str, list[list[str]]] = {}
    counts = {"top_view": 0, "bottom_view": 0, "side_view": 0,
              "unassigned": 0, "dropped": 0}
    for m in matches:
        prefix = side_prefix_for(m.handles, shapes, top_view, bottom_view, side_view)
        if not is_allowed_view(class_name, prefix):
            counts["dropped"] += 1
            continue
        key = f"{prefix}.{base_key}" if prefix else base_key
        out.setdefault(key, []).append(list(m.handles))
        counts[prefix if prefix else "unassigned"] += 1
    return out, counts


def side_prefix_for(
    handles: Iterable[str],
    shapes: Mapping[str, object],
    top_view: Optional[Mapping[str, float]],
    bottom_view: Optional[Mapping[str, float]],
    side_view: Optional[Mapping[str, float]],
) -> Optional[str]:
    """Return ``"top_view"``, ``"bottom_view"``, ``"side_view"``, or ``None``.

    The instance is represented by its DXF entity handles; ``shapes`` is the
    handle→EntityShape map already cached by the matcher. The decision is
    based on the combined bbox center of every entity, with overlap
    priority ``top_view > bottom_view > side_view``:

    - in top_view (or in multiple, top_view wins) → ``"top_view"``
    - in bottom_view but not top_view → ``"bottom_view"``
    - in side_view but not top_view or bottom_view → ``"side_view"``
    - in none, or all three rectangles are None → ``None``
    """
    if top_view is None and bottom_view is None and side_view is None:
        return None
    center = _bbox_center(handles, shapes)
    if center is None:
        return None
    if point_in_rect(center, top_view):
        return "top_view"
    if point_in_rect(center, bottom_view):
        return "bottom_view"
    if point_in_rect(center, side_view):
        return "side_view"
    return None

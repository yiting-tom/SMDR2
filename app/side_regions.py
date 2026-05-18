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
) -> tuple[dict[str, list[list[str]]], dict[str, int]]:
    """Group a single template's match instances by side prefix.

    Returns ``(out, counts)`` where:
    - ``out`` maps ``"<prefix>.<base_key>"`` (or ``base_key`` for unassigned)
      to a list of handle-lists, preserving instance order within each side.
    - ``counts`` is ``{"top_view": N, "bottom_view": M, "side_view": K, "unassigned": U}``.

    Each match instance is expected to expose a ``.handles`` attribute (a
    ``MatchResult`` from :mod:`app.matching`).
    """
    out: dict[str, list[list[str]]] = {}
    counts = {"top_view": 0, "bottom_view": 0, "side_view": 0, "unassigned": 0}
    for m in matches:
        prefix = side_prefix_for(m.handles, shapes, top_view, bottom_view, side_view)
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

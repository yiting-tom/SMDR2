"""Unit tests for side-region containment + key prefixing."""

from __future__ import annotations

import numpy as np
import pytest

from app.matching import EntityShape, MatchResult
from app.side_regions import (
    normalise_rect,
    point_in_rect,
    side_prefix_for,
    split_matches_by_side,
)


def _shape(handle: str, pts: list[tuple[float, float]]) -> EntityShape:
    return EntityShape.from_points(handle, pts)


def test_normalise_rect_orders_coords():
    r = normalise_rect({"x0": 10, "y0": 5, "x1": 2, "y1": 9})
    assert r == {"x0": 2.0, "y0": 5.0, "x1": 10.0, "y1": 9.0}


def test_point_in_rect_closed_interval():
    r = {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert point_in_rect((5.0, 5.0), r)
    assert point_in_rect((0.0, 0.0), r)
    assert point_in_rect((10.0, 10.0), r)
    assert not point_in_rect((10.0001, 5.0), r)
    assert not point_in_rect((-0.1, 5.0), r)
    assert not point_in_rect((5.0, 5.0), None)


def test_side_prefix_center_in_frontside():
    shapes = {"A": _shape("A", [(0, 0), (2, 0), (2, 2), (0, 2)])}
    front = {"x0": -5, "y0": -5, "x1": 5, "y1": 5}
    back = {"x0": 100, "y0": 100, "x1": 110, "y1": 110}
    assert side_prefix_for(["A"], shapes, front, back) == "frontside"


def test_side_prefix_center_in_bottomside():
    shapes = {"A": _shape("A", [(50, 50), (52, 50), (52, 52), (50, 52)])}
    front = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    back = {"x0": 40, "y0": 40, "x1": 60, "y1": 60}
    assert side_prefix_for(["A"], shapes, front, back) == "bottomside"


def test_side_prefix_overlap_tiebreaks_to_frontside():
    shapes = {"A": _shape("A", [(5, 5), (6, 6)])}
    front = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    back = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, front, back) == "frontside"


def test_side_prefix_center_in_neither_returns_none():
    shapes = {"A": _shape("A", [(100, 100), (101, 101)])}
    front = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    back = {"x0": 50, "y0": 50, "x1": 60, "y1": 60}
    assert side_prefix_for(["A"], shapes, front, back) is None


def test_side_prefix_both_rects_none_returns_none():
    shapes = {"A": _shape("A", [(5, 5)])}
    assert side_prefix_for(["A"], shapes, None, None) is None


def test_side_prefix_only_frontside_set():
    shapes = {
        "A": _shape("A", [(1, 1)]),       # inside frontside
        "B": _shape("B", [(50, 50)]),     # outside everything
    }
    front = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, front, None) == "frontside"
    assert side_prefix_for(["B"], shapes, front, None) is None


def test_side_prefix_only_bottomside_set():
    shapes = {"A": _shape("A", [(7, 7)])}
    back = {"x0": 5, "y0": 5, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, None, back) == "bottomside"


def test_side_prefix_multi_entity_uses_combined_bbox():
    # Two entities — the combined bbox center is around (5, 5), inside frontside.
    shapes = {
        "A": _shape("A", [(0, 0), (1, 1)]),
        "B": _shape("B", [(9, 9), (10, 10)]),
    }
    front = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    back = {"x0": 100, "y0": 100, "x1": 110, "y1": 110}
    assert side_prefix_for(["A", "B"], shapes, front, back) == "frontside"


def test_side_prefix_unknown_handles_returns_none():
    shapes = {"A": _shape("A", [(5, 5)])}
    front = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    # All handles miss the map → no usable bbox → no prefix.
    assert side_prefix_for(["X", "Y"], shapes, front, None) is None


def test_side_prefix_empty_points_returns_none():
    shapes = {"A": _shape("A", [])}
    front = {"x0": -1, "y0": -1, "x1": 1, "y1": 1}
    assert side_prefix_for(["A"], shapes, front, None) is None


# ---- split_matches_by_side -----------------------------------------------
def _mr(handles: list[str]) -> MatchResult:
    return MatchResult(handles=handles, score=0.0, scale=1.0)


def test_split_matches_splits_instances_across_sides():
    # Three matches: two centers in frontside, one in bottomside.
    shapes = {
        "F1": _shape("F1", [(1, 1)]),
        "F2": _shape("F2", [(2, 2)]),
        "B1": _shape("B1", [(50, 50)]),
    }
    front = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    back = {"x0": 40, "y0": 40, "x1": 60, "y1": 60}
    matches = [_mr(["F1"]), _mr(["B1"]), _mr(["F2"])]
    out, counts = split_matches_by_side("smd.0", matches, shapes, front, back)
    assert out == {
        "frontside.smd.0": [["F1"], ["F2"]],
        "bottomside.smd.0": [["B1"]],
    }
    assert counts == {"frontside": 2, "bottomside": 1, "unassigned": 0}


def test_split_matches_keeps_unassigned_unprefixed():
    # No rectangles set → every instance falls under base_key, no prefix.
    shapes = {"H": _shape("H", [(5, 5)])}
    out, counts = split_matches_by_side("smd.0", [_mr(["H"])], shapes, None, None)
    assert out == {"smd.0": [["H"]]}
    assert counts == {"frontside": 0, "bottomside": 0, "unassigned": 1}


def test_split_matches_partial_outside_region_is_unassigned():
    shapes = {
        "F": _shape("F", [(1, 1)]),
        "Z": _shape("Z", [(100, 100)]),  # outside both
    }
    front = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    back = {"x0": 50, "y0": 50, "x1": 60, "y1": 60}
    out, counts = split_matches_by_side(
        "smd.0", [_mr(["F"]), _mr(["Z"])], shapes, front, back,
    )
    assert out == {
        "frontside.smd.0": [["F"]],
        "smd.0": [["Z"]],
    }
    assert counts == {"frontside": 1, "bottomside": 0, "unassigned": 1}


def test_split_matches_preserves_instance_order_within_side():
    shapes = {
        f"H{i}": _shape(f"H{i}", [(float(i), 1.0)]) for i in range(5)
    }
    front = {"x0": -1, "y0": 0, "x1": 10, "y1": 2}
    matches = [_mr([f"H{i}"]) for i in range(5)]
    out, _ = split_matches_by_side("smd.0", matches, shapes, front, None)
    assert out == {
        "frontside.smd.0": [["H0"], ["H1"], ["H2"], ["H3"], ["H4"]],
    }

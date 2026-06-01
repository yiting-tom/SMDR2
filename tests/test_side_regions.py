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


# ---- side_prefix_for ------------------------------------------------------
def test_side_prefix_center_in_top_view():
    shapes = {"A": _shape("A", [(0, 0), (2, 0), (2, 2), (0, 2)])}
    top = {"x0": -5, "y0": -5, "x1": 5, "y1": 5}
    bottom = {"x0": 100, "y0": 100, "x1": 110, "y1": 110}
    assert side_prefix_for(["A"], shapes, top, bottom, None) == "top_view"


def test_side_prefix_center_in_bottom_view():
    shapes = {"A": _shape("A", [(50, 50), (52, 50), (52, 52), (50, 52)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    bottom = {"x0": 40, "y0": 40, "x1": 60, "y1": 60}
    assert side_prefix_for(["A"], shapes, top, bottom, None) == "bottom_view"


def test_side_prefix_center_in_side_view_only():
    shapes = {"A": _shape("A", [(200, 200)])}
    side = {"x0": 190, "y0": 190, "x1": 210, "y1": 210}
    assert side_prefix_for(["A"], shapes, None, None, side) == "side_view"


def test_side_prefix_overlap_priority_top_over_bottom():
    # top_view and bottom_view overlap on the same point — top_view wins.
    shapes = {"A": _shape("A", [(5, 5), (6, 6)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    bottom = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, top, bottom, None) == "top_view"


def test_side_prefix_overlap_priority_top_over_side():
    # top_view and side_view overlap — top_view wins.
    shapes = {"A": _shape("A", [(5, 5), (6, 6)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    side = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, top, None, side) == "top_view"


def test_side_prefix_overlap_priority_bottom_over_side_when_top_absent():
    # No top_view set; bottom_view and side_view overlap — bottom_view wins.
    shapes = {"A": _shape("A", [(5, 5)])}
    bottom = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    side = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, None, bottom, side) == "bottom_view"


def test_side_prefix_all_three_overlap_resolves_to_top():
    # All three rectangles cover the same point — priority forces top_view.
    shapes = {"A": _shape("A", [(5, 5)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    bottom = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    side = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, top, bottom, side) == "top_view"


def test_side_prefix_center_in_neither_returns_none():
    shapes = {"A": _shape("A", [(100, 100), (101, 101)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    bottom = {"x0": 50, "y0": 50, "x1": 60, "y1": 60}
    side = {"x0": 70, "y0": 70, "x1": 80, "y1": 80}
    assert side_prefix_for(["A"], shapes, top, bottom, side) is None


def test_side_prefix_all_rects_none_returns_none():
    shapes = {"A": _shape("A", [(5, 5)])}
    assert side_prefix_for(["A"], shapes, None, None, None) is None


def test_side_prefix_only_top_view_set():
    shapes = {
        "A": _shape("A", [(1, 1)]),       # inside top_view
        "B": _shape("B", [(50, 50)]),     # outside everything
    }
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, top, None, None) == "top_view"
    assert side_prefix_for(["B"], shapes, top, None, None) is None


def test_side_prefix_only_bottom_view_set():
    shapes = {"A": _shape("A", [(7, 7)])}
    bottom = {"x0": 5, "y0": 5, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, None, bottom, None) == "bottom_view"


def test_side_prefix_only_side_view_set():
    shapes = {"A": _shape("A", [(7, 7)])}
    side = {"x0": 5, "y0": 5, "x1": 10, "y1": 10}
    assert side_prefix_for(["A"], shapes, None, None, side) == "side_view"


def test_side_prefix_multi_entity_uses_combined_bbox():
    # Two entities — the combined bbox center is around (5, 5), inside top_view.
    shapes = {
        "A": _shape("A", [(0, 0), (1, 1)]),
        "B": _shape("B", [(9, 9), (10, 10)]),
    }
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    bottom = {"x0": 100, "y0": 100, "x1": 110, "y1": 110}
    assert side_prefix_for(["A", "B"], shapes, top, bottom, None) == "top_view"


def test_side_prefix_unknown_handles_returns_none():
    shapes = {"A": _shape("A", [(5, 5)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    # All handles miss the map → no usable bbox → no prefix.
    assert side_prefix_for(["X", "Y"], shapes, top, None, None) is None


def test_side_prefix_empty_points_returns_none():
    shapes = {"A": _shape("A", [])}
    top = {"x0": -1, "y0": -1, "x1": 1, "y1": 1}
    assert side_prefix_for(["A"], shapes, top, None, None) is None


# ---- split_matches_by_side -----------------------------------------------
def _mr(handles: list[str]) -> MatchResult:
    return MatchResult(handles=handles, score=0.0, scale=1.0)


def test_split_matches_splits_instances_across_three_views():
    # Four matches: two in top_view, one in bottom_view, one in side_view.
    shapes = {
        "T1": _shape("T1", [(1, 1)]),
        "T2": _shape("T2", [(2, 2)]),
        "B1": _shape("B1", [(50, 50)]),
        "S1": _shape("S1", [(200, 200)]),
    }
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    bottom = {"x0": 40, "y0": 40, "x1": 60, "y1": 60}
    side = {"x0": 190, "y0": 190, "x1": 210, "y1": 210}
    matches = [_mr(["T1"]), _mr(["B1"]), _mr(["T2"]), _mr(["S1"])]
    out, counts = split_matches_by_side(
        "smd.0", matches, shapes, top, bottom, side, class_name="DieArea",
    )
    assert out == {
        "top_view.smd.0": [["T1"], ["T2"]],
        "bottom_view.smd.0": [["B1"]],
        "side_view.smd.0": [["S1"]],
    }
    assert counts == {
        "top_view": 2, "bottom_view": 1, "side_view": 1,
        "unassigned": 0, "dropped": 0,
    }


def test_split_matches_only_side_view_set():
    # Only side_view is non-null; an instance inside it gets the side_view prefix.
    shapes = {"S": _shape("S", [(5, 5)])}
    side = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    out, counts = split_matches_by_side(
        "smd.0", [_mr(["S"])], shapes, None, None, side, class_name="DieArea",
    )
    assert out == {"side_view.smd.0": [["S"]]}
    assert counts == {
        "top_view": 0, "bottom_view": 0, "side_view": 1,
        "unassigned": 0, "dropped": 0,
    }


def test_split_matches_keeps_unassigned_unprefixed():
    # No rectangles set → every instance falls under base_key, no prefix.
    shapes = {"H": _shape("H", [(5, 5)])}
    out, counts = split_matches_by_side(
        "smd.0", [_mr(["H"])], shapes, None, None, None, class_name="DieArea",
    )
    assert out == {"smd.0": [["H"]]}
    assert counts == {
        "top_view": 0, "bottom_view": 0, "side_view": 0,
        "unassigned": 1, "dropped": 0,
    }


def test_split_matches_partial_outside_region_is_unassigned():
    shapes = {
        "T": _shape("T", [(1, 1)]),
        "Z": _shape("Z", [(100, 100)]),  # outside all three
    }
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    bottom = {"x0": 50, "y0": 50, "x1": 60, "y1": 60}
    side = {"x0": 70, "y0": 70, "x1": 80, "y1": 80}
    out, counts = split_matches_by_side(
        "smd.0", [_mr(["T"]), _mr(["Z"])], shapes, top, bottom, side,
        class_name="DieArea",
    )
    assert out == {
        "top_view.smd.0": [["T"]],
        "smd.0": [["Z"]],
    }
    assert counts == {
        "top_view": 1, "bottom_view": 0, "side_view": 0,
        "unassigned": 1, "dropped": 0,
    }


def test_split_matches_all_three_overlap_resolves_to_top():
    # Single point covered by all three — every match is emitted under top_view.
    shapes = {f"H{i}": _shape(f"H{i}", [(5.0, 5.0)]) for i in range(3)}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    bottom = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    side = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    matches = [_mr([f"H{i}"]) for i in range(3)]
    out, counts = split_matches_by_side(
        "smd.0", matches, shapes, top, bottom, side, class_name="DieArea",
    )
    assert out == {"top_view.smd.0": [["H0"], ["H1"], ["H2"]]}
    assert counts == {
        "top_view": 3, "bottom_view": 0, "side_view": 0,
        "unassigned": 0, "dropped": 0,
    }


def test_split_matches_preserves_instance_order_within_side():
    shapes = {
        f"H{i}": _shape(f"H{i}", [(float(i), 1.0)]) for i in range(5)
    }
    top = {"x0": -1, "y0": 0, "x1": 10, "y1": 2}
    matches = [_mr([f"H{i}"]) for i in range(5)]
    out, _ = split_matches_by_side(
        "smd.0", matches, shapes, top, None, None, class_name="DieArea",
    )
    assert out == {
        "top_view.smd.0": [["H0"], ["H1"], ["H2"], ["H3"], ["H4"]],
    }


# ---- Class-view constraint filter ----------------------------------------
def test_c4ball_in_top_view_is_kept():
    """C4Ball matches inside top_view survive the filter."""
    shapes = {"T": _shape("T", [(5, 5)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    out, counts = split_matches_by_side(
        "c4_ball.0", [_mr(["T"])], shapes, top, None, None, class_name="C4Ball",
    )
    assert out == {"top_view.c4_ball.0": [["T"]]}
    assert counts["top_view"] == 1
    assert counts["dropped"] == 0


def test_c4ball_in_bottom_view_is_dropped():
    """C4Ball in bottom_view violates physics — drop, not relabel."""
    shapes = {"B": _shape("B", [(55, 55)])}
    bottom = {"x0": 50, "y0": 50, "x1": 60, "y1": 60}
    out, counts = split_matches_by_side(
        "c4_ball.0", [_mr(["B"])], shapes, None, bottom, None, class_name="C4Ball",
    )
    assert out == {}
    assert counts["bottom_view"] == 0
    assert counts["dropped"] == 1


def test_c4ball_in_side_view_is_dropped():
    shapes = {"S": _shape("S", [(205, 205)])}
    side = {"x0": 200, "y0": 200, "x1": 210, "y1": 210}
    out, counts = split_matches_by_side(
        "c4_ball.0", [_mr(["S"])], shapes, None, None, side, class_name="C4Ball",
    )
    assert out == {}
    assert counts["dropped"] == 1


def test_c4ball_unassigned_is_dropped():
    """No view rect → C4Ball is unassigned → strict mode drops it."""
    shapes = {"H": _shape("H", [(5, 5)])}
    out, counts = split_matches_by_side(
        "c4_ball.0", [_mr(["H"])], shapes, None, None, None, class_name="C4Ball",
    )
    assert out == {}
    assert counts["unassigned"] == 0
    assert counts["dropped"] == 1


def test_bgaball_in_bottom_view_is_kept():
    shapes = {"B": _shape("B", [(55, 55)])}
    bottom = {"x0": 50, "y0": 50, "x1": 60, "y1": 60}
    out, counts = split_matches_by_side(
        "bga_ball.0", [_mr(["B"])], shapes, None, bottom, None, class_name="BGABall",
    )
    assert out == {"bottom_view.bga_ball.0": [["B"]]}
    assert counts["bottom_view"] == 1
    assert counts["dropped"] == 0


def test_bgaball_in_side_view_is_dropped():
    """BGABall is bottom-only now (side_view retired) → a side_view instance
    is dropped under strict mode."""
    shapes = {"S": _shape("S", [(205, 205)])}
    side = {"x0": 200, "y0": 200, "x1": 210, "y1": 210}
    out, counts = split_matches_by_side(
        "bga_ball.0", [_mr(["S"])], shapes, None, None, side, class_name="BGABall",
    )
    assert out == {}
    assert counts["side_view"] == 0
    assert counts["dropped"] == 1


def test_bgaball_in_top_view_is_dropped():
    shapes = {"T": _shape("T", [(5, 5)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    out, counts = split_matches_by_side(
        "bga_ball.0", [_mr(["T"])], shapes, top, None, None, class_name="BGABall",
    )
    assert out == {}
    assert counts["top_view"] == 0
    assert counts["dropped"] == 1


def test_bgaball_unassigned_is_dropped():
    shapes = {"H": _shape("H", [(5, 5)])}
    out, counts = split_matches_by_side(
        "bga_ball.0", [_mr(["H"])], shapes, None, None, None, class_name="BGABall",
    )
    assert out == {}
    assert counts["dropped"] == 1


def test_unconstrained_class_unaffected_by_filter():
    """DieArea has no entry in CLASS_VIEW_CONSTRAINTS — every position is
    valid, including unassigned. Verifies the absent-key fall-through."""
    shapes = {
        "T": _shape("T", [(5, 5)]),
        "Z": _shape("Z", [(500, 500)]),  # outside all rects
    }
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    out, counts = split_matches_by_side(
        "smd.0", [_mr(["T"]), _mr(["Z"])], shapes,
        top, None, None, class_name="DieArea",
    )
    assert out == {
        "top_view.smd.0": [["T"]],
        "smd.0": [["Z"]],
    }
    assert counts["top_view"] == 1
    assert counts["unassigned"] == 1
    assert counts["dropped"] == 0


def test_mixed_classes_independent_filters():
    """Across one call we don't mix classes (split_matches_by_side is per
    class+template). But verify counts are reset properly each call."""
    shapes = {"T": _shape("T", [(5, 5)])}
    top = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    # C4Ball in top_view → kept
    _, c4_counts = split_matches_by_side(
        "c4_ball.0", [_mr(["T"])], shapes, top, None, None, class_name="C4Ball",
    )
    # BGABall in top_view → dropped
    _, bga_counts = split_matches_by_side(
        "bga_ball.0", [_mr(["T"])], shapes, top, None, None, class_name="BGABall",
    )
    assert c4_counts["dropped"] == 0
    assert c4_counts["top_view"] == 1
    assert bga_counts["dropped"] == 1
    assert bga_counts["top_view"] == 0

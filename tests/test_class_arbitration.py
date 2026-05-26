"""Unit + integration tests for app/class_arbitration.py.

Covers:
- derive_pitch (median NN, robust to outliers)
- count_neighbors (centre/edge/corner of a 3×3 grid)
- classify (Min/Max rule application + default fallback)
- apply_population_fallback (trigger / non-trigger)
- arbitrate end-to-end on a synthetic 10×10 BGA grid + 4 fiducials
- population fallback when only 4 fiducials exist (no real BGA)
- view-conflict reassignment drops + accounting
- determinism under input shuffling
"""

from __future__ import annotations

import numpy as np
import pytest

from app.class_arbitration import (
    apply_population_fallback,
    arbitrate,
    classify,
    count_neighbors,
    derive_pitch,
)
from app.library import (
    ArbitrationGroup,
    MaxNeighbors,
    MinNeighbors,
)


# ---- Helpers --------------------------------------------------------------
class _FakeShape:
    """Minimal stand-in for matching.EntityShape — only the `points`
    attribute is consulted by the arbitration pipeline (via the same
    bbox-centre helper used by side_regions)."""

    def __init__(self, x: float, y: float) -> None:
        self.points = np.array([[x, y]], dtype=np.float64)


def _shapes_from_coords(coords: dict[str, tuple[float, float]]) -> dict[str, _FakeShape]:
    return {h: _FakeShape(x, y) for h, (x, y) in coords.items()}


def _bga_fiducial_group() -> ArbitrationGroup:
    return ArbitrationGroup(
        members=frozenset({"BGABall", "FiducialCircle"}),
        rules={
            "BGABall":        MinNeighbors(2),
            "FiducialCircle": MaxNeighbors(1),
        },
        default_class="FiducialCircle",
        min_population=8,
        pitch_multiplier=1.5,
    )


# ---- derive_pitch ---------------------------------------------------------
def test_derive_pitch_median_with_outlier():
    """4 grid-spaced points + 1 far outlier → median NN is the grid pitch."""
    pts = np.array(
        [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0], [10.0, 10.0]]
    )
    assert derive_pitch(pts) == pytest.approx(1.0)


def test_derive_pitch_returns_none_for_singleton():
    assert derive_pitch(np.array([[0.0, 0.0]])) is None


def test_derive_pitch_returns_none_for_empty():
    assert derive_pitch(np.zeros((0, 2))) is None


def test_derive_pitch_robust_under_corner_fiducials():
    """10×10 BGA grid at pitch 0.4 mm + 4 corner fiducials; median pitch
    stays within ±5% of 0.4."""
    pitch = 0.4
    grid = np.array(
        [[i * pitch, j * pitch] for i in range(10) for j in range(10)]
    )
    fids = np.array([
        [-5.0, -5.0], [-5.0, 10.0], [10.0, -5.0], [10.0, 10.0],
    ])
    pts = np.vstack([grid, fids])
    derived = derive_pitch(pts)
    assert derived is not None
    assert 0.95 * pitch <= derived <= 1.05 * pitch


# ---- count_neighbors ------------------------------------------------------
def test_count_neighbors_3x3_grid():
    """3×3 grid at pitch 1.0, radius 1.5 → centre 8, edge 5, corner 3."""
    pts = np.array(
        [[i, j] for i in range(3) for j in range(3)],
        dtype=np.float64,
    )
    counts = count_neighbors(pts, radius=1.5)
    # Centre is index 4 (i=1,j=1).
    assert counts[4] == 8
    # Corners (i,j) ∈ {(0,0),(0,2),(2,0),(2,2)} have indices 0, 2, 6, 8.
    for ci in (0, 2, 6, 8):
        assert counts[ci] == 3, f"corner {ci} expected 3 neighbours"
    # Edge midpoints — indices 1, 3, 5, 7.
    for ei in (1, 3, 5, 7):
        assert counts[ei] == 5, f"edge {ei} expected 5 neighbours"


def test_count_neighbors_empty():
    assert count_neighbors(np.zeros((0, 2)), 1.0).shape == (0,)


# ---- classify --------------------------------------------------------------
def test_classify_min_max_and_default():
    g = _bga_fiducial_group()
    counts = np.array([0, 1, 2, 5])
    # 0 → MaxNeighbors(1) matches (0 ≤ 1) → FiducialCircle (BGA's
    # MinNeighbors(2) is checked first but 0 ≥ 2 is false)
    # 1 → BGA fails (1 ≥ 2 is false); Fiducial passes (1 ≤ 1) → FiducialCircle
    # 2 → BGA passes (2 ≥ 2) → BGABall
    # 5 → BGA passes → BGABall
    out = classify(counts, g)
    assert out == ["FiducialCircle", "FiducialCircle", "BGABall", "BGABall"]


def test_classify_falls_back_to_default_when_no_rule_matches():
    """A rule pair that leaves a gap (only MinNeighbors(10)) → counts < 10
    fall through to default_class."""
    g = ArbitrationGroup(
        members=frozenset({"BGABall", "FiducialCircle"}),
        rules={
            "BGABall":        MinNeighbors(10),
            "FiducialCircle": MinNeighbors(20),  # both rules are >= variants
        },
        default_class="FiducialCircle",
    )
    counts = np.array([0, 5])
    out = classify(counts, g)
    # Neither rule matches → falls back to default for both.
    assert out == ["FiducialCircle", "FiducialCircle"]


# ---- apply_population_fallback -------------------------------------------
def test_population_fallback_triggers_below_floor():
    g = _bga_fiducial_group()
    # 4 BGA classifications, 0 fiducials → below min_population=8.
    out = apply_population_fallback(["BGABall"] * 4, g)
    assert out == ["FiducialCircle"] * 4


def test_population_fallback_preserves_when_all_above_floor():
    g = _bga_fiducial_group()
    inp = ["BGABall"] * 100 + ["FiducialCircle"] * 8
    out = apply_population_fallback(list(inp), g)
    assert out == inp


def test_population_fallback_only_floors_non_default_classes():
    g = _bga_fiducial_group()
    # BGA above floor, FiducialCircle (default) at 3 — fallback does NOT
    # trigger because the floor applies only to non-default members.
    inp = ["BGABall"] * 50 + ["FiducialCircle"] * 3
    out = apply_population_fallback(list(inp), g)
    assert out == inp


# ---- arbitrate end-to-end ------------------------------------------------
def _make_10x10_bga_plus_4_fiducials() -> tuple[
    dict[str, list[list[str]]],
    dict[str, _FakeShape],
]:
    """Post-view-split state: 100 grid handles fired in both BGABall AND
    FiducialCircle templates within bottom_view; 4 corner handles fired
    only in FiducialCircle (BGABall is view-constrained to bottom/side so
    its top_view matches were already dropped by split_matches_by_side).

    Total unique physical instances: 104. Arbitration should dedupe these
    out of the pool and assign 100 grid → BGABall, 4 corners → FiducialCircle.
    """
    pitch = 0.4
    coords: dict[str, tuple[float, float]] = {}
    grid_handles: list[list[str]] = []
    for i in range(10):
        for j in range(10):
            h = f"grid_{i}_{j}"
            coords[h] = (i * pitch, j * pitch)
            grid_handles.append([h])
    fid_handles: list[list[str]] = []
    for k, (x, y) in enumerate([(-5.0, -5.0), (-5.0, 10.0),
                                 (10.0, -5.0), (10.0, 10.0)]):
        h = f"fid_{k}"
        coords[h] = (x, y)
        fid_handles.append([h])

    out = {
        # Matcher's BGA template fired on the grid (in bottom_view).
        "bottom_view.bga_ball.0": list(grid_handles),
        # Matcher's FiducialCircle template also fired on the grid (same
        # handles, cross-fire — the bug arbitration resolves).
        "bottom_view.fiducial_circle.0": list(grid_handles),
        # Plus the 4 real fiducials in top_view (BGA's matches there were
        # dropped by split_matches_by_side; FiducialCircle is unconstrained).
        "top_view.fiducial_circle.0": list(fid_handles),
    }
    return out, _shapes_from_coords(coords)


def test_arbitrate_bga_grid_plus_corner_fiducials():
    out, shapes = _make_10x10_bga_plus_4_fiducials()
    new_out, counts, view_drops = arbitrate(
        out, shapes, [_bga_fiducial_group()]
    )

    label = "BGABall|FiducialCircle"
    assert label in counts
    gc = counts[label]
    # Pool is deduped by handle set: 100 grid + 4 corners = 104 physical
    # instances even though the matcher cross-fired them across 2 classes.
    assert gc["pool_size"] == 104
    assert gc["derived_pitch"] == pytest.approx(0.4, abs=0.05)
    assert gc["assigned"]["BGABall"] == 100
    assert gc["assigned"]["FiducialCircle"] == 4
    assert gc["population_fallback_triggered"] is False

    # Output keys: every physical instance now appears under exactly one
    # class. The 100 grid handles → bottom_view.bga_ball.* ; the 4 corner
    # handles → top_view.fiducial_circle.* . No cross-fire remains.
    bga_keys = [k for k in new_out if "bga_ball" in k]
    fid_keys = [k for k in new_out if "fiducial_circle" in k]
    bga_total = sum(len(new_out[k]) for k in bga_keys)
    fid_total = sum(len(new_out[k]) for k in fid_keys)
    assert bga_total == 100
    assert fid_total == 4

    bga_handles: set[str] = set()
    for k in bga_keys:
        for hl in new_out[k]:
            bga_handles.update(hl)
    fid_handles: set[str] = set()
    for k in fid_keys:
        for hl in new_out[k]:
            fid_handles.update(hl)
    assert bga_handles.isdisjoint(fid_handles), (
        "no handle should be in both BGA and Fiducial buckets"
    )

    # All assignments respect view constraints (no top_view BGA),
    # so no view-drops should occur.
    assert view_drops == {}


def test_arbitrate_only_corner_fiducials_triggers_population_fallback():
    """A file with ONLY 4 corner fiducials and no BGA grid: median NN
    pitch becomes the diagonal-ish distance between corners, and the
    corners technically have ≥2 neighbours within 1.5× that pitch.
    Without the fallback they'd all classify as BGA. With min_population=8,
    they collapse to FiducialCircle."""
    coords = {
        "f0": (-5.0, -5.0),
        "f1": (-5.0, 10.0),
        "f2": (10.0, -5.0),
        "f3": (10.0, 10.0),
    }
    shapes = _shapes_from_coords(coords)
    out = {
        "top_view.fiducial_circle.0": [["f0"], ["f1"], ["f2"], ["f3"]],
    }
    new_out, counts, _drops = arbitrate(out, shapes, [_bga_fiducial_group()])
    label = "BGABall|FiducialCircle"
    assert counts[label]["population_fallback_triggered"] is True
    # All 4 land under fiducial keys.
    fid_keys = [k for k in new_out if "fiducial_circle" in k]
    fid_total = sum(len(new_out[k]) for k in fid_keys)
    assert fid_total == 4
    assert all("bga_ball" not in k for k in new_out)


def test_arbitrate_view_conflict_drops_instance():
    """When arbitration reassigns FiducialCircle → BGABall but the
    instance sits in top_view (disallowed for BGA), it is dropped."""
    # 3×3 grid in top_view, all originally keyed under FiducialCircle
    # (matcher cross-fire). With min_population=1 the fallback won't
    # rescue them; every grid point has ≥ 2 neighbours → classifies as
    # BGABall → top_view is disallowed → all 9 dropped.
    coords = {f"p{i}_{j}": (i, j) for i in range(3) for j in range(3)}
    shapes = _shapes_from_coords(coords)
    out = {
        "top_view.fiducial_circle.0": [
            [f"p{i}_{j}"] for i in range(3) for j in range(3)
        ],
    }
    group = ArbitrationGroup(
        members=frozenset({"BGABall", "FiducialCircle"}),
        rules={
            "BGABall":        MinNeighbors(2),
            "FiducialCircle": MaxNeighbors(1),
        },
        default_class="FiducialCircle",
        min_population=1,
        pitch_multiplier=1.5,
    )
    new_out, counts, view_drops = arbitrate(out, shapes, [group])
    label = "BGABall|FiducialCircle"
    assert counts[label]["dropped_by_view"] == 9
    assert view_drops[label]["top_view"] == 9
    # No surviving keys — every instance was dropped.
    assert new_out == {}


def test_arbitrate_collapses_to_template_index_not_instance_index():
    """`out_key.<idx>` MUST represent the *template index* of the
    resolved class, NOT a per-instance sequence number. So when a class
    has N matched instances all originating from the same template
    (the common case for single-template classes like FiducialCircle
    and BGABall), they must collapse under one key whose value is a
    list of N handle arrays — mirroring how `c4_ball.0` carries every
    matched C4Ball instance under one key.

    Regression: previously `arbitrate` assigned a fresh per-instance
    sequence (`top_view.fiducial_circle.0`, `.1`, `.2`, …), one key
    per match. Downstream rule-checker consumers expected the
    template-index convention used by non-arbitration classes.
    """
    # 4 corner fiducials in top_view, all originally
    # `top_view.fiducial_circle.0` (single template). Population
    # fallback keeps them all as FiducialCircle.
    coords = {
        "f0": (-5.0, -5.0),
        "f1": (-5.0, 10.0),
        "f2": (10.0, -5.0),
        "f3": (10.0, 10.0),
    }
    shapes = _shapes_from_coords(coords)
    out = {
        "top_view.fiducial_circle.0": [["f0"], ["f1"], ["f2"], ["f3"]],
    }
    new_out, _counts, _drops = arbitrate(out, shapes, [_bga_fiducial_group()])

    fid_keys = [k for k in new_out if "fiducial_circle" in k]
    assert fid_keys == ["top_view.fiducial_circle.0"], (
        f"expected one collapsed key per (view, class, template_idx), "
        f"got {fid_keys}"
    )
    assert len(new_out["top_view.fiducial_circle.0"]) == 4, (
        "all 4 instances should live under the single template-0 key "
        "as 4 separate handle arrays"
    )


def test_arbitrate_reassigned_instance_keyed_under_zero():
    """When arbitration reassigns an instance to a different class
    (e.g. matcher cross-fire fired it as both BGABall and FiducialCircle,
    and the neighbour-count rule resolves it to FiducialCircle), the
    output key uses `.0` as the template index — `original_idx` was a
    template position in the SOURCE class and carries no meaning in
    the NEW class's template list.

    Setup: 4 corner instances cross-fired to BGABall.1 (a hypothetical
    second BGA template) AND FiducialCircle.0. Population fallback
    forces them to FiducialCircle. Output MUST be
    `top_view.fiducial_circle.0`, not `.1`.
    """
    coords = {
        "f0": (-5.0, -5.0),
        "f1": (-5.0, 10.0),
        "f2": (10.0, -5.0),
        "f3": (10.0, 10.0),
    }
    shapes = _shapes_from_coords(coords)
    # Source: every instance appears under TWO keys (cross-fire). The
    # BGABall side uses template index 1 — which would be meaningless
    # if re-emitted under FiducialCircle. Note BGABall keys must use
    # bottom_view (top_view is disallowed) for the input to be valid.
    out = {
        "top_view.fiducial_circle.0":   [["f0"], ["f1"], ["f2"], ["f3"]],
        "bottom_view.bga_ball.1":       [["f0"], ["f1"], ["f2"], ["f3"]],
    }
    new_out, _counts, _drops = arbitrate(out, shapes, [_bga_fiducial_group()])

    fid_keys = [k for k in new_out if "fiducial_circle" in k]
    # `pool_instances` dedupes by handle set and keeps the lex-first
    # source key's (view, class, idx) as the instance's `original_*`
    # fields. `bottom_view.bga_ball.1` sorts before
    # `top_view.fiducial_circle.0`, so each instance enters arbitration
    # carrying view=bottom_view, original_class=BGABall, original_idx=1.
    # Population fallback then reassigns BGABall → FiducialCircle. The
    # invariant we're locking in: the output `.idx` SHALL be 0 (the new
    # class's template index), NOT the original_idx (1, which was a
    # BGABall template position and is meaningless under FiducialCircle).
    assert fid_keys == ["bottom_view.fiducial_circle.0"], (
        f"reassigned instances must land under .0 of the new class, "
        f"not the original .1 from the source key. Got {fid_keys}"
    )


def test_arbitrate_is_deterministic_under_shuffle():
    """Same input run twice produces equal output, regardless of dict
    insertion order or list order within keys."""
    out_a, shapes = _make_10x10_bga_plus_4_fiducials()
    new_a, counts_a, _ = arbitrate(out_a, shapes, [_bga_fiducial_group()])

    # Build a shuffled version: reverse list contents and rotate dict.
    out_b: dict[str, list[list[str]]] = {}
    items = list(out_a.items())
    for k, v in reversed(items):
        out_b[k] = list(reversed(v))
    new_b, counts_b, _ = arbitrate(out_b, shapes, [_bga_fiducial_group()])

    # Compare key sets and per-key list contents as sorted sets so we are
    # not order-coupled at the assertion level — the *output* ordering is
    # already total-ordered by the spec, but the test is shuffle-immune.
    assert set(new_a.keys()) == set(new_b.keys())
    for k in new_a:
        a_handles = sorted(tuple(hl) for hl in new_a[k])
        b_handles = sorted(tuple(hl) for hl in new_b[k])
        assert a_handles == b_handles, f"mismatch at {k!r}"
    assert counts_a == counts_b


def test_arbitrate_noop_when_no_member_classes_present():
    """A library that has only non-arbitrated classes → arbitrate
    returns out untouched and empty counts."""
    coords = {"a": (0.0, 0.0), "b": (1.0, 0.0)}
    shapes = _shapes_from_coords(coords)
    out = {
        "substrate.0": [["a"]],
        "smd_2t.0": [["b"]],
    }
    new_out, counts, drops = arbitrate(out, shapes, [_bga_fiducial_group()])
    assert new_out == out
    # Empty pool → group recorded with pool_size=0, no assignments.
    assert counts == {
        "BGABall|FiducialCircle": {
            "pool_size": 0,
            "derived_pitch": None,
            "assigned": {},
            "reassigned_from_match": 0,
            "population_fallback_triggered": False,
            "dropped_by_view": 0,
        }
    }
    assert drops == {}

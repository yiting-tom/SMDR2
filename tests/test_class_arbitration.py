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
    arbitrate_for_match,
    arbitrate_for_prematch,
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


def test_arbitrate_only_corner_fiducials_short_circuits_via_single_class_guard():
    """A file with ONLY 4 corner fiducials and no BGA grid. The library
    only has FiducialCircle templates → every pool instance has
    `original_class=FiducialCircle` → the single-class guard short-circuits
    arbitration before classify/fallback runs. End result (all 4 stay
    FiducialCircle) is identical to the historical "classify mis-labels
    as BGA → population fallback collapses back to FiducialCircle" path,
    but the diagnostic now records short-circuit instead of fallback."""
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
    # Short-circuit path: fallback never ran, pitch never derived.
    assert counts[label]["population_fallback_triggered"] is False
    assert counts[label]["derived_pitch"] is None
    assert counts[label]["assigned"] == {"FiducialCircle": 4}
    # All 4 land under fiducial keys (source keys, unmodified).
    fid_keys = [k for k in new_out if "fiducial_circle" in k]
    fid_total = sum(len(new_out[k]) for k in fid_keys)
    assert fid_total == 4
    assert all("bga_ball" not in k for k in new_out)


def test_arbitrate_view_conflict_drops_instance():
    """When arbitration reassigns FiducialCircle → BGABall but the
    instance sits in top_view (disallowed for BGA), it is dropped.

    Setup: 3×3 grid in top_view originally keyed under FiducialCircle
    (matcher cross-fire) → classify reassigns to BGABall (≥2 neighbours)
    → BGABall not allowed in top_view → 9 dropped. A geometrically
    distant BGABall anchor in bottom_view ensures the pool is multi-class
    (BGABall + FiducialCircle both in `original_class` set), so the
    single-class short-circuit doesn't fire and the classify-then-reemit
    path runs. The anchor itself has no neighbours within radius so it
    classifies as FiducialCircle and survives in bottom_view (FiducialCircle
    is unconstrained)."""
    coords = {f"p{i}_{j}": (i, j) for i in range(3) for j in range(3)}
    # Far anchor: in bottom_view so its source-key view is valid for
    # BGABall (otherwise it would be dropped by split_matches_by_side
    # upstream of arbitrate); isolated so its classify outcome is
    # FiducialCircle (0 neighbours within any sensible radius).
    coords["anchor"] = (1000.0, 1000.0)
    shapes = _shapes_from_coords(coords)
    out = {
        "top_view.fiducial_circle.0": [
            [f"p{i}_{j}"] for i in range(3) for j in range(3)
        ],
        "bottom_view.bga_ball.0": [["anchor"]],
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
    # 9 grid points reassigned to BGABall → all dropped by top_view conflict.
    assert counts[label]["dropped_by_view"] == 9
    assert view_drops[label]["top_view"] == 9
    # Anchor survives as FiducialCircle in bottom_view (unconstrained).
    fid_keys = [k for k in new_out if "fiducial_circle" in k]
    assert fid_keys == ["bottom_view.fiducial_circle.0"]


# ---- Context-specific entry points (prematch vs. match) -------------------
def _cross_fire_group_min1() -> ArbitrationGroup:
    """BGA/Fiducial group with min_population=1 so the population fallback
    does not fire and classify's raw neighbour-count targets stand."""
    return ArbitrationGroup(
        members=frozenset({"BGABall", "FiducialCircle"}),
        rules={
            "BGABall":        MinNeighbors(2),
            "FiducialCircle": MaxNeighbors(1),
        },
        default_class="FiducialCircle",
        min_population=1,
        pitch_multiplier=1.5,
    )


def test_arbitrate_for_prematch_preserves_view_constrained_instances():
    """At prematch, side regions aren't drawn so every key is unprefixed
    (view_prefix=None). A 3×3 grid that classify reassigns to BGABall must
    be KEPT — strict mode would drop all 9 because BGABall disallows a None
    prefix. The isolated anchor classifies to FiducialCircle and survives."""
    coords = {f"p{i}_{j}": (i, j) for i in range(3) for j in range(3)}
    coords["anchor"] = (1000.0, 1000.0)
    shapes = _shapes_from_coords(coords)
    out = {
        "fiducial_circle.0": [
            [f"p{i}_{j}"] for i in range(3) for j in range(3)
        ],
        "bga_ball.0": [["anchor"]],
    }
    new_out, counts, view_drops = arbitrate_for_prematch(
        out, shapes, [_cross_fire_group_min1()]
    )
    label = "BGABall|FiducialCircle"
    # Nothing dropped: the prematch entry point never enforces view rules.
    assert view_drops == {}
    assert counts[label]["dropped_by_view"] == 0
    # 9 grid points kept as BGABall under the unprefixed key; anchor stays
    # FiducialCircle.
    assert counts[label]["assigned"] == {"BGABall": 9, "FiducialCircle": 1}
    assert len(new_out["bga_ball.0"]) == 9


def test_arbitrate_for_match_drops_view_conflicting_instances():
    """The match entry point enforces view constraints: a top_view grid
    reassigned to BGABall (disallowed in top_view) is dropped, mirroring the
    strict-mode contract but routed through the public wrapper."""
    coords = {f"p{i}_{j}": (i, j) for i in range(3) for j in range(3)}
    coords["anchor"] = (1000.0, 1000.0)
    shapes = _shapes_from_coords(coords)
    out = {
        "top_view.fiducial_circle.0": [
            [f"p{i}_{j}"] for i in range(3) for j in range(3)
        ],
        "bottom_view.bga_ball.0": [["anchor"]],
    }
    new_out, counts, view_drops = arbitrate_for_match(
        out, shapes, [_cross_fire_group_min1()]
    )
    label = "BGABall|FiducialCircle"
    assert counts[label]["dropped_by_view"] == 9
    assert view_drops[label]["top_view"] == 9
    assert [k for k in new_out if "fiducial_circle" in k] == [
        "bottom_view.fiducial_circle.0"
    ]


def test_arbitrate_entry_points_agree_without_view_conflict():
    """When no reassignment hits a view constraint, the prematch and match
    entry points produce byte-identical output — the ONLY difference between
    them is view-conflict handling. Here the grid sits in bottom_view, which
    BGABall allows, so neither path drops anything."""
    def _build_out() -> dict[str, list[list[str]]]:
        return {
            "bottom_view.fiducial_circle.0": [
                [f"p{i}_{j}"] for i in range(3) for j in range(3)
            ],
            "bottom_view.bga_ball.0": [["anchor"]],
        }

    coords = {f"p{i}_{j}": (i, j) for i in range(3) for j in range(3)}
    coords["anchor"] = (1000.0, 1000.0)
    shapes = _shapes_from_coords(coords)
    group = _cross_fire_group_min1()

    pre_out, pre_counts, pre_drops = arbitrate_for_prematch(
        _build_out(), shapes, [group]
    )
    mat_out, mat_counts, mat_drops = arbitrate_for_match(
        _build_out(), shapes, [group]
    )
    assert pre_out == mat_out
    assert pre_counts == mat_counts
    assert pre_drops == mat_drops == {}


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

    Setup: 4 corner instances initially keyed under
    `bottom_view.bga_ball.1` (a hypothetical second BGA template). One
    additional anchor instance is keyed under
    `top_view.fiducial_circle.0` — its sole purpose is to satisfy the
    `default_in_pool` precondition so population fallback fires (without
    it, `default_class=FiducialCircle` would have zero instances in the
    pool and the fallback would correctly be suppressed). The 4 corner
    BGABalls are then reassigned to FiducialCircle; their output key
    MUST use `.0`, not the source `.1`.
    """
    coords = {
        "f0": (-5.0, -5.0),
        "f1": (-5.0, 10.0),
        "f2": (10.0, -5.0),
        "f3": (10.0, 10.0),
        "anchor": (50.0, 50.0),  # geometrically distant — won't be a neighbour
    }
    shapes = _shapes_from_coords(coords)
    out = {
        # The reassignment subject: 4 BGABall instances under template
        # index 1. Note BGABall keys must use bottom_view (top_view is
        # disallowed for BGABall) for the input to be matcher-realistic.
        "bottom_view.bga_ball.1":       [["f0"], ["f1"], ["f2"], ["f3"]],
        # FiducialCircle anchor so default_in_pool=True. Isolated → its
        # own classification doesn't disturb the BGA count rule.
        "top_view.fiducial_circle.0":   [["anchor"]],
    }
    new_out, _counts, _drops = arbitrate(out, shapes, [_bga_fiducial_group()])

    # The 4 corners came from `bottom_view.bga_ball.1` → population
    # fallback reassigns them to FiducialCircle → MUST land under
    # `bottom_view.fiducial_circle.0` (the new class's template index 0,
    # NOT the original .1 which was a BGABall template position).
    bottom_fid_keys = [
        k for k in new_out if k.startswith("bottom_view.fiducial_circle.")
    ]
    assert bottom_fid_keys == ["bottom_view.fiducial_circle.0"], (
        f"reassigned instances must land under .0 of the new class, "
        f"not the original .1 from the source key. Got {bottom_fid_keys}"
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


def test_arbitrate_skips_classify_when_pool_is_single_class():
    """User-reported regression: 17483 BGABall matches at pitch 0.9 mm,
    library has only BGABall templates. The matcher's BGABall template
    happens to cross-fire on a handful of isolated same-radius circles
    (vias / drill holes / decorative dots) in addition to the main BGA
    grid. Those isolated circles have 0 neighbours within `1.5 × pitch`
    → classify (if it ran) would label them FiducialCircle via
    `MaxNeighbors(1)` → they'd re-emit as `fiducial_circle.0` keys with
    no backing FiducialCircle template.

    With the single-class short-circuit, the pool's uniform
    `original_class=BGABall` triggers the early-return before classify
    runs at all. Every instance stays BGABall, no phantom
    fiducial_circle keys are produced.

    This test simulates the asymmetric-density case with a 4-corner
    spread + 2 far outliers — without the short-circuit, every instance
    has 0-1 neighbours within `1.5 × derived_pitch` → all classify as
    FiducialCircle → fallback precondition (no FiducialCircle in pool)
    correctly suppresses the collapse → but classify's per-instance
    FiducialCircle labels would still drive the re-emit → phantom keys.
    """
    coords = {
        "g0": (-5.0, -5.0),
        "g1": (-5.0, 10.0),
        "g2": (10.0, -5.0),
        "g3": (10.0, 10.0),
        # Two far outliers — guaranteed to have 0 neighbours within
        # any reasonable radius derived from the 4-corner cluster.
        "g4": (200.0, 200.0),
        "g5": (-200.0, -200.0),
    }
    shapes = _shapes_from_coords(coords)
    out = {
        "bottom_view.bga_ball.0": [
            ["g0"], ["g1"], ["g2"], ["g3"], ["g4"], ["g5"],
        ],
    }
    new_out, counts, _drops = arbitrate(out, shapes, [_bga_fiducial_group()])

    label = "BGABall|FiducialCircle"
    gc = counts[label]
    # Short-circuit fired before classify/pitch derivation.
    assert gc["population_fallback_triggered"] is False
    assert gc["derived_pitch"] is None
    assert gc["assigned"] == {"BGABall": 6}
    assert gc["reassigned_from_match"] == 0

    # No phantom FiducialCircle keys — the bug case.
    assert all("fiducial_circle" not in k for k in new_out), (
        f"phantom FiducialCircle key in {list(new_out)} — single-class "
        f"guard should have short-circuited"
    )

    # All 6 handles end up under the source bga_ball key (unchanged).
    bga_handles: set[str] = set()
    for k in new_out:
        if "bga_ball" in k:
            for hl in new_out[k]:
                bga_handles.update(hl)
    assert bga_handles == {f"g{i}" for i in range(6)}


def test_fallback_skipped_when_default_class_has_no_pool_evidence():
    """Library has ONLY BGABall templates (no FiducialCircle template).
    Pool contains 4 BGABall match instances, all from `bga_ball.0`.
    `default_class = FiducialCircle` is absent from the pool — no
    library template fired under it. Per-class population check would
    have triggered fallback (4 < 8), but the new `default_in_pool`
    precondition suppresses it. Every instance MUST stay BGABall,
    no phantom `fiducial_circle.*` key is emitted.

    Regression for the user-reported bug where deleting all
    FiducialCircle templates from the library still produced
    FiducialCircle labels in scan-all and the toolbar chip.
    """
    # Tight 2×2 grid in bottom_view (BGABall is allowed there). Each
    # cell has 2-3 neighbours within the derived pitch — `classify()`
    # labels them BGABall (≥2 neighbours). Without the precondition,
    # 4 < min_population=8 would trigger fallback → all FiducialCircle.
    pitch = 0.5
    coords = {
        f"g{i}_{j}": (i * pitch, j * pitch)
        for i in range(2) for j in range(2)
    }
    shapes = _shapes_from_coords(coords)
    out = {
        "bottom_view.bga_ball.0": [
            [f"g{i}_{j}"] for i in range(2) for j in range(2)
        ],
    }
    new_out, counts, _drops = arbitrate(out, shapes, [_bga_fiducial_group()])

    label = "BGABall|FiducialCircle"
    gc = counts[label]
    # Precondition: default_class absent from pool → no fallback.
    assert gc["population_fallback_triggered"] is False
    # Per-instance classify() labels would have to win on their own —
    # in this tight grid every instance has ≥ 2 neighbours within
    # 1.5 × derived pitch, so all 4 classify as BGABall.
    assert gc["assigned"].get("BGABall", 0) == 4
    assert gc["assigned"].get("FiducialCircle", 0) == 0

    # No `fiducial_circle.*` keys may be emitted — that was the bug.
    assert all("fiducial_circle" not in k for k in new_out), (
        f"phantom FiducialCircle key in {list(new_out)} — fallback "
        f"reassigned BGABall matches under a class with no template"
    )

    # All 4 handles end up under a bga_ball key.
    bga_keys = [k for k in new_out if "bga_ball" in k]
    bga_handles: set[str] = set()
    for k in bga_keys:
        for hl in new_out[k]:
            bga_handles.update(hl)
    assert bga_handles == {f"g{i}_{j}" for i in range(2) for j in range(2)}


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

"""Regression invariants for the CIRCLE template matching paths.

Captures the bug pattern `9d66024` introduced: when `build_entity_shapes`
gained the analytical CIRCLE fast path (`EntityShape.from_circle`),
drawing-side shapes started carrying `radius = float(r)` straight from the
DXF primitive — while library-stored templates still reload through
`EntityShape.from_points` which recomputes `radius = max(|pts - centroid|)`.
For real-world DXFs those two computations drifted enough that
`_match_single_circle`'s bucket lookup missed entire populations of
circles, breaking scan-all and Save Match JSON.

These tests lock the invariant: for any CIRCLE handle in any drawing, the
shape the matcher sees on the handle path and the shape the matcher sees
on the stored-template path MUST agree. If a future change introduces a
divergent path again, one of these tests should fail.

Why these tests, specifically:

- They don't depend on knowing the affected DXF's exact geometry — they
  test the algebraic invariant directly.
- They use small, fully-synthetic fixtures, but cover the coordinate
  regimes the affected file lives in (`>= 10⁴ mm` world coords, sub-mm
  radii, non-round radii like `0.22858`, radii that land on banker's
  rounding fence-posts).
- They cross-check the two code paths against each other rather than
  against a hand-computed expected value, so they survive future
  refactors that change BOTH paths identically while still catching any
  refactor that changes only one.
"""

from __future__ import annotations

import math

import pytest

from app.library import (
    Template,
    build_handle_index,
    collect_entity_kinds,
    collect_entity_points,
)
from app.matching import (
    EntityShape,
    _radius_bucket_cache,
    _radius_bucket_key,
    build_entity_shapes,
    find_matches,
    find_matches_from_pointsets,
)


# `_radius_bucket_cache` is keyed by `id(drawing)`, and Python may recycle
# memory between tests (a fresh `shapes` dict can land at the same id as a
# previous test's GC'd dict). Without this autouse fixture the second test
# in a row may hit a stale bucket map for an unrelated drawing — a
# test-ordering hazard that looks like a real parity break.
@pytest.fixture(autouse=True)
def _clear_radius_bucket_cache():
    _radius_bucket_cache.clear()
    yield
    _radius_bucket_cache.clear()


# ---- helpers --------------------------------------------------------------
def _circle_primitive(handle: str, cx: float, cy: float, r: float) -> dict:
    return {"type": "circle", "handle": handle, "center": [cx, cy], "r": r}


def _build(prims: list[dict]) -> tuple[dict[str, list[int]], dict[str, EntityShape]]:
    hi = build_handle_index(prims)
    return hi, build_entity_shapes(prims, hi)


def _template_for_handle(prims: list[dict], hi: dict[str, list[int]], handle: str) -> Template:
    """Mirror the `commit` endpoint's path: collect points + kinds for one
    handle, package as a Template."""
    pts = collect_entity_points(prims, hi, handle)
    kind = collect_entity_kinds(prims, hi, handle)
    return Template.from_entities(
        "TestClass", [pts], entity_kinds=[kind],
    )


def _match_handles_via_handle_path(prims: list[dict], handle: str) -> set[str]:
    _, shapes = _build(prims)
    out = find_matches([handle], shapes)
    return {m.handles[0] for m in out.matches}


def _match_handles_via_pointsets_path(prims: list[dict], handle: str) -> set[str]:
    hi, shapes = _build(prims)
    tmpl = _template_for_handle(prims, hi, handle)
    out = find_matches_from_pointsets(
        tmpl.entity_point_sets, shapes, entity_kinds=tmpl.entity_kinds,
    )
    # The pointsets path has skip=set() by design (the stored template isn't
    # bound to any drawing handle), so the source handle CAN appear in its
    # output. Drop it for parity with the handle path (which skips the
    # template's own handles).
    return {m.handles[0] for m in out.matches} - {handle}


# ---- parity (path equivalence) -------------------------------------------
# These tests assert ONLY that the two paths agree on which handles they
# return. They do NOT assert "and the result is non-empty" — capturing
# parity is the algebraic invariant 9d66024 broke, and asserting existence
# would conflate the parity bug with unrelated chamfer-fails-at-tight-
# packing issues that surface for some (radius, world-coord) combinations.
# Existence is tested separately below under "round-trip identity".
def test_parity_basic_single_radius_population():
    """Three same-radius circles at standard coords."""
    prims = [
        _circle_primitive("T", 0.0, 0.0, 0.5),
        _circle_primitive("A", 10.0, 0.0, 0.5),
        _circle_primitive("B", 0.0, 10.0, 0.5),
    ]
    assert _match_handles_via_handle_path(prims, "T") == \
           _match_handles_via_pointsets_path(prims, "T")


def test_parity_at_packaging_world_coordinates():
    """The affected DXF's world coords sit in the 10⁴-mm range. FP
    cancellation in the centroid-subtract step is most painful here."""
    cx, cy = 36639.0, 41311.0
    prims = [
        _circle_primitive("T", cx,        cy,        0.0375),
        _circle_primitive("A", cx + 0.4,  cy,        0.0375),
        _circle_primitive("B", cx,        cy + 0.4,  0.0375),
        _circle_primitive("C", cx + 0.8,  cy + 0.8,  0.0375),
    ]
    assert _match_handles_via_handle_path(prims, "T") == \
           _match_handles_via_pointsets_path(prims, "T")


def test_parity_non_round_radius():
    """`0.22858` is the recomputed BGABall radius the affected DXF
    surfaced — not exactly representable in float64."""
    r = 0.22858
    prims = [_circle_primitive(f"H{i}", float(i), 0.0, r) for i in range(5)]
    assert _match_handles_via_handle_path(prims, "H0") == \
           _match_handles_via_pointsets_path(prims, "H0")


def test_parity_radius_at_bankers_rounding_fence_post():
    """`r * 10⁴ == 412.5` — the banker's-rounding boundary that any
    ULP-scale drift can flip."""
    r = 0.04125  # 412.5 → banker's round → 412 (even)
    prims = [
        _circle_primitive("T", 0.0,  0.0, r),
        _circle_primitive("A", 1.0,  0.0, r),
        _circle_primitive("B", 0.0,  1.0, r),
    ]
    assert _match_handles_via_handle_path(prims, "T") == \
           _match_handles_via_pointsets_path(prims, "T")


def test_parity_radius_at_bankers_fence_post_with_large_coords():
    """Cross-product of the two known-painful regimes: fence-post
    radius + far-from-origin coordinates."""
    cx, cy = 36639.0, 41311.0
    r = 0.04125
    prims = [
        _circle_primitive("T", cx,       cy,       r),
        _circle_primitive("A", cx + 0.5, cy,       r),
        _circle_primitive("B", cx,       cy + 0.5, r),
        _circle_primitive("C", cx + 1.0, cy + 1.0, r),
    ]
    assert _match_handles_via_handle_path(prims, "T") == \
           _match_handles_via_pointsets_path(prims, "T")


def test_parity_sweep_across_radii_at_large_coords():
    """Sweep a range of radii at the affected coordinate scale. If any
    individual radius breaks parity (handle path finds matches that the
    pointsets path doesn't, or vice versa), this catches it."""
    cx, cy = 36639.0, 41311.0
    # Mix of round, non-round, fence-post, and BGA/C4-typical values.
    radii = [0.01, 0.04, 0.0375, 0.03908, 0.04125, 0.1, 0.225, 0.22858, 0.4]
    for r in radii:
        # Use a 10×r separation so chamfer (used on the no-fast-path side
        # of 9d66024) has clean isolation between candidates. Parity, not
        # absolute match count, is the contract under test.
        prims = [
            _circle_primitive("T", cx,        cy,        r),
            _circle_primitive("A", cx + 10*r, cy,        r),
            _circle_primitive("B", cx,        cy + 10*r, r),
        ]
        via_handle = _match_handles_via_handle_path(prims, "T")
        via_pts = _match_handles_via_pointsets_path(prims, "T")
        assert via_handle == via_pts, (
            f"path divergence at r={r!r}: "
            f"handle={via_handle} vs pointsets={via_pts}"
        )


# ---- shape radius identity (the deepest invariant) -----------------------
def test_shape_radius_bucket_within_one_basic():
    """The radius bucket key `build_entity_shapes` produces for a
    single-CIRCLE handle MUST agree with the bucket key `from_points`
    produces on the same handle's stored template points to within ±1
    bucket. Bit-identical radii are too strict (analytical r vs
    numerical max-norm always differ by ULPs), and so are exact bucket
    keys (banker's-rounding fence-posts can flip on ULP drift). The
    ±1 window is the contract `_match_single_circle`'s neighbour-bucket
    lookup actually relies on — any wider drift is the
    user-visible-bug regime."""
    prims = [_circle_primitive("H", 0.0, 0.0, 0.5)]
    hi = build_handle_index(prims)
    drawing_shape = build_entity_shapes(prims, hi)["H"]

    template_pts = collect_entity_points(prims, hi, "H")
    template_kind = collect_entity_kinds(prims, hi, "H")
    reloaded_shape = EntityShape.from_points("_t", template_pts, kind=template_kind)

    assert abs(
        _radius_bucket_key(drawing_shape.radius)
        - _radius_bucket_key(reloaded_shape.radius)
    ) <= 1


def test_shape_radius_bucket_within_one_at_large_coords():
    """Same ±1 bucket bound, with the coord regime that bit the affected
    DXF. Pre-`np.allclose` fix, `from_points` at large coords dropped
    its last vertex and inflated radius by 3–4 % (~30+ buckets); this
    test would fail by tens of buckets, well outside the ±1 window."""
    cx, cy = 36639.0, 41311.0
    for r in [0.01, 0.0375, 0.04125, 0.225, 0.22858, 0.4]:
        prims = [_circle_primitive("H", cx, cy, r)]
        hi = build_handle_index(prims)
        drawing_shape = build_entity_shapes(prims, hi)["H"]
        template_pts = collect_entity_points(prims, hi, "H")
        template_kind = collect_entity_kinds(prims, hi, "H")
        reloaded_shape = EntityShape.from_points("_t", template_pts, kind=template_kind)
        delta = abs(
            _radius_bucket_key(drawing_shape.radius)
            - _radius_bucket_key(reloaded_shape.radius)
        )
        assert delta <= 1, (
            f"bucket key drift at (cx={cx}, cy={cy}, r={r}): "
            f"drawing={drawing_shape.radius!r}(key={_radius_bucket_key(drawing_shape.radius)}), "
            f"reloaded={reloaded_shape.radius!r}(key={_radius_bucket_key(reloaded_shape.radius)}), "
            f"delta={delta} (must be ≤ 1)"
        )


def test_shape_kind_identity():
    """Both paths must agree on `.kind` for any CIRCLE handle. If
    drawing produces `kind="circle"` but stored template reloads as
    `kind=None` (or vice versa), `_match_single_circle` dispatch
    diverges."""
    cx, cy = 36639.0, 41311.0
    for r in [0.01, 0.0375, 0.04125, 0.225, 0.22858, 0.4]:
        prims = [_circle_primitive("H", cx, cy, r)]
        hi = build_handle_index(prims)
        drawing_shape = build_entity_shapes(prims, hi)["H"]
        template_pts = collect_entity_points(prims, hi, "H")
        template_kind = collect_entity_kinds(prims, hi, "H")
        reloaded_shape = EntityShape.from_points("_t", template_pts, kind=template_kind)
        assert drawing_shape.kind == reloaded_shape.kind == "circle"


# ---- round-trip identity -------------------------------------------------
def test_committed_template_finds_at_least_one_match_in_origin_drawing():
    """The minimal round-trip: commit a CIRCLE handle, run the
    pointsets path against the drawing it came from. The result MUST
    include at least one other same-radius circle. (Returning zero
    matches when the drawing demonstrably has them is the user-visible
    symptom of the bug.)"""
    cx, cy = 36639.0, 41311.0
    r = 0.22858  # the affected DXF's BGA-ish radius
    prims = [
        _circle_primitive("T", cx,       cy,       r),
        _circle_primitive("A", cx + 0.5, cy,       r),
        _circle_primitive("B", cx,       cy + 0.5, r),
    ]
    hi, shapes = _build(prims)
    tmpl = _template_for_handle(prims, hi, "T")
    out = find_matches_from_pointsets(
        tmpl.entity_point_sets, shapes, entity_kinds=tmpl.entity_kinds,
    )
    matched = {m.handles[0] for m in out.matches}
    # Must find at least the two siblings (and possibly T itself, since
    # the pointsets path doesn't skip the source handle).
    assert {"A", "B"}.issubset(matched), (
        f"round-trip lost handles: matched={matched}"
    )


def test_committed_template_finds_high_population_at_packaging_coords():
    """The pathological volume case: a grid of same-radius circles at
    far-from-origin coords. Mirrors the affected DXF's BGA-grid
    population pattern."""
    cx, cy = 36639.0, 41311.0
    r = 0.22858
    pitch = 0.5
    # 10×10 grid → 100 circles
    prims = []
    for i in range(10):
        for j in range(10):
            prims.append(_circle_primitive(
                f"G_{i}_{j}", cx + i * pitch, cy + j * pitch, r,
            ))
    hi, shapes = _build(prims)
    tmpl = _template_for_handle(prims, hi, "G_0_0")
    out = find_matches_from_pointsets(
        tmpl.entity_point_sets, shapes, entity_kinds=tmpl.entity_kinds,
    )
    matched = {m.handles[0] for m in out.matches}
    # All 100 circles share the radius bucket. The pointsets path's
    # skip=set() means even G_0_0 itself comes back, so we expect 100.
    assert len(matched) == 100, (
        f"high-population scan returned {len(matched)} of 100 expected"
    )


# ---- sweep regressions ---------------------------------------------------
@pytest.mark.parametrize("cx,cy", [
    (0.0, 0.0),
    (1000.0, 1000.0),
    (36639.0, 41311.0),     # the affected DXF's regime
    (100000.0, 100000.0),
])
@pytest.mark.parametrize("r", [
    0.01, 0.04, 0.0375, 0.03908, 0.04125, 0.1, 0.225, 0.22858, 0.4,
])
def test_parity_grid(cx: float, cy: float, r: float):
    """Combined sweep over coord regimes × radii. Each combination
    spawned by parametrize becomes an independent test, so the failure
    report names the exact (cx, cy, r) that breaks parity."""
    prims = [
        _circle_primitive("T", cx,        cy,        r),
        _circle_primitive("A", cx + 10*r, cy,        r),
    ]
    via_handle = _match_handles_via_handle_path(prims, "T")
    via_pts = _match_handles_via_pointsets_path(prims, "T")
    assert via_handle == via_pts, (
        f"parity broken at (cx={cx}, cy={cy}, r={r}): "
        f"handle={via_handle} vs pointsets={via_pts}"
    )

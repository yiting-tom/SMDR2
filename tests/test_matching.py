"""Matcher correctness: transform invariants, single + multi-entity, near-miss."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.matching import (
    EntityShape,
    NearMiss,
    align_score,
    find_matches,
    find_matches_from_pointsets,
    signatures_compatible,
)


# ---- helpers --------------------------------------------------------------
def shape(handle, points):
    return EntityShape.from_points(handle, [tuple(p) for p in points])


def rotate(pts, angle_deg):
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    arr = np.asarray(pts)
    R = np.array([[c, -s], [s, c]])
    return (arr @ R.T).tolist()


def translate(pts, dx, dy):
    return [(p[0] + dx, p[1] + dy) for p in pts]


def scale(pts, s):
    return [(p[0] * s, p[1] * s) for p in pts]


def mirror_x(pts):
    return [(-p[0], p[1]) for p in pts]


SQUARE = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
RECT   = [(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0), (0.0, 0.0)]


# ---- signature pre-filter -------------------------------------------------
def test_signature_compatible_when_identical():
    a = shape("a", RECT)
    b = shape("b", RECT)
    assert signatures_compatible(a, b)


def test_signature_rejects_very_different_vcount():
    a = shape("a", SQUARE)            # 5 verts
    b = shape("b", [(0, 0), (1, 1)])   # 2 verts
    assert not signatures_compatible(a, b)


# ---- alignment ------------------------------------------------------------
def test_align_identical_shapes_scores_zero():
    res = align_score(np.asarray(RECT), np.asarray(RECT))
    assert res is not None
    score, scale = res
    assert score < 1e-6
    assert abs(scale - 1.0) < 1e-6


def test_align_translated_copy():
    moved = translate(RECT, 50.0, -7.5)
    res = align_score(np.asarray(RECT), np.asarray(moved))
    assert res is not None
    score, _ = res
    assert score < 1e-6


# Transform-noise floor — the matcher now resamples to RESAMPLE_N points
# along arclength before scoring, so a rotated / mirrored / scaled cloud's
# sample positions are phase-shifted relative to the template by up to half
# the sample spacing. The chamfer floor is bounded by that, not by ULP. The
# matcher's actual acceptance threshold (TOLERANCE_ABS = 0.05) is unchanged
# and these tests still assert "comfortably below" it.
TRANSFORM_NOISE_FLOOR = 0.05


@pytest.mark.parametrize("angle", [30.0, 90.0, 137.0, 270.0])
def test_align_rotated_rect(angle):
    rotated = rotate(RECT, angle)
    res = align_score(np.asarray(RECT), np.asarray(rotated))
    assert res is not None
    score, _ = res
    assert score < TRANSFORM_NOISE_FLOOR, f"rotation by {angle}° should still match"


def test_align_mirrored_copy():
    mirrored = mirror_x(RECT)
    res = align_score(np.asarray(RECT), np.asarray(mirrored))
    assert res is not None
    score, _ = res
    assert score < TRANSFORM_NOISE_FLOOR


def test_align_within_scale_tolerance():
    scaled = scale(RECT, 1.04)  # within 0.95~1.05
    res = align_score(np.asarray(RECT), np.asarray(scaled))
    assert res is not None
    score, sc = res
    assert score < TRANSFORM_NOISE_FLOOR
    assert 0.95 <= sc <= 1.05


def test_align_outside_scale_tolerance_returns_none():
    scaled = scale(RECT, 1.5)
    res = align_score(np.asarray(RECT), np.asarray(scaled))
    assert res is None  # scale check fails → caller treats as near-miss


# ---- single-entity find_matches ------------------------------------------
def test_find_matches_single_finds_translated_copies():
    drawing = {
        "t": shape("t", RECT),
        "a": shape("a", translate(RECT, 10, 0)),
        "b": shape("b", translate(RECT, 0, 10)),
        "c": shape("c", translate(RECT, -20, 5)),
    }
    out = find_matches(["t"], drawing)
    matched = {m.handles[0] for m in out.matches}
    assert matched == {"a", "b", "c"}


def test_find_matches_single_rejects_different_shape():
    drawing = {
        "t":     shape("t", RECT),                       # template
        "other": shape("other", [(0, 0), (5, 0), (5, 4), (0, 4), (0, 0)]),  # different aspect
    }
    out = find_matches(["t"], drawing)
    # No matches — but the differently-shaped rect may be flagged as a near-miss.
    assert not out.matches


def test_find_matches_excludes_template_handle():
    drawing = {
        "t": shape("t", RECT),
        "a": shape("a", translate(RECT, 10, 0)),
    }
    out = find_matches(["t"], drawing)
    assert "t" not in {m.handles[0] for m in out.matches}


# ---- density-invariant matching ------------------------------------------
def _sample_closed_polygon(corners, n_per_side):
    """Build a closed polyline by walking the corners and inserting
    `n_per_side` additional points along each edge. Returns the list of
    points (with closing duplicate appended)."""
    pts = []
    m = len(corners)
    for i in range(m):
        a = corners[i]
        b = corners[(i + 1) % m]
        for k in range(n_per_side + 1):  # corner + interior samples
            f = k / (n_per_side + 1)
            pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
    pts.append(corners[0])  # closing duplicate
    return pts


def test_find_matches_low_vs_high_vertex_count_same_shape():
    # Same closed 2×1 rectangle, drawn two ways: 5-vertex (corners + close)
    # and 41-vertex (corners + 9 interior samples per side + close).
    corners = [(0, 0), (2, 0), (2, 1), (0, 1)]
    sparse = _sample_closed_polygon(corners, n_per_side=0)   # 5 verts
    dense = _sample_closed_polygon(corners, n_per_side=9)    # 41 verts
    assert len(sparse) == 5 and len(dense) == 41
    drawing = {
        "sparse_template": shape("sparse_template", sparse),
        "dense_copy":      shape("dense_copy", translate(dense, 10, 0)),
    }
    out = find_matches(["sparse_template"], drawing)
    assert {m.handles[0] for m in out.matches} == {"dense_copy"}


def test_find_matches_density_invariant_under_mirror():
    # Mirrored copy with a very different vertex count — the substrate
    # failure case the change was built for.
    corners = [(0, 0), (2, 0), (2, 1), (0, 1)]
    sparse = _sample_closed_polygon(corners, n_per_side=0)   # 5 verts
    dense_mirrored = mirror_x(_sample_closed_polygon(corners, n_per_side=15))  # 65 verts
    assert len(sparse) == 5 and len(dense_mirrored) == 65
    drawing = {
        "sparse_template": shape("sparse_template", sparse),
        "dense_mirror":    shape("dense_mirror", dense_mirrored),
    }
    out = find_matches(["sparse_template"], drawing)
    assert {m.handles[0] for m in out.matches} == {"dense_mirror"}


def test_find_matches_same_perimeter_different_shape_rejected():
    # Path length matches the rectangle's (perimeter 6) but the shape is
    # genuinely different — a 1.5×1.5 square. Density-invariant resampling
    # must NOT collapse these into the same match, AND the rejection must
    # happen at the signature stage so no chamfer is computed.
    rect_perimeter_6 = [(0, 0), (2, 0), (2, 1), (0, 1), (0, 0)]
    square_15 = [(0, 0), (1.5, 0), (1.5, 1.5), (0, 1.5), (0, 0)]
    rect = shape("rect", rect_perimeter_6)
    square = shape("square", translate(square_15, 10, 0))
    # Signature gate must reject; chamfer never runs.
    assert not signatures_compatible(rect, square)
    out = find_matches(["rect"], {"rect": rect, "square": square})
    assert not out.matches


def test_find_matches_line_segment_still_works():
    # 2-vertex degenerate-low input — must still match a translated copy.
    line = [(0.0, 0.0), (2.0, 0.0)]
    drawing = {
        "t": shape("t", line),
        "a": shape("a", translate(line, 5, 0)),
        "b": shape("b", translate(line, 0, 5)),
    }
    out = find_matches(["t"], drawing)
    assert {m.handles[0] for m in out.matches} == {"a", "b"}


# ---- multi-entity find_matches -------------------------------------------
def test_find_matches_multi_triangle():
    """Template = 3 connected line entities forming a triangle.
    Same arrangement elsewhere should match."""
    template_entities = {
        "L1": shape("L1", [(0, 0), (1, 0)]),
        "L2": shape("L2", [(1, 0), (0.5, 1)]),
        "L3": shape("L3", [(0.5, 1), (0, 0)]),
        # Same triangle translated by (10, 10):
        "L4": shape("L4", [(10, 10), (11, 10)]),
        "L5": shape("L5", [(11, 10), (10.5, 11)]),
        "L6": shape("L6", [(10.5, 11), (10, 10)]),
    }
    out = find_matches(["L1", "L2", "L3"], template_entities)
    assert len(out.matches) == 1
    match_handles = set(out.matches[0].handles)
    assert match_handles == {"L4", "L5", "L6"}


def test_find_matches_multi_ignores_unrelated_lines():
    """Adding far-away lines that don't form the template's shape shouldn't
    produce spurious matches."""
    entities = {
        # Template triangle:
        "L1": shape("L1", [(0, 0), (1, 0)]),
        "L2": shape("L2", [(1, 0), (0.5, 1)]),
        "L3": shape("L3", [(0.5, 1), (0, 0)]),
        # Lone, isolated lines elsewhere:
        "X1": shape("X1", [(100, 100), (101, 100)]),
        "X2": shape("X2", [(200, 200), (200, 201)]),
    }
    out = find_matches(["L1", "L2", "L3"], entities)
    assert len(out.matches) == 0  # no second triangle


# ---- pointsets entry point ------------------------------------------------
def test_find_matches_from_pointsets_equivalent_to_handle_path():
    drawing = {
        "a": shape("a", translate(RECT, 10, 0)),
        "b": shape("b", translate(RECT, 0, 10)),
    }
    out = find_matches_from_pointsets([RECT], drawing)
    matched = {m.handles[0] for m in out.matches}
    assert matched == {"a", "b"}


# ---- n_jobs equivalence ---------------------------------------------------
def test_n_jobs_does_not_change_result(test_dxf_path):
    """Running with n_jobs > 1 must yield the same matches as n_jobs=1."""
    from app.dxf import flatten_for_render
    from app.library import build_handle_index
    from app.matching import build_entity_shapes, shutdown_pool

    out = flatten_for_render(str(test_dxf_path))
    hi = build_handle_index(out.primitives)
    shapes = build_entity_shapes(out.primitives, hi)
    # Pick the most-common vertex-count bucket (the BGA ball circle in
    # test.dxf) — the exact count depends on whether closed polylines'
    # trailing-equals-first vertex was dropped.
    from collections import Counter
    common_vc = Counter(s.vertex_count for s in shapes.values()).most_common(1)[0][0]
    seed = next(h for h, s in shapes.items() if s.vertex_count == common_vc)

    serial = find_matches([seed], shapes, n_jobs=1)
    parallel = find_matches([seed], shapes, n_jobs=2)
    shutdown_pool()

    assert {m.handles[0] for m in serial.matches} == {m.handles[0] for m in parallel.matches}


# ---- signature pre-filter: rotation-invariant gates ----------------------
def test_signature_rejects_same_perimeter_rect_vs_square():
    # 2×1 rectangle (perimeter 6) vs 1.5×1.5 square (perimeter 6).
    # Path-length gate passes; the σ-ratio gate rejects (~0.5 vs ~1.0).
    rect = shape("rect", [(0, 0), (2, 0), (2, 1), (0, 1), (0, 0)])
    square = shape("square", translate(
        [(0, 0), (1.5, 0), (1.5, 1.5), (0, 1.5), (0, 0)], 10, 0))
    assert not signatures_compatible(rect, square)


def test_signature_rejects_thin_line_vs_thick_blob_same_perimeter():
    # Long thin line (σ-ratio ≈ 0) vs near-square polyline of comparable
    # path length (σ-ratio ≈ 1). σ-ratio gate must reject.
    thin_line = shape("line", [(0, 0), (3, 0)])  # path length 3
    # Square with perimeter 3 (side 0.75), σ-ratio ≈ 1.
    blob = shape("blob", translate(
        [(0, 0), (0.75, 0), (0.75, 0.75), (0, 0.75), (0, 0)], 50, 50))
    assert not signatures_compatible(thin_line, blob)


def test_signature_tolerates_sigma_ratio_within_threshold():
    # Two rectangles with slightly different aspect ratios — σ-ratios
    # differ by less than SIGMA_RATIO_TOL (0.15), and radius / path-length
    # are also within their gates → signature passes.
    rect_2_1 = shape("a", [(0, 0), (2, 0), (2, 1), (0, 1), (0, 0)])
    rect_1p9_1p1 = shape("b", translate(
        [(0, 0), (1.9, 0), (1.9, 1.1), (0, 1.1), (0, 0)], 50, 50))
    assert signatures_compatible(rect_2_1, rect_1p9_1p1)


@pytest.mark.parametrize("angle", [17.0, 45.0, 113.0, 271.0])
def test_signature_invariant_under_rotation(angle):
    # The same shape rotated by any angle must produce the same radius
    # and σ-ratio (within numerical noise), and pass the signature gate.
    base = shape("base", RECT)
    rotated = shape("rot", rotate(RECT, angle))
    assert abs(base.radius - rotated.radius) < 1e-9
    from app.matching import _sigma_ratio
    assert abs(_sigma_ratio(base) - _sigma_ratio(rotated)) < 1e-9
    assert signatures_compatible(base, rotated)


def test_pca_singular_values_degenerate_inputs():
    # < 2 rows → both σ are 0; coincident points → both σ are 0.
    empty = shape("e", [])
    assert empty.pca_sigma1 == 0.0 and empty.pca_sigma2 == 0.0
    single = shape("s", [(1.0, 2.0)])
    assert single.pca_sigma1 == 0.0 and single.pca_sigma2 == 0.0
    coincident = shape("c", [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0)])
    assert coincident.pca_sigma1 == 0.0 and coincident.pca_sigma2 == 0.0


def test_pca_singular_values_ordered():
    # σ₁ ≥ σ₂ always.
    s = shape("s", RECT)
    assert s.pca_sigma1 >= s.pca_sigma2 >= 0.0


# ---- tolerance override --------------------------------------------------
def test_tolerance_override_promotes_near_miss_to_match():
    """A candidate whose chamfer falls between the global default and a
    loosened tolerance must be classified differently by each call. Verifies
    the per-class tolerance plumb-through at the matcher level."""
    template = RECT
    # Substrate-style scale: ~25 mm wide so a few mm of deformation is
    # realistic, well outside the 0.05 mm BGA-ball default but inside a
    # 0.5 mm substrate override.
    template_big = [(0, 0), (25, 0), (25, 12), (0, 12), (0, 0)]
    # Stretch the y-axis slightly so chamfer scales nicely (≈0.2 mm).
    candidate_big = translate(
        [(0, 0), (25.0, 0), (25.0, 12.4), (0, 12.4), (0, 0)],
        100, 0,
    )
    drawing = {
        "t": shape("t", template_big),
        "c": shape("c", candidate_big),
    }
    # First: confirm the candidate falls into near-miss at the strict default.
    # (`find_matches_from_pointsets` also returns "t" as a match because the
    # template pointset matches the drawing's "t" entity exactly — that's
    # fine, we only care about "c"'s classification flipping.)
    strict = find_matches_from_pointsets(
        [template_big], drawing, tolerance=0.05,
    )
    near_handles_strict = {n.handles[0] for n in strict.near_misses}
    matched_handles_strict = {m.handles[0] for m in strict.matches}
    assert "c" in near_handles_strict
    assert "c" not in matched_handles_strict
    # Same candidate, same data, loosened tolerance → flips to match.
    loose = find_matches_from_pointsets(
        [template_big], drawing, tolerance=0.5,
    )
    matched_handles_loose = {m.handles[0] for m in loose.matches}
    assert "c" in matched_handles_loose
    assert not any(n.handles[0] == "c" for n in loose.near_misses)

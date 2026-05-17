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
    # must NOT collapse these into the same match.
    rect_perimeter_6 = [(0, 0), (2, 0), (2, 1), (0, 1), (0, 0)]
    square_15 = [(0, 0), (1.5, 0), (1.5, 1.5), (0, 1.5), (0, 0)]
    drawing = {
        "rect":   shape("rect", rect_perimeter_6),
        "square": shape("square", translate(square_15, 10, 0)),
    }
    out = find_matches(["rect"], drawing)
    assert not out.matches  # different shape, no match


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

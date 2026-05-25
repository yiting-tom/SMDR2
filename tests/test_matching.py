"""Matcher correctness: transform invariants, single + multi-entity, near-miss."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.matching import (
    EntityShape,
    NearMiss,
    align_score,
    diagnose_swap,
    find_matches,
    find_matches_from_pointsets,
    signatures_compatible,
)
from app.matching import _get_fingerprint_buckets  # private — bucket cache contract


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


# ---- signature-mode matching (per-class strategy) -----------------------
# Substrate-style: same bbox + path length + aspect, very different vertex
# distributions. Chamfer flunks this (the 11-vert and 7-vert polylines hit
# different arclength positions after resample); signature mode accepts it.
SUBSTRATE_11 = [
    (0.0, 0.0), (5.0, 0.0), (12.5, 0.0), (20.0, 0.0), (25.0, 0.0),
    (25.0, 6.0), (25.0, 12.0), (15.0, 12.0), (5.0, 12.0),
    (0.0, 12.0), (0.0, 6.0), (0.0, 0.0),
]
SUBSTRATE_7 = [
    (0.0, 0.0), (12.5, 0.0), (25.0, 0.0),
    (25.0, 12.0), (12.5, 12.0), (0.0, 12.0), (0.0, 0.0),
]


def test_signature_mode_matches_same_bbox_different_vertex_count():
    """Two 25×12 substrates with same bbox / path / aspect but different
    vertex layouts. Signature mode must match them and emit zero
    near-misses (match-or-nothing semantics)."""
    template = SUBSTRATE_11
    candidate = translate(SUBSTRATE_7, 100, 0)  # different location, same shape
    drawing = {
        "t": shape("t", template),
        "c": shape("c", candidate),
    }
    sig = find_matches_from_pointsets(
        [template], drawing,
        strategy="signature", bbox_ratio=0.05,
    )
    matched = {m.handles[0] for m in sig.matches}
    assert "c" in matched
    assert not sig.near_misses, "signature mode emits match-or-nothing"


def test_signature_mode_matches_where_chamfer_fails():
    """The motivating case: a near-identical substrate whose chamfer
    score lands above the global tolerance (so chamfer mode parks it in
    near-misses), but whose bbox + path + aspect agree to well within
    5 %. Signature mode is the entire reason this change exists — it
    must match the pair here."""
    # Template: 25×12 rect.
    template = [(0.0, 0.0), (25.0, 0.0), (25.0, 12.0), (0.0, 12.0), (0.0, 0.0)]
    # Candidate: 25×12.4 rect — y-extent stretched by 3.3 %, well within
    # 5 % bbox_ratio and the global σ-ratio tolerance. The chamfer
    # pipeline computes a non-zero point-to-point distance (~0.19 mm in
    # this configuration) that exceeds `TOLERANCE_ABS = 0.05`, so chamfer
    # mode lands the candidate in near-misses.
    candidate = translate(
        [(0.0, 0.0), (25.0, 0.0), (25.0, 12.4), (0.0, 12.4), (0.0, 0.0)],
        100, 0,
    )
    drawing = {
        "t": shape("t", template),
        "c": shape("c", candidate),
    }
    sig = find_matches_from_pointsets(
        [template], drawing,
        strategy="signature", bbox_ratio=0.05,
    )
    assert "c" in {m.handles[0] for m in sig.matches}
    # And chamfer mode does NOT match this candidate.
    chf = find_matches_from_pointsets([template], drawing)
    chf_handles = {m.handles[0] for m in chf.matches}
    assert "c" not in chf_handles
    near = {n.handles[0] for n in chf.near_misses}
    assert "c" in near


def test_signature_mode_rejects_wrong_sized_candidate():
    """A 15% larger candidate must NOT match under bbox_ratio=0.05 and must
    NOT appear in near_misses (signature mode emits match-or-nothing)."""
    template = SUBSTRATE_11
    larger = translate(scale(SUBSTRATE_7, 1.15), 100, 0)
    drawing = {
        "t": shape("t", template),
        "big": shape("big", larger),
    }
    out = find_matches_from_pointsets(
        [template], drawing,
        strategy="signature", bbox_ratio=0.05,
    )
    handles = {m.handles[0] for m in out.matches}
    assert "big" not in handles
    assert not out.near_misses


@pytest.mark.parametrize("angle", [30, 45, 137, 270])
def test_signature_mode_accepts_rotation(angle):
    template = SUBSTRATE_11
    rotated = translate(rotate(SUBSTRATE_7, angle), 100, 0)
    drawing = {
        "t": shape("t", template),
        "r": shape("r", rotated),
    }
    out = find_matches_from_pointsets(
        [template], drawing,
        strategy="signature", bbox_ratio=0.05,
    )
    assert "r" in {m.handles[0] for m in out.matches}


def test_signature_mode_accepts_mirror():
    template = SUBSTRATE_11
    mirrored = translate(mirror_x(SUBSTRATE_7), 100, 0)
    drawing = {
        "t": shape("t", template),
        "m": shape("m", mirrored),
    }
    out = find_matches_from_pointsets(
        [template], drawing,
        strategy="signature", bbox_ratio=0.05,
    )
    assert "m" in {m.handles[0] for m in out.matches}


def test_signature_mode_multi_entity_template_falls_back_to_chamfer():
    """When a multi-entity template is passed with strategy='signature',
    the matcher SHALL run the chamfer pipeline (single-entity
    short-circuit is irrelevant here). The behavior must equal what the
    same call produces under strategy='chamfer'."""
    pointsets = [RECT, translate(RECT, 5, 0)]  # two entities = multi
    drawing = {
        "a": shape("a", RECT),
        "b": shape("b", translate(RECT, 5, 0)),
    }
    sig = find_matches_from_pointsets(pointsets, drawing, strategy="signature")
    chf = find_matches_from_pointsets(pointsets, drawing, strategy="chamfer")
    # Same matches under either flag — signature was silently bypassed.
    assert sorted(m.handles for m in sig.matches) == sorted(m.handles for m in chf.matches)


def test_default_strategy_is_chamfer():
    """Calling without a strategy kwarg keeps every existing behavior."""
    drawing = {"t": shape("t", RECT), "a": shape("a", translate(RECT, 10, 0))}
    out = find_matches(["t"], drawing)
    assert {m.handles[0] for m in out.matches} == {"a"}


# ---- diagnose_swap (instrumentation for /match-swap) ---------------------
def _pad(handle, cx, cy, w=1.0, h=0.5):
    return shape(handle, [
        (cx - w/2, cy - h/2), (cx + w/2, cy - h/2),
        (cx + w/2, cy + h/2), (cx - w/2, cy + h/2),
        (cx - w/2, cy - h/2),
    ])


def test_diagnose_swap_bit_identical_multi_pattern_is_symmetric():
    """Two bit-identical 4-pad patterns translated apart must come out
    symmetric: both directions find the opposing pattern, every per-pair
    gate passes both ways, and per-pair forward and reverse path-length
    ratios are reciprocals."""
    # Pattern A at origin, Pattern B translated by (50, 0).
    a_pads = [_pad(f"A{i}", *pos) for i, pos in enumerate(
        [(0, 0), (3, 0), (0, 2), (3, 2)]
    )]
    b_pads = [_pad(f"B{i}", 50 + cx, cy) for i, (cx, cy) in enumerate(
        [(0, 0), (3, 0), (0, 2), (3, 2)]
    )]
    drawing = {s.handle: s for s in a_pads + b_pads}
    res = diagnose_swap(
        [p.handle for p in a_pads], [p.handle for p in b_pads], drawing,
    )
    assert res["asymmetric"]["a_template_finds_b"]
    assert res["asymmetric"]["b_template_finds_a"]
    for pair in res["pairs"]:
        assert pair["forward"]["gates"]["compatible"]
        assert pair["reverse"]["gates"]["compatible"]
        fwd = pair["forward"]["gates"]["path_length_ratio"]
        rev = pair["reverse"]["gates"]["path_length_ratio"]
        assert fwd is not None and rev is not None
        assert abs(fwd * rev - 1.0) < 1e-9


def test_diagnose_swap_pair_count_is_cartesian_product():
    """O(|A|·|B|) pair-wise dump. Documents the size budget so users grep'ing
    a large response know what to expect."""
    a_pads = [_pad(f"A{i}", i * 3.0, 0) for i in range(3)]
    b_pads = [_pad(f"B{i}", 50 + i * 3.0, 0) for i in range(4)]
    drawing = {s.handle: s for s in a_pads + b_pads}
    res = diagnose_swap(
        [p.handle for p in a_pads], [p.handle for p in b_pads], drawing,
    )
    assert len(res["pairs"]) == 3 * 4


def test_diagnose_swap_multi_trace_present_only_when_both_multi():
    """multi_trace is the seed/pose dump; single-entity templates use a
    different code path (single-serial), so omit the multi trace then."""
    a_pads = [_pad("A0", 0, 0), _pad("A1", 3, 0)]
    b_pads = [_pad("B0", 50, 0), _pad("B1", 53, 0)]
    drawing = {s.handle: s for s in a_pads + b_pads}

    multi = diagnose_swap(
        ["A0", "A1"], ["B0", "B1"], drawing,
    )
    assert "a_template" in multi["multi_trace"]
    assert "b_template" in multi["multi_trace"]

    single = diagnose_swap(["A0"], ["B0"], drawing)
    assert single["multi_trace"] == {}


# ---- _match_multi reference outputs --------------------------------------
# Rigid-transform / fingerprint-bucket matcher contract: matches use
# `score == 0.0` exactly (chamfer is gone from the multi path) and
# `scale == 1.0` exactly (no scale search). Handle sets are pinned per
# scenario.
def test_match_multi_triangle_parity():
    drawing = {
        "L1": shape("L1", [(0, 0), (1, 0)]),
        "L2": shape("L2", [(1, 0), (0.5, 1)]),
        "L3": shape("L3", [(0.5, 1), (0, 0)]),
        "L4": shape("L4", [(10, 10), (11, 10)]),
        "L5": shape("L5", [(11, 10), (10.5, 11)]),
        "L6": shape("L6", [(10.5, 11), (10, 10)]),
    }
    out = find_matches(["L1", "L2", "L3"], drawing)
    assert len(out.matches) == 1
    m = out.matches[0]
    assert sorted(m.handles) == ["L4", "L5", "L6"]
    assert m.scale == 1.0
    assert m.score == 0.0


def test_match_multi_four_pad_smd_parity():
    a_pads = [_pad(f"A{i}", cx, cy) for i, (cx, cy) in enumerate(
        [(0, 0), (3, 0), (0, 2), (3, 2)]
    )]
    b_pads = [_pad(f"B{i}", 50 + cx, cy) for i, (cx, cy) in enumerate(
        [(0, 0), (3, 0), (0, 2), (3, 2)]
    )]
    drawing = {s.handle: s for s in a_pads + b_pads}
    out = find_matches([p.handle for p in a_pads], drawing)
    assert len(out.matches) == 1
    m = out.matches[0]
    assert sorted(m.handles) == ["B0", "B1", "B2", "B3"]
    assert m.scale == 1.0
    assert m.score == 0.0


def test_match_multi_dense_neighbours_parity():
    """Three 3-pad rows placed close enough that their bbox radii overlap.
    Each non-template row must surface as exactly one match, with no
    handle absorbed into another match group."""
    drawing = {}
    for prefix, ox in [("T", 0), ("U", 10), ("V", 20)]:
        for i, cx in enumerate([0, 2, 4]):
            s = _pad(f"{prefix}{i}", ox + cx, 0)
            drawing[s.handle] = s
    out = find_matches(["T0", "T1", "T2"], drawing)
    groups = sorted(tuple(sorted(m.handles)) for m in out.matches)
    assert groups == [("U0", "U1", "U2"), ("V0", "V1", "V2")]
    for m in out.matches:
        assert m.scale == 1.0
        assert m.score == 0.0


def test_match_multi_mirrored_pattern_matches():
    """A multi-entity template mirrored across the y-axis SHALL match via
    one of the four PCA sign variants. Use an asymmetric pad layout so
    mirrored ≠ rotated and the sign variant has to actually do work."""
    # Template = 3 pads in an L: (0,0), (3,0), (0,2)
    t_pads = [_pad(f"T{i}", cx, cy) for i, (cx, cy) in enumerate(
        [(0, 0), (3, 0), (0, 2)]
    )]
    # Mirror across the y-axis → (-cx, cy), translated far away.
    m_pads = [_pad(f"M{i}", 100 - cx, cy) for i, (cx, cy) in enumerate(
        [(0, 0), (3, 0), (0, 2)]
    )]
    drawing = {s.handle: s for s in t_pads + m_pads}
    out = find_matches([p.handle for p in t_pads], drawing)
    assert len(out.matches) == 1
    assert sorted(out.matches[0].handles) == ["M0", "M1", "M2"]


def test_match_multi_reports_scale_exactly_one():
    """Spec: every multi-entity match SHALL carry `scale = 1.0` exactly."""
    a_pads = [_pad(f"A{i}", cx, cy) for i, (cx, cy) in enumerate(
        [(0, 0), (3, 0), (0, 2)]
    )]
    b_pads = [_pad(f"B{i}", 50 + cx, cy) for i, (cx, cy) in enumerate(
        [(0, 0), (3, 0), (0, 2)]
    )]
    drawing = {s.handle: s for s in a_pads + b_pads}
    out = find_matches([p.handle for p in a_pads], drawing)
    assert len(out.matches) >= 1
    for m in out.matches:
        assert m.scale == 1.0  # exact, not approximate
        assert m.score == 0.0


@pytest.mark.parametrize("r", [0.05, 0.5, 1.0, 5.0, 50.0])
def test_from_circle_matches_from_points_reference(r):
    """Analytical CIRCLE fast-path (`EntityShape.from_circle`) must
    produce values numerically equivalent to the reference
    `from_points`-on-synthesised-samples path that `build_entity_shapes`
    used for circles pre-optimisation. Locks in the contract that
    `build_entity_shapes`'s CIRCLE short-circuit is observably
    indistinguishable from the prior generic path."""
    import math
    cx, cy = 10.0, -3.0
    # Reference: same sample sequence collect_entity_points uses.
    n_ref = max(8, min(64, round(2.0 * math.pi * r / 0.01)))
    sampled = [
        (cx + r * math.cos(2 * math.pi * i / n_ref),
         cy + r * math.sin(2 * math.pi * i / n_ref))
        for i in range(n_ref)
    ]
    ref = EntityShape.from_points("R", sampled, kind="circle")
    ana = EntityShape.from_circle("R", cx, cy, r)
    assert ana.kind == "circle"
    assert ana.vertex_count == ref.vertex_count
    assert abs(ana.radius - ref.radius) < 1e-12
    assert abs(ana.path_length - ref.path_length) < 1e-12
    assert abs(ana.pca_sigma1 - ref.pca_sigma1) < 1e-12
    assert abs(ana.pca_sigma2 - ref.pca_sigma2) < 1e-12
    # Centroid is analytically exact (cx, cy); from_points hits FP noise
    # in the mean. Compare with a small tolerance.
    assert abs(ana.centroid[0] - ref.centroid[0]) < 1e-12
    assert abs(ana.centroid[1] - ref.centroid[1]) < 1e-12
    assert ana.points.shape == ref.points.shape


def test_fingerprint_bucket_skips_circles():
    """Spec hint: multi-entity templates never include CIRCLE entities.
    The bucket cache SHALL exclude them so they don't bloat the seed
    enumeration space (and so circles' all-zero fingerprint doesn't
    collide with degenerate non-circle entities)."""
    from app.matching import _get_fingerprint_buckets, _fingerprint_bucket_cache
    _fingerprint_bucket_cache.clear()
    pad = _pad("P", 0, 0)
    circle = EntityShape.from_circle("C", 5, 5, 1.0)
    drawing = {"P": pad, "C": circle}
    buckets = _get_fingerprint_buckets(drawing)
    # Pad's fingerprint bucket contains only the pad's handle.
    all_handles = {h for hs in buckets.values() for h in hs}
    assert all_handles == {"P"}
    assert "C" not in all_handles


def test_fingerprint_bucket_cache_reuses_per_drawing_identity():
    """Spec: cache is keyed by drawing dict identity. Same dict → same
    bucket object; fresh dict → fresh bucket object."""
    drawing = {s.handle: s for s in (
        _pad("A", 0, 0), _pad("B", 5, 0), _pad("C", 0, 5),
    )}
    first = _get_fingerprint_buckets(drawing)
    second = _get_fingerprint_buckets(drawing)
    assert first is second, "same drawing dict must reuse the same bucket object"

    # Rebuild as a new dict object — same content but different identity.
    rebuilt = dict(drawing)
    assert rebuilt is not drawing
    fresh = _get_fingerprint_buckets(rebuilt)
    assert fresh is not first, "fresh drawing dict must build a new bucket object"


@pytest.mark.parametrize("seed", list(range(20)))
def test_match_multi_symmetry_property(seed):
    """Property: for any pair of bit-identical multi-entity patterns L and R
    in the same drawing, `find_matches([L_handles], drawing)` SHALL return
    exactly R's handles AND `find_matches([R_handles], drawing)` SHALL
    return exactly L's handles. The two match-handle sets MUST be each
    other's complement.

    Locks in the structural symmetry of the rigid-transform matcher.
    Every comparison in the multi-match pipeline (fingerprint tuple
    equality, centroid KDTree nearest-neighbor) is commutative, so the
    "L finds R but R doesn't find L" asymmetry the old chamfer matcher
    suffered from is impossible by construction. This test runs 20
    random pattern shapes / sizes / counts to catch any regression that
    would reintroduce an asymmetric comparison.
    """
    rng = np.random.RandomState(seed)
    n_entities = int(rng.randint(2, 6))
    # Random non-square rectangles at random positions. Non-square ensures
    # PCA σ-ratio < 1 → axes are stable.
    template_entities = []
    for _ in range(n_entities):
        w = float(rng.uniform(0.6, 2.0))
        h = float(rng.uniform(0.1, 0.4))
        cx = float(rng.uniform(-5, 5))
        cy = float(rng.uniform(-5, 5))
        template_entities.append((cx, cy, w, h))

    def build_pattern(prefix, ox, oy):
        return [
            _pad(f"{prefix}{i}", ox + cx, oy + cy, w=w, h=h)
            for i, (cx, cy, w, h) in enumerate(template_entities)
        ]

    # Pattern L at origin, pattern R translated far enough that bucket-radius
    # neighbours don't bleed across.
    L_pads = build_pattern("L", 0.0, 0.0)
    R_pads = build_pattern("R", 100.0, 0.0)
    drawing = {s.handle: s for s in L_pads + R_pads}

    L_handles = {p.handle for p in L_pads}
    R_handles = {p.handle for p in R_pads}

    out_LR = find_matches([p.handle for p in L_pads], drawing)
    L_match_handles = {h for m in out_LR.matches for h in m.handles}

    out_RL = find_matches([p.handle for p in R_pads], drawing)
    R_match_handles = {h for m in out_RL.matches for h in m.handles}

    assert L_match_handles == R_handles, (
        f"seed={seed}: L as template should find exactly R's handles, "
        f"got {L_match_handles} (expected {R_handles})"
    )
    assert R_match_handles == L_handles, (
        f"seed={seed}: R as template should find exactly L's handles, "
        f"got {R_match_handles} (expected {L_handles})"
    )


def test_match_multi_three_rect_smd_with_real_dxf_noise():
    """Real-DXF symptom: 3-rect SMD copies whose vertex coordinates
    differ at the µm scale (manually-placed instances, mixed-precision
    transforms) must still cluster into the same fingerprint bucket and
    the predicted-position check must absorb the resulting PCA-axis drift.

    Reproduces a user-reported case where 2-entity (left+right) found
    every copy but 3-entity (left+right+middle) found none. With
    `FINGERPRINT_DIGITS=6` + `CENTROID_NOISE_TOL=1e-6`, even ~5 µm of
    per-vertex noise puts every drawing instance into its own singleton
    bucket and pushes predicted positions out of tolerance — both
    failures the broadened defaults are sized to absorb.
    """
    rng = np.random.RandomState(0)

    def _rect_corners(cx, cy, w, h):
        """The four corners — closing vertex appended after noise so the
        post-dedup centroid matches DXF semantics (where the trailing
        repeat is a bit-identical copy of the first vertex, not an
        independently-noisy point)."""
        hw, hh = w / 2, h / 2
        return [
            (cx - hw, cy - hh), (cx + hw, cy - hh),
            (cx + hw, cy + hh), (cx - hw, cy + hh),
        ]

    def _xform_with_noise(corners, theta, dx, dy, noise_scale):
        c, s = math.cos(theta), math.sin(theta)
        R = np.array([[c, -s], [s, c]])
        arr = np.asarray(corners) @ R.T + np.array([dx, dy])
        arr += rng.normal(scale=noise_scale, size=arr.shape)
        # Bit-identical closing vertex — real DXF closed polylines store
        # vertex 0 twice, exactly.
        return arr.tolist() + [arr[0].tolist()]

    # User's exact dimensions: left+right 0.25w×0.35h, middle 0.6w×0.3h
    template_pts = [
        _rect_corners(-0.175, 0, 0.25, 0.35),
        _rect_corners(0.175, 0, 0.25, 0.35),
        _rect_corners(0, 0, 0.6, 0.3),
    ]
    copies = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, math.pi / 3),
        (0.0, 10.0, math.pi / 2),
        (-5.0, 5.0, -math.pi / 4),
        (3.0, -7.0, 0.7),
    ]

    drawing: dict[str, EntityShape] = {}
    handles: list[str] = []
    for i, (dx, dy, theta) in enumerate(copies):
        for j, pts in enumerate(template_pts):
            h = f"h{i}_{j}"
            # 100 nm per-vertex noise — the upper end of real-DXF
            # coordinate precision (4-decimal stored coords).
            xformed = _xform_with_noise(pts, theta, dx, dy, noise_scale=1e-4)
            drawing[h] = EntityShape.from_points(
                h, [tuple(p) for p in xformed],
            )
            handles.append(h)

    # First copy's three handles are the template.
    template_handles = handles[:3]
    output = find_matches(template_handles, drawing)

    # 4 non-template copies must all be found, each as a complete 3-handle group.
    assert len(output.matches) == 4, (
        f"expected 4 matches (one per non-template copy), got {len(output.matches)}"
    )
    for m in output.matches:
        assert len(m.handles) == 3
        assert set(m.handles).isdisjoint(template_handles)


def test_match_multi_frame_select_with_stacked_duplicates():
    """Real-DXF symptom: frame-selecting an SMD grabs every overlapping
    polyline (outline + fill, layered representations) — 6 handles for
    a 3-rect visual SMD — while click-selecting grabs 3 (one per
    visible rect). The user reported click-select worked, frame-select
    found nothing.

    Two things must hold:
    (a) The 6-handle frame-select template is internally deduped to 3
        visually-distinct entities, so the rigid alignment doesn't
        over-constrain.
    (b) Each matched group is expanded to include every stacked
        duplicate in the drawing's clusters, so highlighting shows the
        full set of polylines per copy regardless of how the user
        selected the template.
    """
    def _rect_corners(cx, cy, w, h):
        hw, hh = w / 2, h / 2
        return [
            (cx - hw, cy - hh), (cx + hw, cy - hh),
            (cx + hw, cy + hh), (cx - hw, cy + hh),
        ]

    def _xform(corners, theta, dx, dy):
        c, s = math.cos(theta), math.sin(theta)
        R = np.array([[c, -s], [s, c]])
        arr = np.asarray(corners) @ R.T + np.array([dx, dy])
        return arr.tolist() + [arr[0].tolist()]

    # Each of left/right/middle stored twice — the stacked-polyline
    # pattern.
    template_corners = [
        _rect_corners(-0.175, 0, 0.25, 0.35),
        _rect_corners(-0.175, 0, 0.25, 0.35),
        _rect_corners(0.175, 0, 0.25, 0.35),
        _rect_corners(0.175, 0, 0.25, 0.35),
        _rect_corners(0, 0, 0.6, 0.3),
        _rect_corners(0, 0, 0.6, 0.3),
    ]
    copies = [
        (0.0, 0.0, 0.0),
        (10.0, 0.0, math.pi / 3),
        (0.0, 10.0, math.pi / 2),
        (-5.0, 5.0, -math.pi / 4),
        (3.0, -7.0, 0.7),
    ]

    drawing: dict[str, EntityShape] = {}
    handles: list[str] = []
    for i, (dx, dy, theta) in enumerate(copies):
        for j, pts in enumerate(template_corners):
            h = f"h{i}_{j}"
            xformed = _xform(pts, theta, dx, dy)
            drawing[h] = EntityShape.from_points(
                h, [tuple(p) for p in xformed],
            )
            handles.append(h)

    # User frame-selects copy 0 → all 6 handles.
    template_handles = handles[:6]
    output = find_matches(template_handles, drawing)

    assert len(output.matches) == 4, (
        f"expected 4 matches, got {len(output.matches)}"
    )
    for m in output.matches:
        assert len(m.handles) == 6, (
            f"each match should expand to the 6-handle cluster: {m.handles}"
        )
        # No template handle leaks into a match group.
        assert set(m.handles).isdisjoint(template_handles)


def test_match_multi_close_packed_smds_find_each_other_symmetrically():
    """Real-DXF symptom: a row of touching SMDs (neighbour's left pad
    sits at the same coordinate as previous neighbour's right pad).
    The user reported "A 找 B 找得到，B 找 A 找不到" — some neighbours
    matched, others didn't, in a direction-dependent pattern.

    Earlier fix extended `skip` to all cluster members of template
    positions to suppress self-match against stacked twins. But for
    close-packed neighbours whose pads share a cluster with the
    template's pads, that extension wrongly locked the neighbour's
    pads out of matching too. The fix: keep `skip` to the user's own
    selection; instead, suppress only candidates whose cluster_key
    matches a template entity's cluster_key (true stacked twin at
    template's exact physical position).
    """
    def _rect_corners(cx, cy, w, h):
        hw, hh = w / 2, h / 2
        return [
            (cx - hw, cy - hh), (cx + hw, cy - hh),
            (cx + hw, cy + hh), (cx - hw, cy + hh),
        ]

    def _xform(corners, theta, dx, dy):
        c, s = math.cos(theta), math.sin(theta)
        R = np.array([[c, -s], [s, c]])
        arr = np.asarray(corners) @ R.T + np.array([dx, dy])
        return arr.tolist() + [arr[0].tolist()]

    # 3-rect SMD (stacked dups), pitch 0.35 mm → neighbour pads touch
    # exactly: SMD_i right at (i·0.35 + 0.175) = (i+1)·0.35 − 0.175 =
    # SMD_{i+1} left.
    template_corners = [
        _rect_corners(-0.175, 0, 0.25, 0.35),
        _rect_corners(-0.175, 0, 0.25, 0.35),
        _rect_corners(0.175, 0, 0.25, 0.35),
        _rect_corners(0.175, 0, 0.25, 0.35),
        _rect_corners(0, 0, 0.6, 0.3),
        _rect_corners(0, 0, 0.6, 0.3),
    ]
    drawing: dict[str, EntityShape] = {}
    smd_handles: list[list[str]] = []
    for i, (dx, dy, theta) in enumerate(
        [(0.0, 0.0, 0.0), (0.35, 0.0, 0.0),
         (0.7, 0.0, 0.0), (1.05, 0.0, 0.0)]
    ):
        h_list = []
        for j, pts in enumerate(template_corners):
            h = f"smd{i}_{j}"
            xformed = _xform(pts, theta, dx, dy)
            drawing[h] = EntityShape.from_points(
                h, [tuple(p) for p in xformed],
            )
            h_list.append(h)
        smd_handles.append(h_list)

    # Each SMD as template must find every other SMD (handles overlap).
    for i, tpl_h in enumerate(smd_handles):
        output = find_matches(tpl_h, drawing)
        found_other_smds: set[int] = set()
        for m in output.matches:
            for k, other_h in enumerate(smd_handles):
                if k == i:
                    continue
                if set(m.handles) & set(other_h):
                    found_other_smds.add(k)
        expected = set(range(len(smd_handles))) - {i}
        assert found_other_smds == expected, (
            f"SMD{i} as template: found other SMDs {sorted(found_other_smds)}, "
            f"expected {sorted(expected)} (close-packed symmetry)"
        )


def test_match_multi_handle_count_consistent_across_match_groups():
    """Every match group must contain exactly as many handles as the
    user-supplied template — no fewer (partial bleed-out), no more
    (extra handles from a neighbour's shared-pad cluster).

    User report: match JSON contained groups of 3 / 5 / 9 handles in
    the same scan. Cause: cluster expansion blindly unioned every
    member of the matched handle's cluster, which for close-packed
    neighbours includes the adjacent SMD's pads. Fix: bound expansion
    by per-role template multiplicity; collapse two raw matches that
    occupy the same physical positions to one (otherwise click-select
    on a frame-stacked drawing produces N copies of every match).
    """
    def _rect_corners(cx, cy, w, h):
        hw, hh = w / 2, h / 2
        return [
            (cx - hw, cy - hh), (cx + hw, cy - hh),
            (cx + hw, cy + hh), (cx - hw, cy + hh),
        ]

    def _xform(corners, theta, dx, dy):
        c, s = math.cos(theta), math.sin(theta)
        R = np.array([[c, -s], [s, c]])
        arr = np.asarray(corners) @ R.T + np.array([dx, dy])
        return arr.tolist() + [arr[0].tolist()]

    # 4 SMDs in a row, pitch 0.35 → pads share cluster with neighbours.
    # Each SMD has 6 handles (3 visual rects × 2 stacked dups).
    template_corners = [
        _rect_corners(-0.175, 0, 0.25, 0.35),
        _rect_corners(-0.175, 0, 0.25, 0.35),
        _rect_corners(0.175, 0, 0.25, 0.35),
        _rect_corners(0.175, 0, 0.25, 0.35),
        _rect_corners(0, 0, 0.6, 0.3),
        _rect_corners(0, 0, 0.6, 0.3),
    ]
    drawing: dict[str, EntityShape] = {}
    smd_handles: list[list[str]] = []
    for i, (dx, dy, theta) in enumerate(
        [(0.0, 0.0, 0.0), (0.35, 0.0, 0.0),
         (0.7, 0.0, 0.0), (1.05, 0.0, 0.0)]
    ):
        h_list = []
        for j, pts in enumerate(template_corners):
            h = f"smd{i}_{j}"
            xformed = _xform(pts, theta, dx, dy)
            drawing[h] = EntityShape.from_points(
                h, [tuple(p) for p in xformed],
            )
            h_list.append(h)
        smd_handles.append(h_list)

    # Frame-select (all 6 handles): every match must have 6 handles.
    out_frame = find_matches(smd_handles[0], drawing)
    assert len(out_frame.matches) == 3, (
        f"frame-select expected 3 matches (one per non-template SMD), "
        f"got {len(out_frame.matches)}"
    )
    for m in out_frame.matches:
        assert len(m.handles) == 6, (
            f"frame-select match should have 6 handles: {m.handles}"
        )

    # Click-select (1 handle per visual rect): every match must have 3.
    click_template = smd_handles[0][::2]  # 3 handles
    out_click = find_matches(click_template, drawing)
    assert len(out_click.matches) == 3, (
        f"click-select expected 3 matches, got {len(out_click.matches)}"
    )
    for m in out_click.matches:
        assert len(m.handles) == 3, (
            f"click-select match should have 3 handles: {m.handles}"
        )


def test_find_matches_from_pointsets_finds_original_template_instance():
    """Library scan (find_matches_from_pointsets) must match the
    original instance the template was saved from — unlike in-drawing
    `find_matches`, which excludes the user-selected handles because
    "find OTHERS" is the user's intent there.

    User-reported symptom: 4-LINE template, drawing has only that one
    instance. In-drawing scan correctly returns 0 (excluded itself).
    Saving to library and running scan-all returned 0 too — wrong,
    because the saved template is a standalone pattern definition;
    its original DXF context isn't part of "what to exclude".
    """
    def _line(x1, y1, x2, y2):
        return [(x1, y1), (x2, y2)]

    def _box_at(cx, cy, w=1.0, h=0.3):
        hw, hh = w / 2, h / 2
        return [
            _line(cx - hw, cy - hh, cx + hw, cy - hh),
            _line(cx + hw, cy - hh, cx + hw, cy + hh),
            _line(cx + hw, cy + hh, cx - hw, cy + hh),
            _line(cx - hw, cy + hh, cx - hw, cy - hh),
        ]

    drawing: dict[str, EntityShape] = {}
    for i, (cx, cy) in enumerate([(0.0, 0.0)]):  # ONE instance only
        for j, line in enumerate(_box_at(cx, cy)):
            h = f"s{i}_{j}"
            drawing[h] = EntityShape.from_points(h, line, kind="line")

    template_points = _box_at(0.0, 0.0)

    # Library scan finds the (only) instance.
    output = find_matches_from_pointsets(
        template_points, drawing, entity_kinds=["line"] * 4,
    )
    assert len(output.matches) == 1, (
        f"library scan should find the original instance, "
        f"got {len(output.matches)} matches"
    )
    assert set(output.matches[0].handles) == {"s0_0", "s0_1", "s0_2", "s0_3"}

    # In-drawing scan, by contrast, still excludes the user's selection.
    in_drawing = find_matches(["s0_0", "s0_1", "s0_2", "s0_3"], drawing)
    assert len(in_drawing.matches) == 0, (
        f"in-drawing scan should exclude the user-selected template, "
        f"got {len(in_drawing.matches)} matches"
    )


def test_match_multi_wrong_shape_seed_rejected():
    """A drawing entity with a different shape than the seed template
    must not produce a match — even when "other" template entities
    happen to align at predicted positions. Closed under the rigid-
    transform / fingerprint-bucket matcher by the fingerprint gate (the
    wrong-shape candidate sits in a different bucket and is never
    enumerated as a candidate seed)."""
    t_seed = _pad("Tseed", 0, 0, w=1.0, h=0.4)
    t_other = _pad("Tother", 5, 0, w=1.0, h=0.4)
    # Chamfered rect: rect with one corner notched off, picked because PCA
    # still points along the x-axis (so pose hypothesis aligns with the
    # template's frame) but the chamfer floor against the original rect
    # blows past tolerance.
    wrong_seed = shape("X", [
        (49.5, -0.2), (50.5, -0.2), (50.5, 0.2),
        (49.7, 0.2), (49.5, 0.1), (49.5, -0.2),
    ])
    other_correct = _pad("Xother", 55, 0, w=1.0, h=0.4)
    drawing = {
        "Tseed": t_seed, "Tother": t_other,
        "X": wrong_seed, "Xother": other_correct,
    }
    out = find_matches(["Tseed", "Tother"], drawing)
    for m in out.matches:
        assert "X" not in m.handles, (
            f"wrong-shape seed leaked into a match: handles={m.handles}"
        )

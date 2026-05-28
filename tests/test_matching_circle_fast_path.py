"""Single-CIRCLE radius-bucket fast path.

Benchmark reference (BGA-style packaging DXF, 381,806 candidate circles):
    Before: 68,569 ms / 381,806 matches / 18,961 near-misses
            (generic _match_single: PCA + 4 sign variants + Chamfer per candidate)
    After:  ~few ms (numbers TBD on the real file; synthetic 5k-circle
            benchmark in test_circle_fast_path_is_fast asserts <100 ms)

The savings come from skipping PCA/Chamfer entirely and emitting no NearMiss
objects. See openspec/changes/add-circle-scan-fast-path/design.md.
"""

from __future__ import annotations

import math
import time

import pytest

from app.library import (
    DEFAULT_LIBRARY_ID,
    LibraryRegistry,
    Store,
    Template,
    build_handle_index,
    collect_entity_kinds,
)
from app.matching import (
    CIRCLE_RADIUS_KEY_DIGITS,
    EntityShape,
    _match_single_circle,
    _radius_bucket_cache,
    _radius_bucket_key,
    build_entity_shapes,
    find_matches,
    find_matches_from_pointsets,
)


# ---- helpers --------------------------------------------------------------
def _sample_circle(
    cx: float, cy: float, r: float, n: int | None = None,
) -> list[tuple[float, float]]:
    """Sample evenly-spaced points around a circle, matching the layout
    `collect_entity_points` produces for primitive type 'circle' (so test
    templates compare cleanly against the synthesised drawing-side points)."""
    if n is None:
        n = max(8, min(64, round(2.0 * math.pi * r / 0.01)))
    return [
        (cx + r * math.cos(2.0 * math.pi * i / n),
         cy + r * math.sin(2.0 * math.pi * i / n))
        for i in range(n)
    ]


def _circle_primitive(handle: str, cx: float, cy: float, r: float) -> dict:
    return {"type": "circle", "handle": handle, "center": [cx, cy], "r": r}


def _polyline_primitive(handle: str, points: list[tuple[float, float]]) -> dict:
    return {"type": "polyline", "handle": handle, "points": points, "closed": True}


def _build_shapes(primitives: list[dict]) -> dict[str, EntityShape]:
    return build_entity_shapes(primitives, build_handle_index(primitives))


def _virtual_circle_shape(handle: str, r: float) -> EntityShape:
    """Construct a circle EntityShape from synthesized points (the path
    library Templates take)."""
    return EntityShape.from_points(handle, _sample_circle(0.0, 0.0, r), kind="circle")


# ---- 1.4: EntityShape.kind from build_entity_shapes -----------------------
def test_build_entity_shapes_kind_circle():
    prims = [_circle_primitive("A", 0.0, 0.0, 1.0)]
    shapes = _build_shapes(prims)
    assert shapes["A"].kind == "circle"


def test_build_entity_shapes_kind_polyline():
    prims = [_polyline_primitive("A", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])]
    shapes = _build_shapes(prims)
    assert shapes["A"].kind == "polyline"


def test_build_entity_shapes_kind_mixed_is_none():
    prims = [
        _polyline_primitive("A", [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]),
        _circle_primitive("A", 5.0, 5.0, 0.5),
    ]
    shapes = _build_shapes(prims)
    assert shapes["A"].kind is None


def test_collect_entity_kinds_helper_agrees():
    prims = [_circle_primitive("A", 0.0, 0.0, 1.0),
             _polyline_primitive("B", [(0.0, 0.0), (1.0, 0.0)])]
    hi = build_handle_index(prims)
    assert collect_entity_kinds(prims, hi, "A") == "circle"
    assert collect_entity_kinds(prims, hi, "B") == "polyline"
    assert collect_entity_kinds(prims, hi, "MISSING") is None


# ---- 4.2: exact-radius match returns same-radius siblings -----------------
def test_circle_fast_path_returns_same_radius_siblings():
    prims = [_circle_primitive("T", 0.0, 0.0, 1.0)]
    # 5 same-radius circles at different positions
    for i, (cx, cy) in enumerate([(10, 0), (0, 10), (-10, 0), (0, -10), (5, 5)]):
        prims.append(_circle_primitive(f"S{i}", cx, cy, 1.0))
    # 3 different-radius circles
    prims.append(_circle_primitive("D0", 20, 20, 1.5))
    prims.append(_circle_primitive("D1", 30, 30, 0.5))
    prims.append(_circle_primitive("D2", 40, 40, 2.0))
    shapes = _build_shapes(prims)
    out = find_matches(["T"], shapes)
    matched = {m.handles[0] for m in out.matches}
    assert matched == {"S0", "S1", "S2", "S3", "S4"}
    assert out.near_misses == []


# ---- 4.3: mixed-radius exclusion (1.0 vs 1.001 → different buckets) ------
def test_circle_fast_path_excludes_nearby_radii():
    prims = [
        _circle_primitive("T", 0.0, 0.0, 1.0),
        _circle_primitive("CLOSE", 10, 0, 1.001),
        _circle_primitive("FAR", 20, 0, 1.05),
        _circle_primitive("SMALL", 30, 0, 0.5),
    ]
    shapes = _build_shapes(prims)
    out = find_matches(["T"], shapes)
    assert out.matches == []
    assert out.near_misses == []


# ---- 4.4: FP-noise within bucket precision still matches ----------------
def test_circle_fast_path_bucket_absorbs_fp_noise():
    fp_noise = 10 ** -(CIRCLE_RADIUS_KEY_DIGITS + 2)
    prims = [
        _circle_primitive("T", 0.0, 0.0, 1.0),
        _circle_primitive("NOISE", 10, 0, 1.0 + fp_noise),
    ]
    shapes = _build_shapes(prims)
    out = find_matches(["T"], shapes)
    assert {m.handles[0] for m in out.matches} == {"NOISE"}


def test_radius_bucket_key_collapses_fp_noise():
    fp_noise = 10 ** -(CIRCLE_RADIUS_KEY_DIGITS + 1)
    assert _radius_bucket_key(1.0) == _radius_bucket_key(1.0 + fp_noise)
    # And a real difference at the bucket precision does NOT collapse.
    real_diff = 10 ** -(CIRCLE_RADIUS_KEY_DIGITS - 1)
    assert _radius_bucket_key(1.0) != _radius_bucket_key(1.0 + real_diff)


# ---- 4.5: skip set respected ---------------------------------------------
def test_circle_fast_path_excludes_template_own_handle():
    prims = [
        _circle_primitive("T", 0.0, 0.0, 1.0),
        _circle_primitive("S0", 10, 0, 1.0),
    ]
    shapes = _build_shapes(prims)
    out = find_matches(["T"], shapes)
    matched = {m.handles[0] for m in out.matches}
    assert "T" not in matched
    assert matched == {"S0"}


def test_circle_fast_path_direct_call_respects_skip():
    prims = [
        _circle_primitive("A", 0.0, 0.0, 1.0),
        _circle_primitive("B", 1.0, 0.0, 1.0),
        _circle_primitive("C", 2.0, 0.0, 1.0),
    ]
    shapes = _build_shapes(prims)
    tpl = shapes["A"]
    out = _match_single_circle(tpl, shapes, skip={"A", "C"})
    assert {m.handles[0] for m in out.matches} == {"B"}


# ---- 4.6: no NearMiss invariant ------------------------------------------
def test_circle_fast_path_emits_no_near_misses():
    # Mix of same-radius, near-radius, far-radius, and non-circle entities.
    prims = [
        _circle_primitive("T", 0, 0, 1.0),
        _circle_primitive("S", 10, 0, 1.0),
        _circle_primitive("NEAR", 20, 0, 1.001),
        _circle_primitive("FAR", 30, 0, 2.0),
        _polyline_primitive("PLY", [(0, 0), (1, 0), (1, 1), (0, 1)]),
    ]
    shapes = _build_shapes(prims)
    out = find_matches(["T"], shapes)
    assert out.near_misses == []


# ---- 4.7: legacy template (entity_kinds=[None]) falls back to generic ---
def test_legacy_template_with_null_kinds_still_matches():
    # Template stored with entity_kinds=None (legacy row).
    tpl_points = _sample_circle(0.0, 0.0, 1.0)
    tpl = Template.from_entities("BGABall", [tpl_points], entity_kinds=None)
    assert tpl.entity_kinds == [None]

    # Drawing has a same-radius circle. The generic path should still find it.
    prims = [_circle_primitive("S", 10, 0, 1.0)]
    shapes = _build_shapes(prims)
    out = find_matches_from_pointsets(
        tpl.entity_point_sets, shapes, entity_kinds=tpl.entity_kinds,
    )
    matched = {m.handles[0] for m in out.matches}
    assert matched == {"S"}


def test_legacy_template_does_not_use_fast_path():
    # A slightly-off-radius circle (1.0 vs 1.01) is REJECTED by the fast
    # path (different bucket) but ACCEPTED by the generic path (within the
    # ±5% scale band + tight chamfer on a perfect circle). Comparing the
    # two with the same template + drawing — kinded vs unkinded — proves
    # we really dispatched to different paths.
    tpl_points = _sample_circle(0.0, 0.0, 1.0)
    prims = [_circle_primitive("OFF", 0, 0, 1.01)]
    shapes = _build_shapes(prims)

    tpl_legacy = Template.from_entities("BGABall", [tpl_points], entity_kinds=None)
    out_legacy = find_matches_from_pointsets(
        tpl_legacy.entity_point_sets, shapes, entity_kinds=tpl_legacy.entity_kinds,
    )
    # Generic path accepts the 1.01-radius circle.
    assert {m.handles[0] for m in out_legacy.matches} == {"OFF"}

    tpl_kinded = Template.from_entities("BGABall", [tpl_points], entity_kinds=["circle"])
    out_kinded = find_matches_from_pointsets(
        tpl_kinded.entity_point_sets, shapes, entity_kinds=tpl_kinded.entity_kinds,
    )
    # Fast path rejects it (different bucket) and emits no near-miss.
    assert out_kinded.matches == []
    assert out_kinded.near_misses == []


def test_kinded_template_uses_fast_path_via_from_pointsets():
    tpl_points = _sample_circle(0.0, 0.0, 1.0)
    tpl = Template.from_entities("BGABall", [tpl_points], entity_kinds=["circle"])
    prims = [
        _circle_primitive("S", 10, 0, 1.0),
        _circle_primitive("DIFF", 20, 0, 1.5),
    ]
    shapes = _build_shapes(prims)
    out = find_matches_from_pointsets(
        tpl.entity_point_sets, shapes, entity_kinds=tpl.entity_kinds,
    )
    assert {m.handles[0] for m in out.matches} == {"S"}
    # Fast path → no near-miss for the diff-radius circle.
    assert out.near_misses == []


# ---- 4.8: cache invalidation when drawing dict is rebuilt ---------------
def test_radius_bucket_cache_invalidates_on_new_drawing():
    prims_a = [
        _circle_primitive("T", 0.0, 0.0, 1.0),
        _circle_primitive("S", 10, 0, 1.0),
    ]
    shapes_a = _build_shapes(prims_a)
    out_a = find_matches(["T"], shapes_a)
    assert {m.handles[0] for m in out_a.matches} == {"S"}

    # New drawing, different radius. Should be a fresh bucket dict despite
    # the cache holding an entry for the previous drawing's id.
    prims_b = [
        _circle_primitive("T", 0.0, 0.0, 2.5),
        _circle_primitive("S", 10, 0, 2.5),
    ]
    shapes_b = _build_shapes(prims_b)
    assert id(shapes_a) != id(shapes_b)
    out_b = find_matches(["T"], shapes_b)
    assert {m.handles[0] for m in out_b.matches} == {"S"}


# ---- 4.5/edge: template with radius=0 falls back -------------------------
def test_zero_radius_circle_falls_back_to_generic():
    # Direct construction with kind="circle" but radius=0 (degenerate). The
    # dispatcher must NOT enter the fast path.
    deg = EntityShape.from_points("DEG", [(0.0, 0.0)], kind="circle")
    assert deg.radius == 0.0
    # Generic path also returns nothing for a 1-vertex template (vertex_count<2).
    shapes = {"DEG": deg, "OTHER": EntityShape.from_points("OTHER", [(1.0, 0.0)])}
    out = find_matches(["DEG"], shapes)
    assert out.matches == []


def test_polyline_template_skips_fast_path():
    # A polyline of equal-radius points should NOT enter the fast path.
    pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    prims = [_polyline_primitive("T", pts), _polyline_primitive("S", pts)]
    shapes = _build_shapes(prims)
    out = find_matches(["T"], shapes)
    # Generic path picks up the same polyline as a match.
    assert {m.handles[0] for m in out.matches} == {"S"}


# ---- 4.1: smoke benchmark on synthetic dense circle grid ----------------
def test_circle_fast_path_is_fast():
    """Build 5000 same-radius circles + 2500 noise-radius circles; assert
    a single frame-select scan finishes in well under 100ms.

    Generic path on the same data would take seconds (PCA + Chamfer per
    candidate); the bucket lookup makes it microseconds end-to-end.
    """
    prims: list[dict] = []
    prims.append(_circle_primitive("TPL", 0.0, 0.0, 1.0))
    for i in range(5000):
        prims.append(_circle_primitive(f"S{i}", float(i), 0.0, 1.0))
    for i in range(2500):
        # Noise spacing 1e-3 mm = 10 buckets per step under
        # CIRCLE_RADIUS_KEY_DIGITS=4. Stays clear of the ±1 lookup window
        # (which intentionally absorbs sub-bucket FP drift; see
        # `test_circle_fast_path_absorbs_bucket_edge_drift`).
        prims.append(_circle_primitive(f"N{i}", float(i), 1.0, 1.0 + (i + 1) * 1e-3))
    shapes = _build_shapes(prims)

    t0 = time.perf_counter()
    out = find_matches(["TPL"], shapes)
    dt_ms = (time.perf_counter() - t0) * 1000.0

    assert len(out.matches) == 5000  # all bit-identical-radius circles
    assert out.near_misses == []
    # Generous threshold: 100ms covers bucket build + lookup + result alloc.
    # On a typical dev machine the actual wall time is single-digit ms.
    assert dt_ms < 100.0, f"fast-path took {dt_ms:.1f} ms, expected <100 ms"


# ---- entity_kinds round-trip through SQLite -----------------------------
def test_entity_kinds_roundtrips_through_sqlite(tmp_db):
    """A committed template's entity_kinds survives a store reload.

    Use a library-scoped class (FiducialCircle) so the surviving row's
    product_id IS NULL and the library-admin reload sees it. The
    entity_kinds round-trip is the only thing under test here; class
    choice is incidental."""
    store = Store(tmp_db)
    lib = LibraryRegistry(store).get(DEFAULT_LIBRARY_ID)
    tpl_points = _sample_circle(0.0, 0.0, 1.0)
    tpl = Template.from_entities("FiducialCircle", [tpl_points], entity_kinds=["circle"])
    lib.add_template(tpl)

    # Reload from disk.
    lib2 = LibraryRegistry(Store(tmp_db)).get(DEFAULT_LIBRARY_ID)
    reloaded = lib2.templates_of("FiducialCircle")
    assert len(reloaded) == 1
    assert reloaded[0].entity_kinds == ["circle"]


def test_legacy_template_loads_with_null_kinds(tmp_db):
    """A row inserted with entity_kinds=NULL (simulating pre-migration data)
    parses back as [None, ...].

    Use a library-scoped class (FiducialCircle) so the row's null
    product_id is legitimate and survives the boot purge. The test's
    point is the entity_kinds=NULL → [None] fallback; class choice is
    incidental."""
    import sqlite3
    import json as _json
    import time as _time
    import uuid as _uuid

    Store(tmp_db)  # initialise schema + migration

    # Manually insert a row with NULL entity_kinds via raw SQL.
    raw_points = [_sample_circle(0.0, 0.0, 1.0)]
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.execute(
            "INSERT INTO templates "
            "(id, library_id, class_name, entity_point_sets, centroid_x, centroid_y, "
            " bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, created_at, entity_kinds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                str(_uuid.uuid4()), DEFAULT_LIBRARY_ID, "FiducialCircle",
                _json.dumps(raw_points), 0.0, 0.0, -1.0, -1.0, 1.0, 1.0, _time.time(),
            ),
        )
        conn.commit()

    # Reload via Library and confirm fallback.
    lib = LibraryRegistry(Store(tmp_db)).get(DEFAULT_LIBRARY_ID)
    reloaded = lib.templates_of("FiducialCircle")
    assert len(reloaded) == 1
    assert reloaded[0].entity_kinds == [None]


# ---- bucket-edge drift (banker's rounding fence-post) --------------------
def test_circle_fast_path_absorbs_bucket_edge_drift():
    """Template's recomputed radius lands one bucket away from the drawing's
    analytical radius (the banker's-rounding fence-post failure mode `9d66024`
    introduced). The ±1 neighbour lookup MUST still find the drawing circle;
    a single-bucket lookup would miss entirely."""
    # r·10^4 = 412.5 exactly — banker's rounds to 412 (even). The drawing
    # primitive lands in bucket 412 via from_circle's analytical r.
    r_drawing = 0.04125
    prims = [_circle_primitive("D", 0.0, 0.0, r_drawing)]
    shapes = _build_shapes(prims)
    drawing_bucket = _radius_bucket_key(shapes["D"].radius)
    assert drawing_bucket == 412  # sanity: confirm banker's-rounding choice

    # Simulate the boundary-drift failure: the stored template's
    # from_points-recomputed radius lands above the .5 fence-post and
    # rounds to 413. Direct field mutation on the dataclass mirrors the
    # post-from_points state without needing to reproduce the FP-precision
    # path that produces it (which is data-dependent on world coords).
    template = _virtual_circle_shape("_tpl", r_drawing)
    template.radius = r_drawing + 1e-7  # > 0.5/1e4 above the fence-post
    template_bucket = _radius_bucket_key(template.radius)
    assert template_bucket == 413
    assert abs(template_bucket - drawing_bucket) == 1  # adjacent buckets

    # Regression check: a single-bucket lookup at the template's key returns
    # empty — proves this test exercises the pre-fix failure mode.
    from app.matching import _get_radius_buckets
    single_bucket_hits = _get_radius_buckets(shapes).get(template_bucket, [])
    assert single_bucket_hits == []

    # With ±1 window: matches MUST include "D".
    out = _match_single_circle(template, shapes, skip=set())
    assert {m.handles[0] for m in out.matches} == {"D"}
    assert out.matches[0].score == 0.0
    assert out.matches[0].scale == 1.0
    assert out.near_misses == []


def test_circle_fast_path_pm1_window_does_not_reach_real_design_steps():
    """The ±1 lookup window MUST NOT pull in circles whose radius is far
    enough away to represent a real design distinction (≥ 1 µm = 10 buckets
    under CIRCLE_RADIUS_KEY_DIGITS = 4)."""
    r0 = 0.4
    prims = [
        _circle_primitive("T",   0.0, 0.0, r0),          # template, excluded by skip
        _circle_primitive("SAME", 1.0, 0.0, r0),         # same radius → match
        _circle_primitive("FAR",  2.0, 0.0, r0 + 1e-3),  # 1 µm bigger = 10 buckets away
    ]
    shapes = _build_shapes(prims)
    out = find_matches(["T"], shapes)
    matched = {m.handles[0] for m in out.matches}
    assert matched == {"SAME"}
    assert "FAR" not in matched


# ---- helper to clear shared module cache between tests ------------------
@pytest.fixture(autouse=True)
def _clear_radius_bucket_cache():
    _radius_bucket_cache.clear()
    yield
    _radius_bucket_cache.clear()

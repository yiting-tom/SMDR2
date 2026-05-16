"""DXF flatten pipeline — smoke test on the bundled test.dxf."""

from __future__ import annotations

from app.dxf import BASE_TOLERANCE, choose_flatten_tolerance, flatten_for_render
from app.library import build_handle_index, collect_entity_points
from app.matching import build_entity_shapes


def test_flatten_produces_primitives(test_dxf_path):
    out = flatten_for_render(str(test_dxf_path))
    assert out.primitives, "expected at least some primitives"
    assert out.bbox is not None
    # Background should be a hex color string.
    assert out.background.startswith("#") and len(out.background) == 7
    # Every primitive should carry its source DXF handle.
    for p in out.primitives:
        assert "handle" in p
        assert p["type"] in {"line", "polyline", "filled_polygon", "point", "circle"}


def test_circle_entity_emits_circle_primitive(tmp_path):
    """A DXF CIRCLE entity must be emitted as a `circle` primitive carrying
    `center` + `r`, not as a flattened closed polyline."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    circle = msp.add_circle(center=(3.0, 4.0), radius=0.15)
    dxf_path = tmp_path / "circle.dxf"
    doc.saveas(str(dxf_path))

    out = flatten_for_render(str(dxf_path))
    prims_for_handle = [p for p in out.primitives if p.get("handle") == circle.dxf.handle]
    assert prims_for_handle, "expected at least one primitive for the CIRCLE"
    # Exactly one circle primitive; no polyline fallback for the same handle.
    circle_prims = [p for p in prims_for_handle if p["type"] == "circle"]
    polyline_prims = [p for p in prims_for_handle if p["type"] == "polyline"]
    assert len(circle_prims) == 1, f"expected 1 circle primitive, got {len(circle_prims)}"
    assert not polyline_prims, "CIRCLE must not flatten to a polyline"
    cp = circle_prims[0]
    cx, cy = cp["center"]
    assert abs(cx - 3.0) < 1e-3 and abs(cy - 4.0) < 1e-3
    assert abs(cp["r"] - 0.15) / 0.15 < 0.01  # within 1 %


def test_collect_entity_points_synthesizes_circle_cloud(tmp_path):
    """A `circle` primitive must feed the matcher a deterministic, evenly-
    spaced point cloud whose radius matches the source CIRCLE. Same DXF →
    same cloud, run after run (matcher fingerprints stay stable)."""
    import math as _math

    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    circle = msp.add_circle(center=(1.0, -2.0), radius=0.5)
    dxf_path = tmp_path / "one_circle.dxf"
    doc.saveas(str(dxf_path))

    out = flatten_for_render(str(dxf_path))
    idx = build_handle_index(out.primitives)
    pts_a = collect_entity_points(out.primitives, idx, circle.dxf.handle)
    pts_b = collect_entity_points(out.primitives, idx, circle.dxf.handle)

    # Deterministic across calls.
    assert pts_a == pts_b
    # 8 ≤ N ≤ 64.
    assert 8 <= len(pts_a) <= 64
    # Every point sits on the circle within 1 %.
    for x, y in pts_a:
        r = _math.hypot(x - 1.0, y - (-2.0))
        assert abs(r - 0.5) / 0.5 < 0.01


def test_non_circular_closed_polyline_stays_polyline(tmp_path):
    """An 8-vertex closed POLYLINE that is NOT a circular approximation must
    remain a polyline — guards against the circle detector eating real
    octagonal pads / fiducial outlines."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    # A clearly non-circular octagon (alternating long/short radial distances).
    pts = [
        (0.0, 0.0),
        (3.0, 0.0),
        (3.5, 0.5),
        (3.5, 2.0),
        (3.0, 2.5),
        (0.0, 2.5),
        (-0.5, 2.0),
        (-0.5, 0.5),
    ]
    poly = msp.add_lwpolyline(pts, close=True)

    dxf_path = tmp_path / "octagon.dxf"
    doc.saveas(str(dxf_path))

    out = flatten_for_render(str(dxf_path))
    prims_for_handle = [p for p in out.primitives if p.get("handle") == poly.dxf.handle]
    assert prims_for_handle
    types = {p["type"] for p in prims_for_handle}
    assert "circle" not in types, "non-circular polyline must not collapse to a circle"
    assert "polyline" in types


def test_handle_index_groups_correctly(test_dxf_path):
    out = flatten_for_render(str(test_dxf_path))
    idx = build_handle_index(out.primitives)
    assert len(idx) > 0
    # Every primitive index must round-trip through the handle index.
    for h, indices in idx.items():
        for i in indices:
            assert out.primitives[i]["handle"] == h


def test_entity_shapes_have_consistent_stats(test_dxf_path):
    out = flatten_for_render(str(test_dxf_path))
    idx = build_handle_index(out.primitives)
    shapes = build_entity_shapes(out.primitives, idx)
    for h, s in shapes.items():
        assert s.vertex_count > 0
        assert s.radius >= 0
        assert s.path_length >= 0
        # Centroid lies inside the bbox of the points.
        xs = s.points[:, 0]; ys = s.points[:, 1]
        assert xs.min() - 1e-6 <= s.centroid[0] <= xs.max() + 1e-6
        assert ys.min() - 1e-6 <= s.centroid[1] <= ys.max() + 1e-6


def test_collect_entity_points_matches_shape_points(test_dxf_path):
    out = flatten_for_render(str(test_dxf_path))
    idx = build_handle_index(out.primitives)
    shapes = build_entity_shapes(out.primitives, idx)
    h = next(iter(shapes))
    raw = collect_entity_points(out.primitives, idx, h)
    # EntityShape may drop a trailing-equals-first point of closed polylines;
    # raw includes it. So shape.vertex_count is either equal or one less.
    assert shapes[h].vertex_count in (len(raw), len(raw) - 1)


def test_decorative_dxf_types_are_flagged_and_excluded_from_index(tmp_path):
    """TEXT / MTEXT / DIMENSION / HATCH must render but not participate in
    selection or matching. JSONBackend tags them with `decorative: True` and
    build_handle_index drops them."""
    import ezdxf
    from ezdxf.math import Vec3

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()

    # One regular selectable entity:
    line = msp.add_line((0, 0), (10, 0))
    # Decorative entities:
    txt = msp.add_text("hello", dxfattribs={"insert": (5, 5), "height": 1})
    mtxt = msp.add_mtext("hello mtext", dxfattribs={"insert": (5, 10), "char_height": 1})
    # Hatch needs at least one boundary path:
    hatch = msp.add_hatch(dxfattribs={"layer": "FILL"})
    hatch.paths.add_polyline_path([(0, 0), (1, 0), (1, 1), (0, 1)], is_closed=True)
    # Dimension:
    dim = msp.add_linear_dim(base=(0, 8), p1=(0, 5), p2=(10, 5))
    dim.render()  # produces the dimension's geometry block

    dxf_path = tmp_path / "synth.dxf"
    doc.saveas(str(dxf_path))

    out = flatten_for_render(str(dxf_path))
    # Bucket primitives by their source-entity handle.
    by_handle = {}
    for p in out.primitives:
        by_handle.setdefault(p.get("handle"), []).append(p)

    # The LINE entity's primitives must NOT carry the decorative flag.
    line_prims = by_handle.get(line.dxf.handle, [])
    assert line_prims, "expected the LINE to produce at least one primitive"
    assert all(not p.get("decorative") for p in line_prims)

    # Each decorative entity's primitives MUST carry the flag.
    for ent in (txt, mtxt, hatch):
        prims = by_handle.get(ent.dxf.handle, [])
        # Some entities (e.g. very thin text in some fonts) might collapse to
        # nothing renderable — only assert flagging when there is geometry.
        if prims:
            assert all(p.get("decorative") for p in prims), \
                f"{ent.dxftype()} primitives missing decorative flag"

    # Build the matching handle index — decoratives must drop out.
    idx = build_handle_index(out.primitives)
    assert line.dxf.handle in idx
    for ent in (txt, mtxt, hatch):
        assert ent.dxf.handle not in idx


# ---- adaptive flatten tolerance ------------------------------------------

def _make_scaled_dxf(tmp_path, scale: float, name: str):
    """Build a DXF with one LINE (bbox driver) and one ELLIPSE at a given
    coordinate scale. Returns the file path."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    # LINE drives the bbox: 30 × 30 units (so diagonal ≈ 30·√2 ≈ 42.4).
    msp.add_line((0, 0), (30 * scale, 30 * scale))
    # ELLIPSE roughly mid-page — its curve is the thing flattened.
    msp.add_ellipse(
        center=(15 * scale, 15 * scale),
        major_axis=(5 * scale, 0),
        ratio=0.5,
    )
    p = tmp_path / name
    doc.saveas(str(p))
    return p


def test_choose_flatten_tolerance_pure_helper():
    # Floor: tiny diagonals don't drop below the base.
    assert choose_flatten_tolerance(0.0001) == BASE_TOLERANCE
    assert choose_flatten_tolerance(100) == BASE_TOLERANCE  # 100 × 1e-5 = 0.001 < base
    # Above the floor: scales linearly with diagonal.
    assert choose_flatten_tolerance(100_000) == 1.0  # 100_000 × 1e-5 = 1.0
    # Defensive on bad input.
    assert choose_flatten_tolerance(-5) == BASE_TOLERANCE
    assert choose_flatten_tolerance(float("nan")) == BASE_TOLERANCE


def test_flatten_tolerance_uses_base_for_normal_scale(tmp_path):
    """A DXF with packaging-scale bbox (~ tens of mm) keeps the floor
    tolerance. Verifies no behaviour change on the normal case."""
    path = _make_scaled_dxf(tmp_path, scale=1.0, name="normal.dxf")
    out = flatten_for_render(str(path))
    assert out.flatten_tolerance == BASE_TOLERANCE


def test_flatten_tolerance_relaxes_for_oversized_scale(tmp_path):
    """Same DXF blown up 1000× (the INSUNITS-bug scenario) — tolerance
    scales with the new diagonal and the ELLIPSE produces a comparable
    number of vertices (within 2×) instead of exploding by ~32×."""
    normal_path = _make_scaled_dxf(tmp_path, scale=1.0, name="normal.dxf")
    huge_path = _make_scaled_dxf(tmp_path, scale=1000.0, name="huge.dxf")

    out_normal = flatten_for_render(str(normal_path))
    out_huge = flatten_for_render(str(huge_path))

    assert out_normal.flatten_tolerance == BASE_TOLERANCE
    # 30×30 box, 1000× scale → diagonal = 30·√2·1000 ≈ 42_426 → tol ≈ 0.4243.
    assert out_huge.flatten_tolerance > BASE_TOLERANCE
    assert 0.3 < out_huge.flatten_tolerance < 0.6

    def ellipse_verts(out):
        # The single ELLIPSE in our fixture; ezdxf emits a full ellipse via
        # draw_path as a closed polyline (radial variance too high for our
        # circle detector since ratio=0.5).
        for p in out.primitives:
            if p.get("decorative"):
                continue
            if p["type"] == "polyline":
                return len(p["points"])
        raise AssertionError("no polyline (ellipse) primitive found")

    n_normal = ellipse_verts(out_normal)
    n_huge = ellipse_verts(out_huge)
    # Vertex count for arc flattening scales with √(r/ε). With r at 1000×
    # and ε at √D×const ≈ 42× (the diagonal grew 1000×), the ratio r/ε
    # tightens ~24×, giving ~5× vertex growth. Without adaptive tolerance
    # the same file would explode ~32×. Bound at 8× confirms we're in the
    # adaptive regime, not the catastrophic one.
    assert n_normal <= n_huge <= n_normal * 8, \
        f"ellipse vertex count drift: normal={n_normal} huge={n_huge}"


def test_render_output_carries_insunits(tmp_path):
    """`$INSUNITS` from the DXF header must round-trip onto RenderOutput so
    the preprocess worker can persist it."""
    import ezdxf

    # Explicit mm (INSUNITS = 4).
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.modelspace().add_line((0, 0), (10, 10))
    p = tmp_path / "mm.dxf"
    doc.saveas(str(p))
    assert flatten_for_render(str(p)).insunits == 4

    # Unitless (INSUNITS = 0). ezdxf's new() defaults to 0 already; verify
    # we see exactly that, not None.
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 0
    doc.modelspace().add_line((0, 0), (10, 10))
    p = tmp_path / "unitless.dxf"
    doc.saveas(str(p))
    assert flatten_for_render(str(p)).insunits == 0


def test_flatten_tolerance_falls_back_when_extents_unavailable(tmp_path):
    """An empty modelspace has no extents → tolerance falls back to base
    and flatten still completes cleanly."""
    import ezdxf

    doc = ezdxf.new("R2010", setup=True)
    # Nothing added to msp.
    path = tmp_path / "empty.dxf"
    doc.saveas(str(path))

    out = flatten_for_render(str(path))
    assert out.flatten_tolerance == BASE_TOLERANCE
    assert out.primitives == []

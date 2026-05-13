"""DXF flatten pipeline — smoke test on the bundled test.dxf."""

from __future__ import annotations

from app.dxf import flatten_for_render
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
        assert p["type"] in {"line", "polyline", "filled_polygon", "point"}


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

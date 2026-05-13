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

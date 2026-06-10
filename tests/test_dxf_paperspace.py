"""Paper-space layout (AutoCAD-tab) rendering in `flatten_for_render`.

Covers the case where a DXF exported from a DWG keeps its geometry in
paper-space layout tabs rather than model space. Before this feature
`flatten_for_render` only walked `doc.modelspace()`, so such files came
back empty.
"""

from __future__ import annotations

import ezdxf

from app.dxf import enumerate_layouts, flatten_for_render


def _paperspace_dxf(tmp_path, *, layouts):
    """Build a DXF with an EMPTY model space and geometry in the named
    paper-space layouts. `layouts` maps tab name → number of LINE entities.
    Returns (path, {tab: [handles]})."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4  # declare mm so auto-rescale doesn't convert ezdxf-default meters
    handles: dict[str, list[str]] = {}
    for name, n in layouts.items():
        if name != "Layout1":  # Layout1 ships with the doc; others need creating
            doc.layouts.new(name)
        psp = doc.paperspace(name)
        hs = []
        for i in range(n):
            ln = psp.add_line((0, i), (10, i))
            hs.append(ln.dxf.handle)
        handles[name] = hs
    path = tmp_path / "paperspace.dxf"
    doc.saveas(str(path))
    return path, handles


def test_modelspace_empty_falls_back_to_paper_layout(tmp_path):
    """Auto mode: model space empty + one content layout → render it."""
    path, handles = _paperspace_dxf(tmp_path, layouts={"Layout1": 3})
    out = flatten_for_render(str(path))
    assert out.primitives, "paper-space geometry must render"
    assert out.bbox is not None
    assert out.source_layout == "Layout1"
    assert out.source_is_paperspace is True
    got = {p.get("handle") for p in out.primitives}
    assert set(handles["Layout1"]).issubset(got)


def test_richest_layout_wins_in_auto_mode(tmp_path):
    """With model space empty and several content layouts, auto mode picks
    the one with the most entities."""
    path, _ = _paperspace_dxf(tmp_path, layouts={"Layout1": 2, "Layout2": 9})
    out = flatten_for_render(str(path))
    assert out.source_layout == "Layout2"
    assert out.source_is_paperspace is True


def test_explicit_layout_name_renders_that_tab(tmp_path):
    """An explicit `layout_name` overrides the richest-layout heuristic."""
    path, handles = _paperspace_dxf(tmp_path, layouts={"Layout1": 2, "Layout2": 9})
    out = flatten_for_render(str(path), layout_name="Layout1")
    assert out.source_layout == "Layout1"
    got = {p.get("handle") for p in out.primitives}
    assert set(handles["Layout1"]).issubset(got)
    # Layout2's handles must NOT appear — only the requested tab is drawn.
    assert not set(handles["Layout2"]) & got


def test_unknown_layout_name_falls_back_to_auto(tmp_path):
    """A stale / unknown tab name degrades to auto-resolution, not a crash."""
    path, _ = _paperspace_dxf(tmp_path, layouts={"Layout1": 4})
    out = flatten_for_render(str(path), layout_name="GoneTab")
    assert out.source_layout == "Layout1"
    assert out.primitives


def test_modelspace_geometry_takes_precedence(tmp_path):
    """Zero regression: when model space has geometry, auto mode renders it
    and ignores paper-space layouts entirely."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    mc = doc.modelspace().add_circle((1.0, 1.0), 0.5)
    psp = doc.paperspace("Layout1")
    pline = psp.add_line((0, 0), (10, 0))
    path = tmp_path / "both.dxf"
    doc.saveas(str(path))

    out = flatten_for_render(str(path))
    assert out.source_layout == "Model"
    assert out.source_is_paperspace is False
    got = {p.get("handle") for p in out.primitives}
    assert mc.dxf.handle in got
    assert pline.dxf.handle not in got  # paper-space line is NOT rendered


def test_enumerate_layouts_lists_tabs_with_counts(tmp_path):
    path, _ = _paperspace_dxf(tmp_path, layouts={"Layout1": 2, "Layout2": 9})
    inv = enumerate_layouts(str(path))
    by_name = {L["name"]: L for L in inv}
    assert by_name["Model"]["entity_count"] == 0
    assert by_name["Model"]["is_paperspace"] is False
    assert by_name["Layout1"]["entity_count"] == 2
    assert by_name["Layout1"]["is_paperspace"] is True
    assert by_name["Layout2"]["entity_count"] == 9


def test_truly_empty_file_behaves_like_before(tmp_path):
    """No geometry anywhere → empty result, modelspace source (the historical
    behaviour), no raise."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    path = tmp_path / "empty.dxf"
    doc.saveas(str(path))
    out = flatten_for_render(str(path))
    assert out.primitives == []
    assert out.source_layout == "Model"
    assert out.source_is_paperspace is False


def test_viewport_only_tab_is_not_counted_as_content(tmp_path):
    """A paper-space tab holding only a VIEWPORT (AutoCAD's framing/title-
    block tab — present in essentially every DWG export) must read as empty,
    so it neither out-ranks a real tab nor trips the picker."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    psp1 = doc.paperspace("Layout1")
    for i in range(4):
        psp1.add_circle((2 + i * 2, 5), 0.5)
    doc.layouts.new("Layout2")
    psp2 = doc.paperspace("Layout2")
    psp2.add_viewport(center=(400, 300), size=(800, 600),
                      view_center_point=(0, 0), view_height=600)
    path = tmp_path / "real_plus_viewport.dxf"
    doc.saveas(str(path))

    inv = {L["name"]: L for L in enumerate_layouts(str(path))}
    assert inv["Layout1"]["entity_count"] == 4
    assert inv["Layout2"]["entity_count"] == 0  # viewport excluded

    # Auto-resolution renders the real tab, never the viewport-only one.
    out = flatten_for_render(str(path))
    assert out.source_layout == "Layout1"
    assert out.primitives


def test_resolve_ignores_viewport_heavy_tab(tmp_path):
    """Ranking is by renderable entities: a tab with many viewports but no
    geometry must lose to a tab with a little real geometry."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    psp1 = doc.paperspace("Layout1")
    psp1.add_line((0, 0), (1, 0))  # one real entity
    doc.layouts.new("Layout2")
    psp2 = doc.paperspace("Layout2")
    for _ in range(5):  # five viewports, zero geometry
        psp2.add_viewport(center=(400, 300), size=(800, 600),
                          view_center_point=(0, 0), view_height=600)
    path = tmp_path / "vp_heavy.dxf"
    doc.saveas(str(path))

    out = flatten_for_render(str(path))
    assert out.source_layout == "Layout1"  # not the viewport-heavy Layout2


def test_bundled_sample_still_renders_from_model(test_dxf_path):
    """The bundled sample's geometry is in model space; the feature must not
    change how it resolves."""
    out = flatten_for_render(str(test_dxf_path))
    assert out.source_layout == "Model"
    assert out.source_is_paperspace is False
    assert out.primitives

"""resolve_views — per-(product, role) view resolution."""

from __future__ import annotations

import pytest

from app.files import FileRecord
from app.product_views import (
    ViewCoverageConflict,
    ViewSource,
    resolve_views,
)


def _rec(
    id: str,
    *,
    dxf_view: str | None,
    top: dict | None = None,
    bottom: dict | None = None,
    side: dict | None = None,
) -> FileRecord:
    return FileRecord(
        id=id,
        name=f"{id}.dxf",
        size=1,
        uploaded_at=0.0,
        status="ready_to_match",
        product_id="p1",
        dxf_role="SBT",
        dxf_view=dxf_view,
        top_view_rect=top,
        bottom_view_rect=bottom,
        side_view_rect=side,
    )


RECT_A = {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
RECT_B = {"x0": 20.0, "y0": 0.0, "x1": 30.0, "y1": 10.0}
RECT_C = {"x0": 0.0, "y0": 20.0, "x1": 10.0, "y1": 30.0}


def test_empty_input_returns_empty_mapping():
    assert resolve_views([]) == {}


def test_multi_file_with_all_three_rects():
    rec = _rec("f1", dxf_view="multi", top=RECT_A, bottom=RECT_B, side=RECT_C)
    out = resolve_views([rec])
    assert out == {
        "top":    ViewSource(file_id="f1", source="region", rect=RECT_A),
        "bottom": ViewSource(file_id="f1", source="region", rect=RECT_B),
        "side":   ViewSource(file_id="f1", source="region", rect=RECT_C),
    }


def test_multi_file_with_partial_coverage():
    """A multi file may have only some view rects set."""
    rec = _rec("f1", dxf_view="multi", top=RECT_A)
    out = resolve_views([rec])
    assert set(out.keys()) == {"top"}
    assert out["top"].source == "region"


def test_multi_plus_single_view_split():
    multi = _rec("f1", dxf_view="multi", top=RECT_A, bottom=RECT_B)
    side_file = _rec("f2", dxf_view="side")
    out = resolve_views([multi, side_file])
    assert set(out.keys()) == {"top", "bottom", "side"}
    assert out["top"].source == "region" and out["top"].file_id == "f1"
    assert out["bottom"].source == "region" and out["bottom"].file_id == "f1"
    assert out["side"].source == "whole_file" and out["side"].file_id == "f2"


def test_all_three_views_as_split_files():
    top = _rec("ftop", dxf_view="top")
    bot = _rec("fbot", dxf_view="bottom")
    side = _rec("fside", dxf_view="side")
    out = resolve_views([top, bot, side])
    assert {v: s.source for v, s in out.items()} == {
        "top": "whole_file",
        "bottom": "whole_file",
        "side": "whole_file",
    }


def test_missing_view_is_allowed():
    """One product may not have a 'side' view at all — that's not an error."""
    rec = _rec("f1", dxf_view="multi", top=RECT_A, bottom=RECT_B)
    out = resolve_views([rec])
    assert "side" not in out


def test_conflict_multi_region_and_split_file_for_same_view():
    multi = _rec("f1", dxf_view="multi", top=RECT_A)
    split = _rec("f2", dxf_view="top")
    with pytest.raises(ViewCoverageConflict) as excinfo:
        resolve_views([multi, split])
    assert excinfo.value.view == "top"
    assert set(excinfo.value.file_ids) == {"f1", "f2"}


def test_conflict_two_split_files_same_view():
    a = _rec("f1", dxf_view="top")
    b = _rec("f2", dxf_view="top")
    with pytest.raises(ViewCoverageConflict) as excinfo:
        resolve_views([a, b])
    assert excinfo.value.view == "top"
    assert set(excinfo.value.file_ids) == {"f1", "f2"}


def test_legacy_null_view_treated_as_multi():
    """Pre-migration rows have dxf_view=None — resolver treats them as multi."""
    rec = _rec("legacy", dxf_view=None, top=RECT_A)
    out = resolve_views([rec])
    assert out["top"].file_id == "legacy"
    assert out["top"].source == "region"


def test_resolve_for_product_integration(tmp_db, monkeypatch):
    """End-to-end via the FILE_STORE: register a mixed set and confirm
    resolve_for_product returns the right per-view source."""
    import app.product_views as pv
    from app.files import FileStore

    fs = FileStore(tmp_db)
    monkeypatch.setattr(pv, "FILE_STORE", fs)

    fs.register("multi1", "m.dxf", 1, product_id="prod1",
                dxf_role="SBT", dxf_view="multi")
    fs.update_side_regions("multi1", top_view_rect=RECT_A,
                            bottom_view_rect=None, side_view_rect=None)
    fs.register("split-side", "s.dxf", 1, product_id="prod1",
                dxf_role="SBT", dxf_view="side")

    out = pv.resolve_for_product("prod1", "SBT")
    assert set(out.keys()) == {"top", "side"}
    assert out["top"].source == "region" and out["top"].file_id == "multi1"
    assert out["side"].source == "whole_file" and out["side"].file_id == "split-side"
    # bottom is not covered by either file — and that's OK.
    assert "bottom" not in out

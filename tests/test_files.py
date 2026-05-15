"""FileStore CRUD + lifecycle status transitions."""

from __future__ import annotations

import pytest

from app.files import (
    ERROR,
    FileStore,
    PREPROCESSING,
    READY,
    compute_unit_scale_warning,
)


def test_register_and_get(tmp_db):
    fs = FileStore(tmp_db)
    rec = fs.register("abc123", "foo.dxf", 100_000)
    assert rec.status == PREPROCESSING
    got = fs.get("abc123")
    assert got is not None
    assert got.name == "foo.dxf"
    assert got.size == 100_000


def test_update_parsed_moves_to_ready(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("abc", "a.dxf", 1)
    fs.update_parsed("abc", primitive_count=42,
                      bbox=(0.0, 0.0, 10.0, 10.0), background="#fff",
                      insunits=4)
    rec = fs.get("abc")
    assert rec.status == READY
    assert rec.primitive_count == 42
    assert rec.bbox == (0.0, 0.0, 10.0, 10.0)
    assert rec.background == "#fff"
    assert rec.insunits == 4


def test_update_parsed_omits_insunits(tmp_db):
    """`insunits` is optional — legacy callers (and unit tests in other
    files that haven't been updated) must still work; the column is set
    to NULL."""
    fs = FileStore(tmp_db)
    fs.register("legacy", "l.dxf", 1)
    fs.update_parsed("legacy", primitive_count=1,
                      bbox=(0.0, 0.0, 1.0, 1.0), background="#000")
    rec = fs.get("legacy")
    assert rec.insunits is None
    # Legacy record → to_dict reports no warning (NULL insunits + tiny bbox).
    d = rec.to_dict()
    assert d["unit_scale_warning"] is None


@pytest.mark.parametrize("insunits,bbox,expected_kind", [
    # No bbox yet → no warning regardless.
    (4,    None,                          None),
    # Tiny bboxes are always fine.
    (0,    (0, 0, 50, 50),                "unitless"),     # diagonal ~70 > 100? actually ~70.7 < 100 → mild
    # Wait — 50x50 diagonal = sqrt(5000) ≈ 70.7. Below 100. So this case:
    # insunits=0, diagonal<100 → "unitless" kind. Keep parametrization
    # mapped to the diagonal-aware table.
    # Normal packaging file: mm, diagonal 300 → no warning.
    (4,    (0, 0, 200, 200),              None),           # diagonal ~283
    # Declared mm but bbox enormous → suspect.
    (4,    (0, 0, 30_000, 30_000),        "suspect_scale"),
    # Unitless, mid-large diagonal → suspect.
    (0,    (0, 0, 500, 500),              "suspect_scale"),  # diagonal ~707 > 100
    # Unitless, tiny bbox → mild "unitless".
    (0,    (0, 0, 30, 30),                "unitless"),       # diagonal ~42 < 100
    # Legacy NULL insunits, large bbox → suspect.
    (None, (0, 0, 30_000, 30_000),        "suspect_scale"),
    # Legacy NULL insunits, mid bbox → no warning (we don't speculate).
    (None, (0, 0, 200, 200),              None),
])
def test_unit_scale_warning_heuristic(insunits, bbox, expected_kind):
    kind, detail = compute_unit_scale_warning(insunits, bbox)
    assert kind == expected_kind
    if kind:
        # Detail should mention both the raw INSUNITS value and the diagonal.
        assert "INSUNITS" in detail
        assert "diagonal" in detail


def test_update_status_error(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("xyz", "x.dxf", 1)
    fs.update_status("xyz", ERROR, error="boom")
    rec = fs.get("xyz")
    assert rec.status == ERROR
    assert rec.error == "boom"


def test_list_all_ordered_by_upload_time(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("first", "1.dxf", 1)
    fs.register("second", "2.dxf", 1)
    listed = fs.list_all()
    # DESC by uploaded_at — most-recent first.
    assert listed[0].id == "second"
    assert listed[1].id == "first"


def test_register_idempotent_overwrites(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("dup", "v1.dxf", 100)
    fs.register("dup", "v2.dxf", 200)
    rec = fs.get("dup")
    assert rec.name == "v2.dxf"
    assert rec.size == 200


def test_to_dict_round_trip(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("k", "n.dxf", 5)
    fs.update_parsed("k", 1, (0, 1, 2, 3), "#000000")
    d = fs.get("k").to_dict()
    assert d["status"] == READY
    assert d["bbox"] == [0, 1, 2, 3]


def test_side_regions_persist_and_round_trip(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("r1", "n.dxf", 5)
    fs.update_side_regions(
        "r1",
        {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0},
        {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0},
    )
    rec = fs.get("r1")
    assert rec.frontside_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert rec.bottomside_rect == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
    d = rec.to_dict()
    assert d["frontside_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert d["bottomside_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}


def test_side_regions_clear_one_independently(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("r2", "n.dxf", 5)
    fs.update_side_regions(
        "r2",
        {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        {"x0": 5, "y0": 5, "x1": 6, "y1": 6},
    )
    # Pass None for frontside, keep bottomside.
    fs.update_side_regions("r2", None, {"x0": 5, "y0": 5, "x1": 6, "y1": 6})
    rec = fs.get("r2")
    assert rec.frontside_rect is None
    assert rec.bottomside_rect == {"x0": 5.0, "y0": 5.0, "x1": 6.0, "y1": 6.0}


def test_clear_side_regions_unsets_both(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("r3", "n.dxf", 5)
    fs.update_side_regions(
        "r3",
        {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        {"x0": 5, "y0": 5, "x1": 6, "y1": 6},
    )
    fs.clear_side_regions("r3")
    rec = fs.get("r3")
    assert rec.frontside_rect is None
    assert rec.bottomside_rect is None


def test_library_swap_preserves_side_regions(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("r4", "n.dxf", 5, library_id="A")
    fs.update_side_regions(
        "r4",
        {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        None,
    )
    fs.update_library("r4", "B")
    rec = fs.get("r4")
    assert rec.library_id == "B"
    assert rec.frontside_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert rec.bottomside_rect is None

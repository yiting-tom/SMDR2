"""FileStore CRUD + lifecycle status transitions."""

from __future__ import annotations

import pytest

from app.files import (
    ERROR,
    FileStore,
    PREPROCESSING,
    READY,
    compute_unit_scale_warning,
    format_applied_scale_label,
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


# ---- applied_scale column + payload --------------------------------------
def test_fresh_db_has_applied_scale_column_default_1(tmp_db):
    fs = FileStore(tmp_db)
    cols = [r["name"] for r in fs.conn.execute("PRAGMA table_info(files)")]
    assert "applied_scale" in cols
    fs.register("a", "a.dxf", 1)
    rec = fs.get("a")
    # Default 1.0 for any row that hasn't been re-preprocessed yet.
    assert rec.applied_scale == 1.0
    assert rec.to_dict()["applied_scale"] == 1.0


def test_update_parsed_persists_applied_scale_and_reports_change(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("b", "b.dxf", 1)
    # First preprocess: factor 1.0, no change (prior default is also 1.0).
    changed = fs.update_parsed("b", 1, (0, 0, 1, 1), "#000",
                               insunits=4, applied_scale=1.0)
    assert changed is False
    # Second preprocess: factor flips to 0.001 — caller invalidates match.
    changed = fs.update_parsed("b", 1, (0, 0, 1, 1), "#000",
                               insunits=0, applied_scale=0.001)
    assert changed is True
    rec = fs.get("b")
    assert rec.applied_scale == pytest.approx(0.001)


def test_to_dict_rescaled_payload(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("c", "c.dxf", 1)
    # Persisted (post-rescale) bbox in mm; applied_scale records the
    # multiplier we applied (0.001 = was 1000× too big).
    fs.update_parsed("c", 1, (0, 0, 42, 42), "#000",
                     insunits=0, applied_scale=0.001)
    d = fs.get("c").to_dict()
    assert d["applied_scale"] == pytest.approx(0.001)
    assert d["applied_scale_label"] == "÷1000"
    # The detail string spells out both the source units and the factor.
    assert d["unit_scale_warning_detail"]
    assert "0.001" in d["unit_scale_warning_detail"] or "÷1000" in d["unit_scale_warning_detail"]
    assert "INSUNITS=0" in d["unit_scale_warning_detail"]


def test_to_dict_inch_rescaled_payload(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("d", "d.dxf", 1)
    # 10-inch design → 254 mm after the inch → mm rescale.
    fs.update_parsed("d", 1, (0, 0, 254, 254), "#000",
                     insunits=1, applied_scale=25.4)
    d = fs.get("d").to_dict()
    assert d["applied_scale"] == pytest.approx(25.4)
    assert d["applied_scale_label"] == "×25.4 (inch)"
    assert "INSUNITS=1" in d["unit_scale_warning_detail"]
    assert "inch" in d["unit_scale_warning_detail"]


def test_to_dict_not_rescaled_payload_unchanged(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("e", "e.dxf", 1)
    fs.update_parsed("e", 1, (0, 0, 300, 300), "#000",
                     insunits=4, applied_scale=1.0)
    d = fs.get("e").to_dict()
    assert d["applied_scale"] == 1.0
    assert d["applied_scale_label"] is None
    # mm + diagonal ~424 is a normal design → no warning, no detail.
    assert d["unit_scale_warning"] is None
    assert d["unit_scale_warning_detail"] is None


@pytest.mark.parametrize("factor,insunits,expected", [
    (0.001, 0,    "÷1000"),
    (0.01,  0,    "÷100"),
    (0.1,   0,    "÷10"),
    (10.0,  5,    "×10"),
    (100.0, 0,    "×100"),
    (1000.0, 6,   "×1000"),
    (25.4,  1,    "×25.4 (inch)"),
])
def test_format_applied_scale_label(factor, insunits, expected):
    assert format_applied_scale_label(factor, insunits) == expected


def test_format_applied_scale_label_no_op():
    assert format_applied_scale_label(1.0, 4) == ""


def test_legacy_db_alter_adds_applied_scale_with_default(tmp_path):
    """Open a pre-auto-rescale DB and confirm the ALTER fires and old data
    survives."""
    import json as _json
    import sqlite3
    db_path = tmp_path / "legacy_no_applied_scale.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE files (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            size            INTEGER NOT NULL,
            uploaded_at     REAL NOT NULL,
            status          TEXT NOT NULL,
            error           TEXT,
            parsed_at       REAL,
            primitive_count INTEGER,
            bbox_xmin       REAL,
            bbox_ymin       REAL,
            bbox_xmax       REAL,
            bbox_ymax       REAL,
            background      TEXT,
            library_id      TEXT NOT NULL DEFAULT 'default',
            product_id      TEXT,
            dxf_role        TEXT,
            dxf_view        TEXT,
            match_saved     INTEGER NOT NULL DEFAULT 0,
            selected_layers TEXT,
            top_view_rect   TEXT,
            bottom_view_rect TEXT,
            side_view_rect  TEXT,
            insunits        INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO files (id, name, size, uploaded_at, status, insunits, "
        "bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy-x", "x.dxf", 1, 0.0, "ready_to_match", 0,
         0.0, 0.0, 42_000.0, 42_000.0),
    )
    conn.commit()
    conn.close()
    fs = FileStore(db_path)
    cols = [r["name"] for r in fs.conn.execute("PRAGMA table_info(files)")]
    assert "applied_scale" in cols
    rec = fs.get("legacy-x")
    # Default fills in as 1.0 — the file hasn't been re-preprocessed yet.
    assert rec.applied_scale == 1.0
    # Existing bbox + insunits survived the ALTER.
    assert rec.insunits == 0
    assert rec.bbox == (0.0, 0.0, 42_000.0, 42_000.0)


def test_side_regions_persist_and_round_trip(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("r1", "n.dxf", 5)
    fs.update_side_regions(
        "r1",
        {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0},
        {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0},
        {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0},
    )
    rec = fs.get("r1")
    assert rec.top_view_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert rec.bottom_view_rect == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
    assert rec.side_view_rect == {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0}
    d = rec.to_dict()
    assert d["top_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert d["bottom_view_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
    assert d["side_view_rect"] == {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0}


def test_side_regions_clear_one_independently(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("r2", "n.dxf", 5)
    fs.update_side_regions(
        "r2",
        {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        {"x0": 5, "y0": 5, "x1": 6, "y1": 6},
        {"x0": 9, "y0": 9, "x1": 10, "y1": 10},
    )
    # Clear top_view; keep bottom_view and side_view.
    fs.update_side_regions(
        "r2",
        None,
        {"x0": 5, "y0": 5, "x1": 6, "y1": 6},
        {"x0": 9, "y0": 9, "x1": 10, "y1": 10},
    )
    rec = fs.get("r2")
    assert rec.top_view_rect is None
    assert rec.bottom_view_rect == {"x0": 5.0, "y0": 5.0, "x1": 6.0, "y1": 6.0}
    assert rec.side_view_rect == {"x0": 9.0, "y0": 9.0, "x1": 10.0, "y1": 10.0}


def test_side_regions_only_side_view_set(tmp_db):
    # The "any combination" requirement: it's valid to set only side_view.
    fs = FileStore(tmp_db)
    fs.register("r2b", "n.dxf", 5)
    fs.update_side_regions(
        "r2b",
        None,
        None,
        {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
    )
    rec = fs.get("r2b")
    assert rec.top_view_rect is None
    assert rec.bottom_view_rect is None
    assert rec.side_view_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}


def test_clear_side_regions_unsets_all(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("r3", "n.dxf", 5)
    fs.update_side_regions(
        "r3",
        {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        {"x0": 5, "y0": 5, "x1": 6, "y1": 6},
        {"x0": 9, "y0": 9, "x1": 10, "y1": 10},
    )
    fs.clear_side_regions("r3")
    rec = fs.get("r3")
    assert rec.top_view_rect is None
    assert rec.bottom_view_rect is None
    assert rec.side_view_rect is None


def test_library_swap_preserves_side_regions(tmp_db):
    fs = FileStore(tmp_db)
    fs.register("r4", "n.dxf", 5, library_id="A")
    fs.update_side_regions(
        "r4",
        {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        None,
        {"x0": 20, "y0": 20, "x1": 30, "y1": 30},
    )
    fs.update_library("r4", "B")
    rec = fs.get("r4")
    assert rec.library_id == "B"
    assert rec.top_view_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert rec.bottom_view_rect is None
    assert rec.side_view_rect == {"x0": 20.0, "y0": 20.0, "x1": 30.0, "y1": 30.0}


def test_migration_renames_old_columns_and_adds_side_view(tmp_path):
    """A DB created under the pre-rename schema gets migrated on next open:
    frontside_rect → top_view_rect, bottomside_rect → bottom_view_rect,
    plus a new side_view_rect column. Existing values survive the rename."""
    import json as _json
    import sqlite3
    db_path = tmp_path / "legacy.sqlite"
    # Build the pre-rename schema by hand, mirroring the columns that
    # existed before this change. Only the bits we touch matter.
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE files (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            size            INTEGER NOT NULL,
            uploaded_at     REAL NOT NULL,
            status          TEXT NOT NULL,
            error           TEXT,
            parsed_at       REAL,
            primitive_count INTEGER,
            bbox_xmin       REAL,
            bbox_ymin       REAL,
            bbox_xmax       REAL,
            bbox_ymax       REAL,
            background      TEXT,
            library_id      TEXT NOT NULL DEFAULT 'default',
            product_id      TEXT,
            dxf_role        TEXT,
            match_saved     INTEGER NOT NULL DEFAULT 0,
            selected_layers TEXT,
            frontside_rect  TEXT,
            bottomside_rect TEXT,
            insunits        INTEGER
        );
        """
    )
    conn.execute(
        "INSERT INTO files (id, name, size, uploaded_at, status, frontside_rect, bottomside_rect) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-1", "x.dxf", 1, 0.0, "ready_to_match",
            _json.dumps({"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}),
            _json.dumps({"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}),
        ),
    )
    conn.commit()
    conn.close()

    # Re-open through FileStore — _migrate() runs and renames the columns.
    fs = FileStore(db_path)
    cols = [r["name"] for r in fs.conn.execute("PRAGMA table_info(files)")]
    assert "top_view_rect" in cols
    assert "bottom_view_rect" in cols
    assert "side_view_rect" in cols
    assert "frontside_rect" not in cols
    assert "bottomside_rect" not in cols
    rec = fs.get("legacy-1")
    assert rec.top_view_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert rec.bottom_view_rect == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
    assert rec.side_view_rect is None

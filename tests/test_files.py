"""FileStore content rows + version bindings + lifecycle status transitions.

Versioned model (2026-06-10): `files` is pure content-addressed storage;
all lifecycle state lives on the `version_files` binding, keyed by
(version_id, file_id). Tests bind into a literal version id — the
binding table has no FK on versions, so FileStore can be exercised in
isolation.
"""

from __future__ import annotations

import time

import pytest

from app.files import (
    ERROR,
    FileStore,
    PREPROCESSING,
    READY,
    compute_unit_scale_warning,
    format_applied_scale_label,
    unit_display,
)


VID = "v-test"


def _bind(fs, fid, name="f.dxf", size=1, role="SBT", vid=VID, **kw):
    """Register the content row then bind it into a version's role."""
    fs.register_content(fid, name, size)
    return fs.bind(vid, role, fid, **kw)


def test_bind_and_get(tmp_db):
    fs = FileStore(tmp_db)
    rec = _bind(fs, "abc123", "foo.dxf", 100_000)
    assert rec.status == PREPROCESSING
    got = fs.get(VID, "abc123")
    assert got is not None
    assert got.name == "foo.dxf"
    assert got.size == 100_000
    assert got.version_id == VID
    assert got.dxf_role == "SBT"


def test_update_parsed_moves_to_ready(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "abc", "a.dxf", 1)
    fs.update_parsed(VID, "abc", primitive_count=42,
                     bbox=(0.0, 0.0, 10.0, 10.0), background="#fff",
                     insunits=4)
    rec = fs.get(VID, "abc")
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
    _bind(fs, "legacy", "l.dxf", 1)
    fs.update_parsed(VID, "legacy", primitive_count=1,
                     bbox=(0.0, 0.0, 1.0, 1.0), background="#000")
    rec = fs.get(VID, "legacy")
    assert rec.insunits is None
    # Legacy record → to_dict reports no warning (NULL insunits + tiny bbox).
    d = rec.to_dict()
    assert d["unit_scale_warning"] is None


# Unit-scale warnings were removed on 2026-06-09 — every file is treated
# as mm as-authored, so the warning is never raised for any INSUNITS/bbox.
@pytest.mark.parametrize("insunits,bbox", [
    (4,    None),
    (0,    (0, 0, 50, 50)),
    (4,    (0, 0, 200, 200)),
    (4,    (0, 0, 30_000, 30_000)),     # would have been "suspect_scale"
    (0,    (0, 0, 500, 500)),           # would have been "suspect_scale"
    (0,    (0, 0, 30, 30)),             # would have been "unitless"
    (None, (0, 0, 30_000, 30_000)),     # would have been "suspect_scale"
])
def test_unit_scale_warning_always_none(insunits, bbox):
    kind, detail = compute_unit_scale_warning(insunits, bbox)
    assert kind is None
    assert detail == ""


@pytest.mark.parametrize("insunits,override,exp_label,exp_is_mm", [
    (4,    None,   "mm",       True),    # declared mm
    (1,    None,   "inch",     False),   # declared inch → warn on dashboard
    (5,    None,   "cm",       False),
    (6,    None,   "m",        False),
    (0,    None,   "unitless", False),
    (2,    None,   "foot",     False),   # declared foot
    (3,    None,   "未指定",    False),   # unmapped INSUNITS → unspecified
    (None, None,   "未指定",    False),   # legacy / missing header
    (0,    "inch", "inch",     False),   # operator override is authoritative
    (1,    "mm",   "mm",       True),    # override to mm wins over INSUNITS=inch
])
def test_unit_display(insunits, override, exp_label, exp_is_mm):
    label, is_mm = unit_display(insunits, override)
    assert label == exp_label
    assert is_mm is exp_is_mm


def test_to_dict_exposes_unit_fields(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "u", "u.dxf", 1)
    fs.update_parsed(VID, "u", 1, (0, 0, 254, 254), "#000",
                     insunits=1, applied_scale=25.4)
    fs.set_user_unit_override(VID, "u", "inch")
    d = fs.get(VID, "u").to_dict()
    assert d["unit_label"] == "inch"
    assert d["unit_is_mm"] is False


def test_update_status_error(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "xyz", "x.dxf", 1)
    fs.update_status(VID, "xyz", ERROR, error="boom")
    rec = fs.get(VID, "xyz")
    assert rec.status == ERROR
    assert rec.error == "boom"


def test_list_all_ordered_by_upload_time(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "first", "1.dxf", 1)
    time.sleep(0.01)  # ensure strictly increasing uploaded_at
    _bind(fs, "second", "2.dxf", 1)
    listed = fs.list_all()
    # DESC by uploaded_at — most-recent first.
    assert listed[0].id == "second"
    assert listed[1].id == "first"


def test_register_content_idempotent_first_wins(tmp_db):
    """Content rows are keyed by hash: a re-upload of identical bytes is a
    no-op and the first uploader's filename/size win (the old register()
    overwrite semantics died with the versioned model)."""
    fs = FileStore(tmp_db)
    fs.register_content("dup", "v1.dxf", 100)
    fs.register_content("dup", "v2.dxf", 200)
    fs.bind(VID, "SBT", "dup")
    rec = fs.get(VID, "dup")
    assert rec.name == "v1.dxf"
    assert rec.size == 100


def test_bind_replaces_existing_binding(tmp_db):
    """Re-binding the same (version, file) replaces the binding row and
    resets its lifecycle."""
    fs = FileStore(tmp_db)
    _bind(fs, "rb", "r.dxf", 1)
    fs.update_parsed(VID, "rb", 1, (0, 0, 1, 1), "#000")
    assert fs.get(VID, "rb").status == READY
    fs.bind(VID, "BD", "rb")
    rec = fs.get(VID, "rb")
    assert rec.status == PREPROCESSING
    assert rec.dxf_role == "BD"


def test_to_dict_round_trip(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "k", "n.dxf", 5)
    fs.update_parsed(VID, "k", 1, (0, 1, 2, 3), "#000000")
    d = fs.get(VID, "k").to_dict()
    assert d["status"] == READY
    assert d["bbox"] == [0, 1, 2, 3]
    assert d["version_id"] == VID


# ---- applied_scale column + payload --------------------------------------
def test_fresh_db_has_applied_scale_column_default_1(tmp_db):
    fs = FileStore(tmp_db)
    cols = [r["name"] for r in fs.conn.execute("PRAGMA table_info(version_files)")]
    assert "applied_scale" in cols
    _bind(fs, "a", "a.dxf", 1)
    rec = fs.get(VID, "a")
    # Default 1.0 for any binding that hasn't been re-preprocessed yet.
    assert rec.applied_scale == 1.0
    assert rec.to_dict()["applied_scale"] == 1.0


def test_update_parsed_persists_applied_scale_and_reports_change(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "b", "b.dxf", 1)
    # First preprocess: factor 1.0, no change (prior default is also 1.0).
    changed = fs.update_parsed(VID, "b", 1, (0, 0, 1, 1), "#000",
                               insunits=4, applied_scale=1.0)
    assert changed is False
    # Second preprocess: factor flips to 0.001 — caller invalidates match.
    changed = fs.update_parsed(VID, "b", 1, (0, 0, 1, 1), "#000",
                               insunits=0, applied_scale=0.001)
    assert changed is True
    rec = fs.get(VID, "b")
    assert rec.applied_scale == pytest.approx(0.001)


def test_to_dict_rescaled_payload(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "c", "c.dxf", 1)
    # Persisted (post-rescale) bbox in mm; applied_scale records the
    # multiplier we applied (0.001 = was 1000× too big).
    fs.update_parsed(VID, "c", 1, (0, 0, 42, 42), "#000",
                     insunits=0, applied_scale=0.001)
    d = fs.get(VID, "c").to_dict()
    assert d["applied_scale"] == pytest.approx(0.001)
    assert d["applied_scale_label"] == "÷1000"
    # The detail string spells out both the source units and the factor.
    assert d["unit_scale_warning_detail"]
    assert "0.001" in d["unit_scale_warning_detail"] or "÷1000" in d["unit_scale_warning_detail"]
    assert "INSUNITS=0" in d["unit_scale_warning_detail"]


def test_to_dict_inch_rescaled_payload(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "d", "d.dxf", 1)
    # 10-inch design → 254 mm after the inch → mm rescale.
    fs.update_parsed(VID, "d", 1, (0, 0, 254, 254), "#000",
                     insunits=1, applied_scale=25.4)
    d = fs.get(VID, "d").to_dict()
    assert d["applied_scale"] == pytest.approx(25.4)
    assert d["applied_scale_label"] == "×25.4 (inch)"
    assert "INSUNITS=1" in d["unit_scale_warning_detail"]
    assert "inch" in d["unit_scale_warning_detail"]


def test_to_dict_not_rescaled_payload_unchanged(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "e", "e.dxf", 1)
    fs.update_parsed(VID, "e", 1, (0, 0, 300, 300), "#000",
                     insunits=4, applied_scale=1.0)
    d = fs.get(VID, "e").to_dict()
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


# ---- legacy-schema handling ------------------------------------------------
# Removed tests (two-tier scope removed 2026-06-10, openspec
# add-product-versioning) — in-place ALTER migrations no longer exist;
# a pre-versioning DB is dropped and rebuilt from scratch by
# app.dbschema.ensure_versioned_schema (decision C9: no data preserved):
# - test_legacy_db_alter_adds_applied_scale_with_default
# - test_migration_renames_old_columns_and_adds_side_view
# - test_legacy_db_alter_adds_user_unit_override
# - test_legacy_db_alter_adds_dxf_recover_notes_column
# The rebuild semantics they all converge to is asserted below.
def test_legacy_db_is_rebuilt_from_scratch(tmp_path):
    """Open a pre-versioning DB (files table with library_id/product_id,
    pre-rename rect columns, no versions table) through FileStore: the
    schema guard drops every known table and the store recreates the
    versioned shape. Legacy rows do NOT survive."""
    import sqlite3
    db_path = tmp_path / "legacy_pre_versioning.sqlite"
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
        "INSERT INTO files (id, name, size, uploaded_at, status, insunits) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("legacy-x", "x.dxf", 1, 0.0, "ready_to_match", 0),
    )
    conn.commit()
    conn.close()

    fs = FileStore(db_path)
    # Rebuilt content schema: lifecycle columns gone, content columns present.
    cols = [r["name"] for r in fs.conn.execute("PRAGMA table_info(files)")]
    assert "product_id" not in cols
    assert "library_id" not in cols
    assert "dxf_recover_notes" in cols
    # The binding table exists with the per-binding state columns.
    vf_cols = [r["name"] for r in fs.conn.execute("PRAGMA table_info(version_files)")]
    for c in ("applied_scale", "user_unit_override", "top_view_rect",
              "bottom_view_rect", "side_view_rect", "chosen_layout"):
        assert c in vf_cols
    # No legacy data preserved.
    assert fs.content_exists("legacy-x") is False
    assert fs.list_all() == []


def test_side_regions_persist_and_round_trip(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "r1", "n.dxf", 5)
    fs.update_side_regions(
        VID,
        "r1",
        {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0},
        {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0},
        {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0},
    )
    rec = fs.get(VID, "r1")
    assert rec.top_view_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert rec.bottom_view_rect == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
    assert rec.side_view_rect == {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0}
    d = rec.to_dict()
    assert d["top_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert d["bottom_view_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
    assert d["side_view_rect"] == {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0}


def test_side_regions_clear_one_independently(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "r2", "n.dxf", 5)
    fs.update_side_regions(
        VID,
        "r2",
        {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        {"x0": 5, "y0": 5, "x1": 6, "y1": 6},
        {"x0": 9, "y0": 9, "x1": 10, "y1": 10},
    )
    # Clear top_view; keep bottom_view and side_view.
    fs.update_side_regions(
        VID,
        "r2",
        None,
        {"x0": 5, "y0": 5, "x1": 6, "y1": 6},
        {"x0": 9, "y0": 9, "x1": 10, "y1": 10},
    )
    rec = fs.get(VID, "r2")
    assert rec.top_view_rect is None
    assert rec.bottom_view_rect == {"x0": 5.0, "y0": 5.0, "x1": 6.0, "y1": 6.0}
    assert rec.side_view_rect == {"x0": 9.0, "y0": 9.0, "x1": 10.0, "y1": 10.0}


def test_side_regions_only_side_view_set(tmp_db):
    # The "any combination" requirement: it's valid to set only side_view.
    fs = FileStore(tmp_db)
    _bind(fs, "r2b", "n.dxf", 5)
    fs.update_side_regions(
        VID,
        "r2b",
        None,
        None,
        {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
    )
    rec = fs.get(VID, "r2b")
    assert rec.top_view_rect is None
    assert rec.bottom_view_rect is None
    assert rec.side_view_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}


def test_clear_side_regions_unsets_all(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "r3", "n.dxf", 5)
    fs.update_side_regions(
        VID,
        "r3",
        {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
        {"x0": 5, "y0": 5, "x1": 6, "y1": 6},
        {"x0": 9, "y0": 9, "x1": 10, "y1": 10},
    )
    fs.clear_side_regions(VID, "r3")
    rec = fs.get(VID, "r3")
    assert rec.top_view_rect is None
    assert rec.bottom_view_rect is None
    assert rec.side_view_rect is None


def test_second_version_binding_does_not_share_side_regions(tmp_db):
    """update_library() died with the versioned model — the analogous
    invariant now: view rects are per (version, file) binding, so binding
    the same content into another version neither copies nor clobbers the
    first binding's rects."""
    fs = FileStore(tmp_db)
    fs.register_content("r4", "n.dxf", 5)
    fs.bind("v-A", "SBT", "r4")
    fs.update_side_regions(
        "v-A",
        "r4",
        {"x0": 0, "y0": 0, "x1": 10, "y1": 10},
        None,
        {"x0": 20, "y0": 20, "x1": 30, "y1": 30},
    )
    fs.bind("v-B", "SBT", "r4")
    rec_a = fs.get("v-A", "r4")
    assert rec_a.top_view_rect == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 10.0}
    assert rec_a.bottom_view_rect is None
    assert rec_a.side_view_rect == {"x0": 20.0, "y0": 20.0, "x1": 30.0, "y1": 30.0}
    rec_b = fs.get("v-B", "r4")
    assert rec_b.top_view_rect is None
    assert rec_b.bottom_view_rect is None
    assert rec_b.side_view_rect is None


# ---- user_unit_override column + persistence ------------------------------
def test_fresh_db_has_user_unit_override_column_default_null(tmp_db):
    fs = FileStore(tmp_db)
    cols = [r["name"] for r in fs.conn.execute("PRAGMA table_info(version_files)")]
    assert "user_unit_override" in cols
    _bind(fs, "a", "a.dxf", 1)
    rec = fs.get(VID, "a")
    assert rec.user_unit_override is None
    assert rec.to_dict()["user_unit_override"] is None


def test_set_user_unit_override_persists(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "b", "b.dxf", 1)
    fs.set_user_unit_override(VID, "b", "inch")
    rec = fs.get(VID, "b")
    assert rec.user_unit_override == "inch"
    assert rec.to_dict()["user_unit_override"] == "inch"


def test_set_user_unit_override_to_none_clears(tmp_db):
    fs = FileStore(tmp_db)
    _bind(fs, "c", "c.dxf", 1)
    fs.set_user_unit_override(VID, "c", "inch")
    fs.set_user_unit_override(VID, "c", None)
    rec = fs.get(VID, "c")
    assert rec.user_unit_override is None


# ---- dxf_recover_notes (recover-fallback persistence) -------------------
def test_dxf_recover_notes_default_null_on_fresh_register(tmp_db):
    """A freshly-bound FileRecord SHALL have `dxf_recover_notes == None`
    and the dashboard payload SHALL carry the same field as `None`."""
    fs = FileStore(tmp_db)
    _bind(fs, "rn-fresh", "f.dxf", 1)
    rec = fs.get(VID, "rn-fresh")
    assert rec.dxf_recover_notes is None
    assert rec.to_dict()["dxf_recover_notes"] is None


def test_dxf_recover_notes_round_trip(tmp_db):
    """`set_dxf_recover_notes(file_id, notes)` is content-level (no
    version); it persists JSON-encoded, `get()` decodes it back to a
    dict, and `to_dict()` exposes the same shape on every binding."""
    fs = FileStore(tmp_db)
    _bind(fs, "rn-round", "f.dxf", 1)
    notes = {
        "strict_error": "DXFStructureError: invalid header tag",
        "n_fixed": 12,
        "n_unrecoverable": 1,
        "audit_messages": ["msg #1", "msg #2"],
    }
    fs.set_dxf_recover_notes("rn-round", notes)
    rec = fs.get(VID, "rn-round")
    assert rec.dxf_recover_notes == notes
    assert rec.to_dict()["dxf_recover_notes"] == notes
    # Content-level: a second version's binding of the same bytes sees it too.
    fs.bind("v-other", "SBT", "rn-round")
    assert fs.get("v-other", "rn-round").dxf_recover_notes == notes
    # Clear it back to None.
    fs.set_dxf_recover_notes("rn-round", None)
    rec2 = fs.get(VID, "rn-round")
    assert rec2.dxf_recover_notes is None

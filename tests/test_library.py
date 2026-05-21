"""SQLite-backed library: persistence round-trip, CRUD, class ops."""

from __future__ import annotations

import pytest

from app.library import (
    CLASS_ARBITRATION_GROUPS,
    CLASS_JSON_KEY,
    CLASS_VIEW_CONSTRAINTS,
    DEFAULT_CLASSES,
    DEFAULT_LIBRARY_ID,
    ArbitrationGroup,
    Library,
    LibraryRegistry,
    MaxNeighbors,
    MinNeighbors,
    Store,
    Template,
    _build_arbitration_index,
    arbitration_group_for,
    is_allowed_view,
)


def _make_template(class_name="BGABall"):
    return Template.from_entities(
        class_name,
        [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]],
    )


def _default_lib(tmp_db):
    """Get the default library from a fresh registry on a tmp DB."""
    return LibraryRegistry(Store(tmp_db)).get(DEFAULT_LIBRARY_ID)


def test_store_creates_default_classes(tmp_db):
    lib = _default_lib(tmp_db)
    assert set(lib.classes) >= set(DEFAULT_CLASSES)
    for c in DEFAULT_CLASSES:
        assert lib.count(c) == 0


def test_add_template_persists(tmp_db):
    lib = _default_lib(tmp_db)
    t = _make_template("BGABall")
    lib.add_template(t)
    assert lib.count("BGABall") == 1

    # Re-open: template survives.
    lib2 = _default_lib(tmp_db)
    assert lib2.count("BGABall") == 1
    assert lib2.templates_of("BGABall")[0].id == t.id


def test_delete_template(tmp_db):
    lib = _default_lib(tmp_db)
    t = _make_template("SMD-2T")
    lib.add_template(t)
    assert lib.delete_template(t.id)
    assert lib.count("SMD-2T") == 0
    lib2 = _default_lib(tmp_db)
    assert lib2.count("SMD-2T") == 0


def test_delete_nonexistent_returns_false(tmp_db):
    lib = _default_lib(tmp_db)
    assert lib.delete_template("does-not-exist") is False


def test_move_template_changes_class_and_persists(tmp_db):
    lib = _default_lib(tmp_db)
    t = _make_template("BGABall")
    lib.add_template(t)
    assert lib.move_template(t.id, "SMD-2T")
    assert lib.count("BGABall") == 0
    assert lib.count("SMD-2T") == 1
    lib2 = _default_lib(tmp_db)
    assert lib2.count("BGABall") == 0
    assert lib2.count("SMD-2T") == 1


def test_add_custom_class(tmp_db):
    lib = _default_lib(tmp_db)
    lib.add_class("my_new_class")
    assert "my_new_class" in lib.classes
    lib2 = _default_lib(tmp_db)
    assert "my_new_class" in lib2.classes


def test_template_from_entities_validates_nonempty():
    import pytest
    with pytest.raises(ValueError):
        Template.from_entities("SMD-2T", [[]])


# ---- per-class match strategy + bbox_ratio -------------------------------
def test_new_class_defaults_to_chamfer(tmp_db):
    """Newly-seeded classes start at match_strategy='chamfer' / bbox_ratio=None
    so existing matching behavior is unchanged after upgrade."""
    lib = _default_lib(tmp_db)
    for c in DEFAULT_CLASSES:
        strategy, bbox_ratio = lib.strategy_of(c)
        assert strategy == "chamfer"
        assert bbox_ratio is None
    # summary surfaces both fields on every entry
    by_name = {e["name"]: e for e in lib.summary()}
    for c in DEFAULT_CLASSES:
        assert by_name[c]["match_strategy"] == "chamfer"
        assert by_name[c]["bbox_ratio"] is None


def test_set_strategy_round_trips_signature(tmp_db):
    lib = _default_lib(tmp_db)
    assert lib.set_strategy("Substrate", "signature", 0.05)
    assert lib.strategy_of("Substrate") == ("signature", 0.05)
    # Reload from store: value sticks.
    lib2 = _default_lib(tmp_db)
    assert lib2.strategy_of("Substrate") == ("signature", 0.05)


def test_set_strategy_unknown_class_returns_false(tmp_db):
    lib = _default_lib(tmp_db)
    assert lib.set_strategy("DoesNotExist", "signature", 0.05) is False


def test_migration_adds_strategy_columns(tmp_db):
    """Pre-change DB (classes table without match_strategy / bbox_ratio
    columns) gets both columns added on next Store() open. Existing rows
    end up at chamfer / NULL."""
    import sqlite3

    conn = sqlite3.connect(str(tmp_db))
    conn.executescript(
        """
        CREATE TABLE libraries (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            created_at  REAL NOT NULL
        );
        CREATE TABLE classes (
            library_id  TEXT NOT NULL,
            name        TEXT NOT NULL,
            rank        INTEGER NOT NULL,
            created_at  REAL NOT NULL,
            PRIMARY KEY (library_id, name)
        );
        CREATE TABLE templates (
            id                 TEXT PRIMARY KEY,
            library_id         TEXT NOT NULL,
            class_name         TEXT NOT NULL,
            entity_point_sets  TEXT NOT NULL,
            centroid_x         REAL NOT NULL,
            centroid_y         REAL NOT NULL,
            bbox_xmin          REAL NOT NULL,
            bbox_ymin          REAL NOT NULL,
            bbox_xmax          REAL NOT NULL,
            bbox_ymax          REAL NOT NULL,
            created_at         REAL NOT NULL
        );
        INSERT INTO libraries (id, name, created_at) VALUES ('default', 'Default', 0);
        INSERT INTO classes (library_id, name, rank, created_at)
            VALUES ('default', 'Substrate', 0, 0);
        """
    )
    conn.commit()
    conn.close()

    lib = _default_lib(tmp_db)
    cols = [
        r["name"]
        for r in lib.store.conn.execute("PRAGMA table_info(classes)")
    ]
    assert "match_strategy" in cols
    assert "bbox_ratio" in cols
    assert lib.strategy_of("Substrate") == ("chamfer", None)


def test_all_templates_returns_indexed_tuples(tmp_db):
    lib = _default_lib(tmp_db)
    t1 = _make_template("SMD-2T")
    t2 = _make_template("SMD-2T")
    t3 = _make_template("BGABall")
    for t in (t1, t2, t3):
        lib.add_template(t)
    flat = lib.all_templates()
    flat_by_id = {t.id: (c, i, t) for c, i, t in flat}
    assert flat_by_id[t1.id][0] == "SMD-2T"
    assert flat_by_id[t1.id][1] == 0
    assert flat_by_id[t2.id][1] == 1
    assert flat_by_id[t3.id][0] == "BGABall"
    assert flat_by_id[t3.id][1] == 0


# ---- multi-library specific tests -----------------------------------------
def test_multiple_libraries_isolated(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    lib_a = reg.get(DEFAULT_LIBRARY_ID)
    lib_b = reg.create("BGA Variants")

    t = _make_template("BGABall")
    lib_a.add_template(t)
    assert lib_a.count("BGABall") == 1
    assert lib_b.count("BGABall") == 0  # other library is independent

    # Reload and confirm separation persists.
    reg2 = LibraryRegistry(Store(tmp_db))
    assert reg2.get(DEFAULT_LIBRARY_ID).count("BGABall") == 1
    assert reg2.get(lib_b.library_id).count("BGABall") == 0


def test_cannot_delete_default_library(tmp_db):
    import pytest
    reg = LibraryRegistry(Store(tmp_db))
    with pytest.raises(ValueError):
        reg.delete(DEFAULT_LIBRARY_ID)


def test_create_library_seeds_default_classes(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    new_lib = reg.create("New Lib")
    assert set(new_lib.classes) >= set(DEFAULT_CLASSES)


# ---- C4Ball canonical-class ordering --------------------------------------
def test_c4ball_ordered_immediately_before_bgaball(tmp_db):
    """C4Ball and BGABall are both ball-type interconnect; the canonical
    order groups them together with C4Ball first."""
    lib = _default_lib(tmp_db)
    assert "C4Ball" in lib.classes
    c4_idx = lib.classes.index("C4Ball")
    bga_idx = lib.classes.index("BGABall")
    assert c4_idx + 1 == bga_idx, (
        f"expected C4Ball directly before BGABall, "
        f"got positions {c4_idx} and {bga_idx}"
    )


def test_c4ball_json_key_mapping():
    assert CLASS_JSON_KEY["C4Ball"] == "c4_ball"


def test_legacy_library_gets_c4ball_seeded_and_ranked(tmp_db):
    """A library that pre-dates the C4Ball addition (15 canonical classes,
    no C4Ball row) SHALL gain C4Ball on next Store boot and SHALL re-rank
    it to sit immediately before BGABall — mirrors the existing
    FiducialCircle/Cross seeding behavior."""
    import sqlite3
    import time

    # Hand-build a DB whose `default` library has every default class
    # EXCEPT C4Ball. Same shape as the live schema; we skip the C4Ball
    # row so the migration has work to do.
    conn = sqlite3.connect(str(tmp_db))
    conn.executescript(
        """
        CREATE TABLE libraries (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            created_at  REAL NOT NULL
        );
        CREATE TABLE classes (
            library_id     TEXT NOT NULL,
            name           TEXT NOT NULL,
            rank           INTEGER NOT NULL,
            created_at     REAL NOT NULL,
            match_strategy TEXT NOT NULL DEFAULT 'chamfer',
            bbox_ratio     REAL,
            PRIMARY KEY (library_id, name)
        );
        CREATE TABLE templates (
            id                 TEXT PRIMARY KEY,
            library_id         TEXT NOT NULL,
            class_name         TEXT NOT NULL,
            entity_point_sets  TEXT NOT NULL,
            centroid_x         REAL NOT NULL,
            centroid_y         REAL NOT NULL,
            bbox_xmin          REAL NOT NULL,
            bbox_ymin          REAL NOT NULL,
            bbox_xmax          REAL NOT NULL,
            bbox_ymax          REAL NOT NULL,
            created_at         REAL NOT NULL,
            entity_kinds       TEXT
        );
        INSERT INTO libraries (id, name, created_at) VALUES ('default', 'Default', 0);
        """
    )
    # Seed the pre-C4Ball canonical 15 — everything except C4Ball, in canonical
    # rank order. We derive the list from the current DEFAULT_CLASSES so the
    # test stays correct if more classes get added later.
    legacy_classes = [c for c in DEFAULT_CLASSES if c != "C4Ball"]
    now = time.time()
    for rank, name in enumerate(legacy_classes):
        conn.execute(
            "INSERT INTO classes (library_id, name, rank, created_at, match_strategy, bbox_ratio) "
            "VALUES ('default', ?, ?, ?, 'chamfer', NULL)",
            (name, rank, now),
        )
    conn.commit()
    conn.close()

    lib = _default_lib(tmp_db)
    assert "C4Ball" in lib.classes
    c4_idx = lib.classes.index("C4Ball")
    bga_idx = lib.classes.index("BGABall")
    assert c4_idx + 1 == bga_idx, (
        f"after migration expected C4Ball directly before BGABall, "
        f"got positions {c4_idx} and {bga_idx}"
    )


# ---- Per-class view constraints -------------------------------------------
def test_class_view_constraints_seed_entries():
    assert CLASS_VIEW_CONSTRAINTS["C4Ball"] == frozenset({"top_view"})
    assert CLASS_VIEW_CONSTRAINTS["BGABall"] == frozenset({"bottom_view", "side_view"})


def test_is_allowed_view_unconstrained_class():
    """Classes absent from the registry admit every position including None."""
    for v in ("top_view", "bottom_view", "side_view", None):
        assert is_allowed_view("Substrate", v) is True
        assert is_allowed_view("SMD-2T", v) is True
        # A made-up class name (custom user class) also passes through.
        assert is_allowed_view("MyMarker", v) is True


def test_is_allowed_view_c4ball():
    assert is_allowed_view("C4Ball", "top_view") is True
    assert is_allowed_view("C4Ball", "bottom_view") is False
    assert is_allowed_view("C4Ball", "side_view") is False
    # Strict mode: unassigned never allowed for a constrained class.
    assert is_allowed_view("C4Ball", None) is False


def test_is_allowed_view_bgaball():
    assert is_allowed_view("BGABall", "bottom_view") is True
    assert is_allowed_view("BGABall", "side_view") is True
    assert is_allowed_view("BGABall", "top_view") is False
    assert is_allowed_view("BGABall", None) is False


# ---- Neighbour-count arbitration registry ---------------------------------
def test_arbitration_groups_default_seed():
    """Seeded BGABall/FiducialCircle group present with documented rules."""
    matching = [
        g for g in CLASS_ARBITRATION_GROUPS
        if g.members == frozenset({"BGABall", "FiducialCircle"})
    ]
    assert len(matching) == 1, f"expected one BGA/Fiducial group, got {matching!r}"
    g = matching[0]
    assert g.rules["BGABall"] == MinNeighbors(2)
    assert g.rules["FiducialCircle"] == MaxNeighbors(1)
    assert g.default_class == "FiducialCircle"


def test_arbitration_group_for_returns_containing_group():
    g = arbitration_group_for("BGABall")
    assert g is not None
    assert "FiducialCircle" in g.members
    assert arbitration_group_for("Substrate") is None


def test_arbitration_group_requires_rule_per_member():
    with pytest.raises(ValueError, match="missing rules for members"):
        ArbitrationGroup(
            members=frozenset({"BGABall", "FiducialCircle"}),
            rules={"BGABall": MinNeighbors(2)},  # FiducialCircle rule missing
            default_class="BGABall",
        )


def test_arbitration_group_default_class_must_be_member():
    with pytest.raises(ValueError, match="not in members"):
        ArbitrationGroup(
            members=frozenset({"BGABall", "FiducialCircle"}),
            rules={
                "BGABall":        MinNeighbors(2),
                "FiducialCircle": MaxNeighbors(1),
            },
            default_class="Substrate",  # not a member
        )


def test_arbitration_group_rejects_extra_rules():
    with pytest.raises(ValueError, match="non-members"):
        ArbitrationGroup(
            members=frozenset({"BGABall", "FiducialCircle"}),
            rules={
                "BGABall":        MinNeighbors(2),
                "FiducialCircle": MaxNeighbors(1),
                "Substrate":      MinNeighbors(0),  # extra
            },
            default_class="FiducialCircle",
        )


def test_arbitration_group_rejects_singleton_members():
    with pytest.raises(ValueError, match="needs ≥2 members"):
        ArbitrationGroup(
            members=frozenset({"BGABall"}),
            rules={"BGABall": MinNeighbors(2)},
            default_class="BGABall",
        )


def test_arbitration_index_rejects_class_in_two_groups():
    g1 = ArbitrationGroup(
        members=frozenset({"BGABall", "FiducialCircle"}),
        rules={"BGABall": MinNeighbors(2), "FiducialCircle": MaxNeighbors(1)},
        default_class="FiducialCircle",
    )
    g2 = ArbitrationGroup(
        members=frozenset({"BGABall", "C4Ball"}),
        rules={"BGABall": MinNeighbors(2), "C4Ball": MaxNeighbors(1)},
        default_class="C4Ball",
    )
    with pytest.raises(ValueError, match="appears in two arbitration groups"):
        _build_arbitration_index((g1, g2))

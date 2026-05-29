"""SQLite-backed library: persistence round-trip, CRUD, class ops."""

from __future__ import annotations

import pytest

from app.library import (
    CLASS_ARBITRATION_GROUPS,
    CLASS_JSON_KEY,
    CLASS_VIEW_CONSTRAINTS,
    DEFAULT_CLASSES,
    DEFAULT_LIBRARY_ID,
    PRODUCT_SCOPED_CLASSES,
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
    is_product_scoped,
)


def _make_template(class_name="SMD-2T"):
    # Default to a library-scoped class so persistence-round-trip tests
    # using `add_template` (no product context) don't get purged by the
    # migration on reload. Tests exercising product-scope semantics use
    # the Store / add_template_for_file API directly.
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
    t = _make_template("SMD-2T")
    lib.add_template(t)
    assert lib.count("SMD-2T") == 1

    # Re-open: template survives.
    lib2 = _default_lib(tmp_db)
    assert lib2.count("SMD-2T") == 1
    assert lib2.templates_of("SMD-2T")[0].id == t.id


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
    # Start in a library-scoped class so `add_template` lands a
    # surviving row; move it to another library-scoped class.
    t = _make_template("SMD-3T")
    lib.add_template(t)
    assert lib.move_template(t.id, "SMD-2T")
    assert lib.count("SMD-3T") == 0
    assert lib.count("SMD-2T") == 1
    lib2 = _default_lib(tmp_db)
    assert lib2.count("SMD-3T") == 0
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
    # Distinct geometry: 4-point triangle instead of the 5-point unit square
    # that `_make_template` returns by default. Necessary because dedup on
    # commit collapses translation-equivalent shapes within the same class.
    t2 = Template.from_entities(
        "SMD-2T",
        [[(0.0, 0.0), (2.0, 0.0), (1.0, 1.732), (0.0, 0.0)]],
    )
    # FiducialCircle is library-scoped like SMD-2T; both survive an
    # `add_template` (no product) round-trip.
    t3 = _make_template("FiducialCircle")
    for t in (t1, t2, t3):
        lib.add_template(t)
    flat = lib.all_templates()
    flat_by_id = {t.id: (c, i, t) for c, i, t in flat}
    assert flat_by_id[t1.id][0] == "SMD-2T"
    assert flat_by_id[t1.id][1] == 0
    assert flat_by_id[t2.id][1] == 1
    assert flat_by_id[t3.id][0] == "FiducialCircle"
    assert flat_by_id[t3.id][1] == 0


# ---- multi-library specific tests -----------------------------------------
def test_multiple_libraries_isolated(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    lib_a = reg.get(DEFAULT_LIBRARY_ID)
    lib_b = reg.create("Other Library")

    # Library-scoped class so `add_template` lands a row that survives
    # boot migration; the test's intent is cross-library isolation.
    t = _make_template("SMD-2T")
    lib_a.add_template(t)
    assert lib_a.count("SMD-2T") == 1
    assert lib_b.count("SMD-2T") == 0  # other library is independent

    # Reload and confirm separation persists.
    reg2 = LibraryRegistry(Store(tmp_db))
    assert reg2.get(DEFAULT_LIBRARY_ID).count("SMD-2T") == 1
    assert reg2.get(lib_b.library_id).count("SMD-2T") == 0


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


def test_fiducial_square_ordered_immediately_after_fiducial_cross(tmp_db):
    """All three fiducial classes share the alignment-marker role; the
    canonical order groups them together with FiducialSquare last."""
    lib = _default_lib(tmp_db)
    assert "FiducialSquare" in lib.classes
    cross_idx = lib.classes.index("FiducialCross")
    square_idx = lib.classes.index("FiducialSquare")
    assert cross_idx + 1 == square_idx, (
        f"expected FiducialSquare directly after FiducialCross, "
        f"got positions {cross_idx} and {square_idx}"
    )


def test_fiducial_square_json_key_mapping():
    assert CLASS_JSON_KEY["FiducialSquare"] == "fiducial_square"


def test_legacy_library_gets_fiducial_square_seeded_and_ranked(tmp_db):
    """A library that pre-dates the FiducialSquare addition (16 canonical
    classes, no FiducialSquare row) SHALL gain FiducialSquare on next
    Store boot and SHALL re-rank it to sit immediately after
    FiducialCross — mirrors the C4Ball seeding behavior."""
    import sqlite3
    import time

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
    legacy_classes = [c for c in DEFAULT_CLASSES if c != "FiducialSquare"]
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
    assert "FiducialSquare" in lib.classes
    cross_idx = lib.classes.index("FiducialCross")
    square_idx = lib.classes.index("FiducialSquare")
    assert cross_idx + 1 == square_idx, (
        f"after migration expected FiducialSquare directly after FiducialCross, "
        f"got positions {cross_idx} and {square_idx}"
    )


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


# ---- Per-class storage scope (library vs. product) -----------------------
def test_is_product_scoped_partition():
    """The 8 design-specific classes return True; library-scoped classes
    (SMD/Fiducial/Pin-1/2DBarcode) and arbitrary custom classes return False."""
    for cls in (
        "Substrate", "Lid", "LidOuter", "LidInner", "DieArea",
        "C4Ball", "BGABall", "Protrusion",
    ):
        assert is_product_scoped(cls) is True, cls
    for cls in (
        "SMD-2T", "SMD-3T", "SMD-8T", "SMD-14T",
        "FiducialCircle", "FiducialCross", "FiducialSquare",
        "Pin-1", "2DBarcode",
        "MyMarker",  # arbitrary user-added class
    ):
        assert is_product_scoped(cls) is False, cls


def test_product_scoped_classes_subset_of_defaults():
    assert PRODUCT_SCOPED_CLASSES <= set(DEFAULT_CLASSES)


def _seeded_store(tmp_db):
    """Boot a Store + hydrate the default Library so its classes table is
    fully seeded — needed before exercising raw Store.insert_template /
    load_library calls."""
    reg = LibraryRegistry(Store(tmp_db))
    reg.get(DEFAULT_LIBRARY_ID)  # triggers DEFAULT_CLASSES seeding
    return reg.store


def test_load_library_default_is_library_scope_only(tmp_db):
    """Without a product_id, load_library returns ONLY library-scoped
    rows (product_id IS NULL). Product-scoped templates are hidden."""
    store = _seeded_store(tmp_db)
    lib_id = DEFAULT_LIBRARY_ID
    t_lib = Template.from_entities("SMD-2T", [[(0.0, 0.0), (1.0, 0.0)]])
    t_prod = Template.from_entities("Substrate", [[(0.0, 0.0), (1.0, 0.0)]])
    store.insert_template(lib_id, t_lib)  # product_id default None
    store.insert_template(lib_id, t_prod, product_id="p1")

    classes, _configs, templates = store.load_library(lib_id)
    assert "SMD-2T" in classes
    assert len(templates.get("SMD-2T", [])) == 1
    assert len(templates.get("Substrate", [])) == 0


def test_load_library_with_product_id_merges_scopes(tmp_db):
    """With product_id=p1, load_library merges library-scoped rows with
    p1's product-scoped rows."""
    store = _seeded_store(tmp_db)
    lib_id = DEFAULT_LIBRARY_ID
    t_lib = Template.from_entities("SMD-2T", [[(0.0, 0.0), (1.0, 0.0)]])
    t_prod = Template.from_entities("Substrate", [[(0.0, 0.0), (1.0, 0.0)]])
    store.insert_template(lib_id, t_lib)
    store.insert_template(lib_id, t_prod, product_id="p1")

    _classes, _configs, templates = store.load_library(lib_id, product_id="p1")
    assert len(templates.get("SMD-2T", [])) == 1
    assert len(templates.get("Substrate", [])) == 1


def test_load_library_other_product_does_not_see_substrate(tmp_db):
    """Product p2 does not see product p1's Substrate template."""
    store = _seeded_store(tmp_db)
    lib_id = DEFAULT_LIBRARY_ID
    t_prod = Template.from_entities("Substrate", [[(0.0, 0.0), (1.0, 0.0)]])
    store.insert_template(lib_id, t_prod, product_id="p1")

    _classes, _configs, templates = store.load_library(lib_id, product_id="p2")
    assert len(templates.get("Substrate", [])) == 0


def test_insert_template_keyword_product_id_roundtrips(tmp_db):
    """A template inserted with product_id="p1" comes back via
    load_library(product_id="p1") but not via load_library() alone."""
    store = _seeded_store(tmp_db)
    lib_id = DEFAULT_LIBRARY_ID
    t = Template.from_entities("C4Ball", [[(0.0, 0.0), (1.0, 0.0)]])
    store.insert_template(lib_id, t, product_id="p1")

    _, _, lib_only = store.load_library(lib_id)
    assert len(lib_only.get("C4Ball", [])) == 0

    _, _, with_p1 = store.load_library(lib_id, product_id="p1")
    assert len(with_p1.get("C4Ball", [])) == 1
    assert with_p1["C4Ball"][0].id == t.id


def test_migration_purges_legacy_library_scope_product_class_rows(tmp_db):
    """A pre-class-scope DB has product_id IS NULL rows for product-scoped
    classes. First boot SHALL drop them. Subsequent boots SHALL be no-ops."""
    import sqlite3
    import json as _json
    import time as _time
    import uuid as _uuid

    # First Store() call initialises the schema + runs _migrate(). After
    # that we sneak in a legacy-shape row (Substrate with product_id IS NULL)
    # to mimic what was on disk before this change shipped.
    Store(tmp_db)
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.execute(
            "INSERT INTO templates "
            "(id, library_id, class_name, entity_point_sets, centroid_x, centroid_y, "
            " bbox_xmin, bbox_ymin, bbox_xmax, bbox_ymax, created_at, entity_kinds, product_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (
                str(_uuid.uuid4()), DEFAULT_LIBRARY_ID, "Substrate",
                _json.dumps([[(0.0, 0.0), (1.0, 0.0)]]),
                0.0, 0.0, -1.0, -1.0, 1.0, 1.0, _time.time(),
            ),
        )
        conn.commit()

    # Confirm the row was inserted.
    with sqlite3.connect(str(tmp_db)) as conn:
        n_before = conn.execute(
            "SELECT COUNT(*) FROM templates "
            "WHERE class_name = 'Substrate' AND product_id IS NULL"
        ).fetchone()[0]
    assert n_before == 1

    # Re-open: migration purges the orphan row.
    Store(tmp_db)
    with sqlite3.connect(str(tmp_db)) as conn:
        n_after = conn.execute(
            "SELECT COUNT(*) FROM templates "
            "WHERE class_name = 'Substrate' AND product_id IS NULL"
        ).fetchone()[0]
    assert n_after == 0


def test_migration_purge_is_idempotent(tmp_db):
    """Booting twice in succession leaves the templates table identical
    on the second boot."""
    Store(tmp_db)
    Store(tmp_db)  # idempotent — should not raise or change anything

    with __import__("sqlite3").connect(str(tmp_db)) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()]
    assert "product_id" in cols

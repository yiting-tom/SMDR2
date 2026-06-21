"""SQLite-backed library: persistence round-trip, CRUD, class ops.

Versioned model (2026-06-10): no auto-created "default" library exists —
production libraries are created 1:1 by version creation (app.versions).
Tests create a plain library directly via Store.create_library.
"""

from __future__ import annotations

import pytest

from app.library import (
    CLASS_DEFAULT_MATCH_CONFIG,
    CLASS_JSON_KEY,
    CLASS_VIEW_CONSTRAINTS,
    DEFAULT_CLASSES,
    LibraryRegistry,
    Store,
    Template,
    is_allowed_view,
)


LIB_ID = "lib-under-test"


def _make_template(class_name="SMD-2T"):
    return Template.from_entities(
        class_name,
        [[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]],
    )


def _lib(tmp_db, lib_id=LIB_ID):
    """Open (creating on first call) a test library from a fresh registry
    on a tmp DB — re-calling with the same tmp_db reloads from disk."""
    store = Store(tmp_db)
    if store.get_library(lib_id) is None:
        store.create_library(lib_id, "Test Library")
    return LibraryRegistry(store).get(lib_id)


def test_store_creates_default_classes(tmp_db):
    lib = _lib(tmp_db)
    assert set(lib.classes) >= set(DEFAULT_CLASSES)
    for c in DEFAULT_CLASSES:
        assert lib.count(c) == 0


def test_add_template_persists(tmp_db):
    lib = _lib(tmp_db)
    t = _make_template("SMD-2T")
    lib.add_template(t)
    assert lib.count("SMD-2T") == 1

    # Re-open: template survives.
    lib2 = _lib(tmp_db)
    assert lib2.count("SMD-2T") == 1
    assert lib2.templates_of("SMD-2T")[0].id == t.id


def test_revision_bumps_on_every_result_affecting_write(tmp_db):
    """The library `revision` strictly increases on insert / delete / reclass /
    strategy-change, and a pure read leaves it unchanged (pre-match staleness
    signal — see fix-stale-prematch-cache)."""
    store = Store(tmp_db)
    store.create_library(LIB_ID, "Test Library")
    # Strategy bump needs the class row to exist (rowcount > 0 to count).
    store.upsert_class(LIB_ID, "SMD-2T")
    store.upsert_class(LIB_ID, "SMD-3T")
    t = _make_template("SMD-2T")

    r0 = store.current_revision(LIB_ID)
    store.insert_template(LIB_ID, t)
    r1 = store.current_revision(LIB_ID)
    assert r1 > r0, "insert_template must bump revision"

    # Pure read does not bump.
    store.load_library(LIB_ID)
    assert store.current_revision(LIB_ID) == r1

    store.update_template_class(t.id, "SMD-3T")
    r2 = store.current_revision(LIB_ID)
    assert r2 > r1, "update_template_class must bump revision"

    assert store.update_class_strategy(LIB_ID, "SMD-3T", "signature", 0.5) is True
    r3 = store.current_revision(LIB_ID)
    assert r3 > r2, "update_class_strategy must bump revision"

    assert store.delete_template(t.id) is True
    r4 = store.current_revision(LIB_ID)
    assert r4 > r3, "delete_template must bump revision"


def test_current_revision_unknown_library_is_zero(tmp_db):
    store = Store(tmp_db)
    assert store.current_revision("no-such-library") == 0


def test_delete_template(tmp_db):
    lib = _lib(tmp_db)
    t = _make_template("SMD-2T")
    lib.add_template(t)
    assert lib.delete_template(t.id)
    assert lib.count("SMD-2T") == 0
    lib2 = _lib(tmp_db)
    assert lib2.count("SMD-2T") == 0


def test_delete_nonexistent_returns_false(tmp_db):
    lib = _lib(tmp_db)
    assert lib.delete_template("does-not-exist") is False


def test_move_template_changes_class_and_persists(tmp_db):
    lib = _lib(tmp_db)
    t = _make_template("SMD-3T")
    lib.add_template(t)
    assert lib.move_template(t.id, "SMD-2T")
    assert lib.count("SMD-3T") == 0
    assert lib.count("SMD-2T") == 1
    lib2 = _lib(tmp_db)
    assert lib2.count("SMD-3T") == 0
    assert lib2.count("SMD-2T") == 1


def test_add_custom_class(tmp_db):
    lib = _lib(tmp_db)
    lib.add_class("my_new_class")
    assert "my_new_class" in lib.classes
    lib2 = _lib(tmp_db)
    assert "my_new_class" in lib2.classes


def test_template_from_entities_validates_nonempty():
    with pytest.raises(ValueError):
        Template.from_entities("SMD-2T", [[]])


# ---- per-class match strategy + bbox_ratio -------------------------------
def test_new_class_defaults_to_chamfer(tmp_db):
    """Newly-seeded classes default to chamfer / bbox_ratio=None, except the
    large-outline classes in CLASS_DEFAULT_MATCH_CONFIG which seed as their
    declared signature default."""
    lib = _lib(tmp_db)
    by_name = {e["name"]: e for e in lib.summary()}
    for c in DEFAULT_CLASSES:
        expected = CLASS_DEFAULT_MATCH_CONFIG.get(c, ("chamfer", None))
        assert lib.strategy_of(c) == expected
        # summary surfaces both fields on every entry
        assert by_name[c]["match_strategy"] == expected[0]
        assert by_name[c]["bbox_ratio"] == expected[1]


def test_large_outline_classes_default_to_signature(tmp_db):
    """Substrate / RingOuter / RingInner seed as signature with bbox_ratio
    0.0001 — their large sharp-cornered boundary is matched by size + aspect,
    not chamfer (which is winding / start-vertex sensitive). Persists across a
    store reload."""
    lib = _lib(tmp_db)
    for c in ("Substrate", "RingOuter", "RingInner"):
        assert lib.strategy_of(c) == ("signature", 0.0001)
    # Reload: the seeded default sticks (and is not re-clobbered by the boot
    # maintenance, which only touches untouched chamfer/NULL rows).
    lib2 = _lib(tmp_db)
    for c in ("Substrate", "RingOuter", "RingInner"):
        assert lib2.strategy_of(c) == ("signature", 0.0001)


def test_signature_default_preserves_explicit_override(tmp_db):
    """An explicit signature bbox_ratio (distinct from the declared 0.0001
    default) survives a store reopen — the boot maintenance only converts rows
    still in the pristine chamfer/NULL state, so it never overwrites an
    explicit signature choice."""
    lib = _lib(tmp_db)
    assert lib.set_strategy("Substrate", "signature", 0.05)
    assert _lib(tmp_db).strategy_of("Substrate") == ("signature", 0.05)


def test_set_strategy_round_trips_signature(tmp_db):
    lib = _lib(tmp_db)
    assert lib.set_strategy("Substrate", "signature", 0.05)
    assert lib.strategy_of("Substrate") == ("signature", 0.05)
    # Reload from store: value sticks.
    lib2 = _lib(tmp_db)
    assert lib2.strategy_of("Substrate") == ("signature", 0.05)


def test_set_strategy_unknown_class_returns_false(tmp_db):
    lib = _lib(tmp_db)
    assert lib.set_strategy("DoesNotExist", "signature", 0.05) is False


def test_pre_versioning_db_is_rebuilt_from_scratch(tmp_db):
    """Replaces test_migration_adds_strategy_columns (two-tier scope removed
    2026-06-10, openspec add-product-versioning): the in-place ALTER that
    added match_strategy / bbox_ratio is gone. A pre-versioning DB (no
    `versions` table) is dropped and rebuilt from scratch by
    app.dbschema.ensure_versioned_schema — fresh schema has the columns,
    legacy rows do NOT survive (decision C9)."""
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
        INSERT INTO libraries (id, name, created_at) VALUES ('default', 'Default', 0);
        INSERT INTO classes (library_id, name, rank, created_at)
            VALUES ('default', 'Substrate', 0, 0);
        """
    )
    conn.commit()
    conn.close()

    store = Store(tmp_db)
    cols = [
        r["name"]
        for r in store.conn.execute("PRAGMA table_info(classes)")
    ]
    assert "match_strategy" in cols
    assert "bbox_ratio" in cols
    # No data preserved: the legacy 'default' library is gone.
    assert store.get_library("default") is None
    assert store.list_libraries() == []


def test_all_templates_returns_indexed_tuples(tmp_db):
    lib = _lib(tmp_db)
    t1 = _make_template("SMD-2T")
    # Distinct geometry: 4-point triangle instead of the 5-point unit square
    # that `_make_template` returns by default. Necessary because dedup on
    # commit collapses translation-equivalent shapes within the same class.
    t2 = Template.from_entities(
        "SMD-2T",
        [[(0.0, 0.0), (2.0, 0.0), (1.0, 1.732), (0.0, 0.0)]],
    )
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
    lib_a = reg.create("Library A")
    lib_b = reg.create("Other Library")

    t = _make_template("SMD-2T")
    lib_a.add_template(t)
    assert lib_a.count("SMD-2T") == 1
    assert lib_b.count("SMD-2T") == 0  # other library is independent

    # Reload and confirm separation persists.
    reg2 = LibraryRegistry(Store(tmp_db))
    assert reg2.get(lib_a.library_id).count("SMD-2T") == 1
    assert reg2.get(lib_b.library_id).count("SMD-2T") == 0


# test_cannot_delete_default_library was removed (two-tier scope removed
# 2026-06-10, openspec add-product-versioning): no "default" library is
# auto-created anymore and LibraryRegistry.delete no longer protects any
# id — libraries die with their version / product cascade.
def test_delete_library_removes_it(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    lib = reg.create("Doomed")
    assert reg.exists(lib.library_id) is True
    reg.delete(lib.library_id)
    assert reg.exists(lib.library_id) is False


def test_create_library_seeds_default_classes(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    new_lib = reg.create("New Lib")
    assert set(new_lib.classes) >= set(DEFAULT_CLASSES)


# ---- C4Ball canonical-class ordering --------------------------------------
def test_c4ball_ordered_immediately_before_bgaball(tmp_db):
    """C4Ball and BGABall are both ball-type interconnect; the canonical
    order groups them together with C4Ball first."""
    lib = _lib(tmp_db)
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
    lib = _lib(tmp_db)
    assert "FiducialSquare" in lib.classes
    cross_idx = lib.classes.index("FiducialCross")
    square_idx = lib.classes.index("FiducialSquare")
    assert cross_idx + 1 == square_idx, (
        f"expected FiducialSquare directly after FiducialCross, "
        f"got positions {cross_idx} and {square_idx}"
    )


def test_fiducial_square_json_key_mapping():
    assert CLASS_JSON_KEY["FiducialSquare"] == "fiducial_square"


def _seed_library_missing_class(tmp_db, missing: str) -> None:
    """Boot the versioned schema, then hand-insert a library whose classes
    table carries every default class EXCEPT `missing` — mimics a library
    created before that class was added. The next Store() boot must seed
    and re-rank it. (Setup goes through Store() first because a hand-built
    pre-versioning DB would be dropped by the dbschema rebuild guard.)"""
    import sqlite3
    import time

    Store(tmp_db)  # initialise the versioned schema
    legacy_classes = [c for c in DEFAULT_CLASSES if c != missing]
    now = time.time()
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.execute(
            "INSERT INTO libraries (id, name, created_at) VALUES (?, ?, 0)",
            (LIB_ID, "Legacy Lib"),
        )
        for rank, name in enumerate(legacy_classes):
            conn.execute(
                "INSERT INTO classes (library_id, name, rank, created_at, match_strategy, bbox_ratio) "
                "VALUES (?, ?, ?, ?, 'chamfer', NULL)",
                (LIB_ID, name, rank, now),
            )
        conn.commit()


def test_legacy_library_gets_fiducial_square_seeded_and_ranked(tmp_db):
    """A library that pre-dates the FiducialSquare addition (no
    FiducialSquare row) SHALL gain FiducialSquare on next Store boot and
    SHALL re-rank it to sit immediately after FiducialCross — mirrors the
    C4Ball seeding behavior."""
    _seed_library_missing_class(tmp_db, "FiducialSquare")

    lib = _lib(tmp_db)  # fresh Store boot runs the per-boot maintenance
    assert "FiducialSquare" in lib.classes
    cross_idx = lib.classes.index("FiducialCross")
    square_idx = lib.classes.index("FiducialSquare")
    assert cross_idx + 1 == square_idx, (
        f"after migration expected FiducialSquare directly after FiducialCross, "
        f"got positions {cross_idx} and {square_idx}"
    )


def test_legacy_library_gets_c4ball_seeded_and_ranked(tmp_db):
    """A library that pre-dates the C4Ball addition (no C4Ball row) SHALL
    gain C4Ball on next Store boot and SHALL re-rank it to sit immediately
    before BGABall — mirrors the existing FiducialCircle/Cross seeding
    behavior."""
    _seed_library_missing_class(tmp_db, "C4Ball")

    lib = _lib(tmp_db)
    assert "C4Ball" in lib.classes
    c4_idx = lib.classes.index("C4Ball")
    bga_idx = lib.classes.index("BGABall")
    assert c4_idx + 1 == bga_idx, (
        f"after migration expected C4Ball directly before BGABall, "
        f"got positions {c4_idx} and {bga_idx}"
    )


# ---- LidOuter re-introduction (openspec add-lidouter-class) ----------------
def test_legacy_library_gets_lidouter_seeded_and_ranked(tmp_db):
    """A library that pre-dates the LidOuter re-introduction gains it on
    next boot, ranked directly after RingInner, with the signature default."""
    _seed_library_missing_class(tmp_db, "LidOuter")
    lib = _lib(tmp_db)
    assert "LidOuter" in lib.classes
    assert lib.classes.index("LidOuter") == lib.classes.index("RingInner") + 1
    assert lib.strategy_of("LidOuter") == ("signature", 0.0001)


def test_lidinner_survives_rename_pass(tmp_db):
    """LidInner was re-introduced 2026-06-15 as its own class; like LidOuter
    it must NOT appear in LEGACY_CLASS_RENAME, else the old LidInner→RingInner
    mapping would wipe every new LidInner class and its templates on reboot."""
    from app.library import LEGACY_CLASS_RENAME, Template
    assert "LidInner" not in LEGACY_CLASS_RENAME
    lib = _lib(tmp_db)
    assert "LidInner" in lib.classes
    assert lib.strategy_of("LidInner") == ("signature", 0.0001)
    _, dup = lib.add_template_for_file(Template.from_entities(
        "LidInner", [[(0.0, 0.0), (9.0, 0.0), (9.0, 7.0), (0.0, 7.0)]]
    ))
    assert not dup
    lib2 = _lib(tmp_db)  # reboot runs the rename pass
    assert "LidInner" in lib2.classes
    assert len(lib2.templates_of("LidInner")) == 1


def test_lidouter_survives_rename_pass(tmp_db):
    """LidOuter must NOT appear in LEGACY_CLASS_RENAME any more: the 06-09
    mapping (LidOuter→RingOuter) would wipe every new LidOuter class and
    its templates on the next boot."""
    from app.library import LEGACY_CLASS_RENAME, Template
    assert "LidOuter" not in LEGACY_CLASS_RENAME
    lib = _lib(tmp_db)
    _, dup = lib.add_template_for_file(Template.from_entities(
        "LidOuter", [[(0.0, 0.0), (10.0, 0.0), (10.0, 8.0), (0.0, 8.0)]]
    ))
    assert not dup
    lib2 = _lib(tmp_db)  # reboot runs the rename pass
    assert "LidOuter" in lib2.classes
    assert len(lib2.templates_of("LidOuter")) == 1


def test_legacy_snake_lid_outer_renames_to_lidouter(tmp_db):
    """The snake_case legacy id goes back to its literal meaning now that
    LidOuter exists again (lid_inner likewise → the re-introduced LidInner)."""
    import sqlite3
    import time
    Store(tmp_db)
    with sqlite3.connect(str(tmp_db)) as conn:
        conn.execute(
            "INSERT INTO libraries (id, name, created_at) VALUES (?, ?, 0)",
            (LIB_ID, "Legacy Lib"),
        )
        conn.execute(
            "INSERT INTO classes (library_id, name, rank, created_at, match_strategy, bbox_ratio) "
            "VALUES (?, 'lid_outer', 0, ?, 'chamfer', NULL)",
            (LIB_ID, time.time()),
        )
        conn.commit()
    lib = _lib(tmp_db)
    assert "LidOuter" in lib.classes
    assert "lid_outer" not in lib.classes


# ---- Per-class view constraints -------------------------------------------
def test_class_view_constraints_seed_entries():
    assert CLASS_VIEW_CONSTRAINTS["C4Ball"] == frozenset({"top_view"})
    # BGABall is bottom-only (disambiguated against top-only FiducialCircle).
    assert CLASS_VIEW_CONSTRAINTS["BGABall"] == frozenset({"bottom_view"})
    assert CLASS_VIEW_CONSTRAINTS["FiducialCircle"] == frozenset({"top_view"})
    assert CLASS_VIEW_CONSTRAINTS["FiducialCross"] == frozenset({"top_view", "bottom_view"})
    assert CLASS_VIEW_CONSTRAINTS["FiducialSquare"] == frozenset({"top_view", "bottom_view"})
    for smd in ("SMD-2T", "SMD-3T", "SMD-8T", "SMD-14T"):
        assert CLASS_VIEW_CONSTRAINTS[smd] == frozenset({"top_view", "bottom_view"})


def test_is_allowed_view_unconstrained_class():
    """Classes absent from the registry admit every position including None."""
    for v in ("top_view", "bottom_view", "side_view", None):
        # Substrate / DieArea have no view constraint.
        assert is_allowed_view("Substrate", v) is True
        assert is_allowed_view("DieArea", v) is True
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
    # side_view retired: BGABall is bottom-only now.
    assert is_allowed_view("BGABall", "side_view") is False
    assert is_allowed_view("BGABall", "top_view") is False
    assert is_allowed_view("BGABall", None) is False


def test_is_allowed_view_fiducial_circle_top_only():
    assert is_allowed_view("FiducialCircle", "top_view") is True
    assert is_allowed_view("FiducialCircle", "bottom_view") is False
    assert is_allowed_view("FiducialCircle", "side_view") is False
    assert is_allowed_view("FiducialCircle", None) is False


def test_every_default_class_has_a_category():
    from app.library import CLASS_CATEGORY, CLASS_CATEGORY_ORDER
    # Every default class is categorised, and every category used is declared.
    assert set(DEFAULT_CLASSES) <= set(CLASS_CATEGORY)
    order_keys = [k for k, _ in CLASS_CATEGORY_ORDER]
    assert order_keys == ["structure", "balls", "smd", "marks"]
    assert set(CLASS_CATEGORY.values()) <= set(order_keys)
    assert all(label for _, label in CLASS_CATEGORY_ORDER)


def test_class_category_assignments():
    from app.library import CLASS_CATEGORY
    assert CLASS_CATEGORY["DAM1"] == "structure"
    assert CLASS_CATEGORY["DAM2"] == "structure"
    assert CLASS_CATEGORY["Protrusion"] == "structure"
    for s in ("SMD-2T", "SMD-3T", "SMD-8T", "SMD-14T"):
        assert CLASS_CATEGORY[s] == "smd"
    for b in ("C4Ball", "BGABall"):
        assert CLASS_CATEGORY[b] == "balls"
    for m in ("FiducialCircle", "FiducialCross", "FiducialSquare",
              "Pin-1", "2DBarcode"):
        assert CLASS_CATEGORY[m] == "marks"


# ---- Single-scope template storage ----------------------------------------
# Removed tests (two-tier scope removed 2026-06-10, openspec
# add-product-versioning) — PRODUCT_SCOPED_CLASSES / is_product_scoped,
# the templates.product_id column, the dual-scope merge in load_library,
# and the boot purge of leaked library-scope rows no longer exist (every
# library belongs 1:1 to a version, so all templates are version-scoped
# by construction):
# - test_is_product_scoped_partition
# - test_product_scoped_classes_subset_of_defaults
# - test_load_library_default_is_library_scope_only
# - test_load_library_with_product_id_merges_scopes
# - test_load_library_other_product_does_not_see_substrate
# - test_insert_template_keyword_product_id_roundtrips
# - test_migration_purges_legacy_library_scope_product_class_rows

def test_load_library_returns_all_templates(tmp_db):
    """load_library(library_id) is the version's complete template view —
    previously-product-scoped classes (e.g. Substrate) are plain rows now,
    visible without any product context."""
    lib = _lib(tmp_db)
    store = lib.store
    t_smd = Template.from_entities("SMD-2T", [[(0.0, 0.0), (1.0, 0.0)]])
    t_sub = Template.from_entities("Substrate", [[(0.0, 0.0), (1.0, 0.0)]])
    store.insert_template(LIB_ID, t_smd)
    store.insert_template(LIB_ID, t_sub)

    classes, _configs, templates = store.load_library(LIB_ID)
    assert "SMD-2T" in classes
    assert len(templates.get("SMD-2T", [])) == 1
    assert len(templates.get("Substrate", [])) == 1
    assert templates["Substrate"][0].id == t_sub.id


def test_store_boot_is_idempotent_and_has_no_product_id(tmp_db):
    """Booting twice in succession leaves the templates table identical on
    the second boot — and the rebuilt schema carries no product_id column
    (replaces test_migration_purge_is_idempotent, which asserted the
    removed two-tier column)."""
    Store(tmp_db)
    Store(tmp_db)  # idempotent — should not raise or change anything

    with __import__("sqlite3").connect(str(tmp_db)) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(templates)").fetchall()]
    assert "product_id" not in cols

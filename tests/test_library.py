"""SQLite-backed library: persistence round-trip, CRUD, class ops."""

from __future__ import annotations

from app.library import (
    DEFAULT_CLASSES,
    DEFAULT_LIBRARY_ID,
    Library,
    LibraryRegistry,
    Store,
    Template,
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

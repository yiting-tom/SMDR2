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


def _make_template(class_name="bga_ball"):
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
    t = _make_template("bga_ball")
    lib.add_template(t)
    assert lib.count("bga_ball") == 1

    # Re-open: template survives.
    lib2 = _default_lib(tmp_db)
    assert lib2.count("bga_ball") == 1
    assert lib2.templates_of("bga_ball")[0].id == t.id


def test_delete_template(tmp_db):
    lib = _default_lib(tmp_db)
    t = _make_template("smd")
    lib.add_template(t)
    assert lib.delete_template(t.id)
    assert lib.count("smd") == 0
    lib2 = _default_lib(tmp_db)
    assert lib2.count("smd") == 0


def test_delete_nonexistent_returns_false(tmp_db):
    lib = _default_lib(tmp_db)
    assert lib.delete_template("does-not-exist") is False


def test_move_template_changes_class_and_persists(tmp_db):
    lib = _default_lib(tmp_db)
    t = _make_template("bga_ball")
    lib.add_template(t)
    assert lib.move_template(t.id, "smd")
    assert lib.count("bga_ball") == 0
    assert lib.count("smd") == 1
    lib2 = _default_lib(tmp_db)
    assert lib2.count("bga_ball") == 0
    assert lib2.count("smd") == 1


def test_add_custom_class(tmp_db):
    lib = _default_lib(tmp_db)
    lib.add_class("my_new_class")
    assert "my_new_class" in lib.classes
    lib2 = _default_lib(tmp_db)
    assert "my_new_class" in lib2.classes


def test_template_from_entities_validates_nonempty():
    import pytest
    with pytest.raises(ValueError):
        Template.from_entities("smd", [[]])


def test_all_templates_returns_indexed_tuples(tmp_db):
    lib = _default_lib(tmp_db)
    t1 = _make_template("smd")
    t2 = _make_template("smd")
    t3 = _make_template("bga_ball")
    for t in (t1, t2, t3):
        lib.add_template(t)
    flat = lib.all_templates()
    flat_by_id = {t.id: (c, i, t) for c, i, t in flat}
    assert flat_by_id[t1.id][0] == "smd"
    assert flat_by_id[t1.id][1] == 0
    assert flat_by_id[t2.id][1] == 1
    assert flat_by_id[t3.id][0] == "bga_ball"
    assert flat_by_id[t3.id][1] == 0


# ---- multi-library specific tests -----------------------------------------
def test_multiple_libraries_isolated(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    lib_a = reg.get(DEFAULT_LIBRARY_ID)
    lib_b = reg.create("BGA Variants")

    t = _make_template("bga_ball")
    lib_a.add_template(t)
    assert lib_a.count("bga_ball") == 1
    assert lib_b.count("bga_ball") == 0  # other library is independent

    # Reload and confirm separation persists.
    reg2 = LibraryRegistry(Store(tmp_db))
    assert reg2.get(DEFAULT_LIBRARY_ID).count("bga_ball") == 1
    assert reg2.get(lib_b.library_id).count("bga_ball") == 0


def test_cannot_delete_default_library(tmp_db):
    import pytest
    reg = LibraryRegistry(Store(tmp_db))
    with pytest.raises(ValueError):
        reg.delete(DEFAULT_LIBRARY_ID)


def test_create_library_seeds_default_classes(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    new_lib = reg.create("New Lib")
    assert set(new_lib.classes) >= set(DEFAULT_CLASSES)

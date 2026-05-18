"""ProductStore CRUD + per-product file slot constraints."""

from __future__ import annotations

import pytest

from app.files import FileStore
from app.library import DEFAULT_LIBRARY_ID, LibraryRegistry, Store
from app.products import ProductStore, VALID_ROLES


def _bootstrap(tmp_db):
    """Open fresh stores backed by the same temp DB and seed the default library."""
    libstore = Store(tmp_db)
    LibraryRegistry(libstore).get(DEFAULT_LIBRARY_ID)
    pstore = ProductStore(tmp_db)
    fstore = FileStore(tmp_db)
    return pstore, fstore


def test_product_crud(tmp_db):
    pstore, _ = _bootstrap(tmp_db)
    p = pstore.create("Acme P-1", DEFAULT_LIBRARY_ID)
    assert p.id and p.name == "Acme P-1" and p.library_id == DEFAULT_LIBRARY_ID

    assert pstore.get(p.id) is not None
    assert any(x.id == p.id for x in pstore.list_all())

    assert pstore.delete(p.id) is True
    assert pstore.get(p.id) is None


def test_role_uniqueness_per_product(tmp_db):
    pstore, fstore = _bootstrap(tmp_db)
    p = pstore.create("Pkg-1", DEFAULT_LIBRARY_ID)

    fstore.register("f1", "a.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="SBT")

    # Different role under the same product is fine.
    fstore.register("f3", "c.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="BD")

    # Same role under a *different* product is also fine.
    p2 = pstore.create("Pkg-2", DEFAULT_LIBRARY_ID)
    fstore.register("f4", "d.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p2.id, dxf_role="SBT")

    # All four files coexist (different role or different product):
    by_role = {f.dxf_role: f.id for f in fstore.list_by_product(p.id)}
    assert by_role == {"SBT": "f1", "BD": "f3"}
    by_role_p2 = {f.dxf_role: f.id for f in fstore.list_by_product(p2.id)}
    assert by_role_p2 == {"SBT": "f4"}


def test_replace_in_same_slot_via_register(tmp_db):
    """Production behaviour for re-uploading: the endpoint first clears the
    slot, then registers the new file. Verify the FileStore primitives
    support this two-step transition without violating the unique index."""
    pstore, fstore = _bootstrap(tmp_db)
    p = pstore.create("Pkg-R", DEFAULT_LIBRARY_ID)
    fstore.register("a", "a.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="POD")
    # Free the slot on the first file (mimics what the upload endpoint does).
    with fstore.lock, fstore.conn:
        fstore.conn.execute(
            "UPDATE files SET product_id = NULL, dxf_role = NULL WHERE id = ?",
            ("a",),
        )
    fstore.register("b", "b.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="POD")
    by_role = {f.dxf_role: f.id for f in fstore.list_by_product(p.id)}
    assert by_role == {"POD": "b"}


def test_list_by_product(tmp_db):
    pstore, fstore = _bootstrap(tmp_db)
    p = pstore.create("Pkg-3", DEFAULT_LIBRARY_ID)
    for i, role in enumerate(VALID_ROLES):
        fstore.register(f"id{i}", f"x{i}.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                        product_id=p.id, dxf_role=role)
    items = fstore.list_by_product(p.id)
    assert {f.dxf_role for f in items} == set(VALID_ROLES)


def test_set_match_saved(tmp_db):
    pstore, fstore = _bootstrap(tmp_db)
    p = pstore.create("Pkg-4", DEFAULT_LIBRARY_ID)
    fstore.register("z1", "z.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="SBT")
    assert fstore.get("z1").match_saved is False
    fstore.set_match_saved("z1", True)
    assert fstore.get("z1").match_saved is True
    fstore.set_match_saved("z1", False)
    assert fstore.get("z1").match_saved is False


# ---- multi-DXF-per-role coexistence rules --------------------------------
def test_same_role_accumulates_files_under_multi(tmp_db):
    """All DXFs in a (product, role) are tagged `multi`; the schema
    distinguishes them by file id alone. Three siblings coexist."""
    pstore, fstore = _bootstrap(tmp_db)
    p = pstore.create("Pkg-MV", DEFAULT_LIBRARY_ID)
    fstore.register("f1", "a.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="SBT", dxf_view="multi")
    fstore.register("f2", "b.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="SBT", dxf_view="multi")
    fstore.register("f3", "c.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="SBT", dxf_view="multi")
    ids = {f.id for f in fstore.list_by_product(p.id) if f.dxf_role == "SBT"}
    assert ids == {"f1", "f2", "f3"}


def test_same_role_allows_multiple_files(tmp_db):
    """A (product, role) accepts any number of DXFs; the DB no longer
    enforces a per-slot unique constraint. View coverage is the job of
    `app.product_views.resolve_views`, not the schema."""
    pstore, fstore = _bootstrap(tmp_db)
    p = pstore.create("Pkg-Multi", DEFAULT_LIBRARY_ID)
    fstore.register("a", "a.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="BD", dxf_view="multi")
    # Adding a second file under the same (product, role) must succeed.
    fstore.register("b", "b.dxf", 1, library_id=DEFAULT_LIBRARY_ID,
                    product_id=p.id, dxf_role="BD", dxf_view="multi")
    ids = {f.id for f in fstore.list_by_product(p.id) if f.dxf_role == "BD"}
    assert ids == {"a", "b"}


def test_legacy_row_backfilled_to_multi(tmp_db):
    """A row inserted with a pre-multi-dxf schema (no dxf_view column) is
    migrated to dxf_view='multi' when FileStore reinitialises."""
    import sqlite3
    # Build a legacy schema close to the production shape before this
    # change — every column referenced by `_row_to_record` is present
    # except the new dxf_view; the old (product_id, dxf_role) unique
    # index is also present.
    raw = sqlite3.connect(str(tmp_db))
    raw.executescript("""
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
            match_saved     INTEGER NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX idx_files_product_role
            ON files(product_id, dxf_role)
            WHERE product_id IS NOT NULL AND dxf_role IS NOT NULL;
    """)
    raw.execute(
        "INSERT INTO files (id, name, size, uploaded_at, status, library_id, product_id, dxf_role) "
        "VALUES ('legacy', 'old.dxf', 1, 0.0, 'ready_to_match', 'default', 'p1', 'SBT')"
    )
    raw.commit()
    raw.close()
    # Initialise the new FileStore against the legacy DB.
    fstore = FileStore(tmp_db)
    rec = fstore.get("legacy")
    assert rec is not None
    assert rec.dxf_view == "multi"
    # The view-aware UNIQUE index from the intermediate iteration must NOT
    # be present (the current model permits multiple files per role); a
    # non-unique (product_id, dxf_role) index is fine and aids queries.
    indexes = {r["name"]: r["sql"] for r in fstore.conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND name LIKE 'idx_files_product%'"
    ).fetchall()}
    assert "idx_files_product_role_view" not in indexes
    pr_sql = indexes.get("idx_files_product_role") or ""
    assert "UNIQUE" not in pr_sql.upper(), (
        f"idx_files_product_role must not be UNIQUE; got: {pr_sql}"
    )

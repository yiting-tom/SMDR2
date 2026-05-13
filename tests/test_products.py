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

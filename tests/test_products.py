"""Product creation (with mandatory first version) + per-version role bindings.

Versioned model (2026-06-10): products are created through
`VersionStore.create_product` (one txn, no version-less products); files
are bound into a *version's* role via FileStore.bind, so every former
per-product slot behaviour is now per-version.
"""

from __future__ import annotations


from app.files import FileStore
from app.library import Store
from app.products import ProductStore, VALID_ROLES
from app.versions import VersionStore


def _bootstrap(tmp_db):
    """Open fresh stores backed by the same temp DB. Store() first so the
    libraries table exists before version creation inserts into it."""
    Store(tmp_db)
    vstore = VersionStore(tmp_db)
    pstore = ProductStore(tmp_db)
    fstore = FileStore(tmp_db)
    return vstore, pstore, fstore


def _add_file(fstore, version_id, fid, name, role, **kw):
    fstore.register_content(fid, name, 1)
    return fstore.bind(version_id, role, fid, **kw)


def test_product_crud(tmp_db):
    vstore, pstore, _ = _bootstrap(tmp_db)
    p, v = vstore.create_product("Acme P-1", "v1")
    assert p.id and p.name == "Acme P-1"
    # The mandatory first version lands in the same transaction, with its
    # own (empty) library — products no longer carry a library_id.
    assert not hasattr(p, "library_id")
    assert v.product_id == p.id
    assert v.label == "v1"
    assert v.library_id
    assert Store(tmp_db).get_library(v.library_id) is not None

    assert pstore.get(p.id) is not None
    assert any(x.id == p.id for x in pstore.list_all())

    # Deletion path: cascade versions first, then the product row.
    removed = vstore.delete_for_product(p.id)
    assert [x.id for x in removed] == [v.id]
    assert pstore.delete(p.id) is True
    assert pstore.get(p.id) is None
    assert vstore.list_by_product(p.id) == []


def test_role_bindings_scoped_per_version(tmp_db):
    vstore, _, fstore = _bootstrap(tmp_db)
    _, v1 = vstore.create_product("Pkg-1", "v1")

    _add_file(fstore, v1.id, "f1", "a.dxf", "SBT")

    # Different role under the same version is fine.
    _add_file(fstore, v1.id, "f3", "c.dxf", "BD")

    # Same role under a *different* product's version is also fine.
    _, v2 = vstore.create_product("Pkg-2", "v1")
    _add_file(fstore, v2.id, "f4", "d.dxf", "SBT")

    # All bindings coexist (different role or different version):
    by_role = {f.dxf_role: f.id for f in fstore.list_by_version(v1.id)}
    assert by_role == {"SBT": "f1", "BD": "f3"}
    by_role_v2 = {f.dxf_role: f.id for f in fstore.list_by_version(v2.id)}
    assert by_role_v2 == {"SBT": "f4"}


def test_replace_in_same_slot_via_rebind(tmp_db):
    """Production behaviour for re-uploading: the endpoint first clears the
    slot (unbind_role), then binds the new file. Verify the FileStore
    primitives support this two-step transition."""
    vstore, _, fstore = _bootstrap(tmp_db)
    _, v = vstore.create_product("Pkg-R", "v1")
    _add_file(fstore, v.id, "a", "a.dxf", "POD")
    # Free the slot (mimics what the upload endpoint does).
    assert fstore.unbind_role(v.id, "POD") == 1
    _add_file(fstore, v.id, "b", "b.dxf", "POD")
    by_role = {f.dxf_role: f.id for f in fstore.list_by_version(v.id)}
    assert by_role == {"POD": "b"}
    # The replaced file's content row survives (content is shared storage).
    assert fstore.content_exists("a") is True
    assert fstore.binding_count("a") == 0


def test_list_by_version(tmp_db):
    vstore, _, fstore = _bootstrap(tmp_db)
    _, v = vstore.create_product("Pkg-3", "v1")
    for i, role in enumerate(VALID_ROLES):
        _add_file(fstore, v.id, f"id{i}", f"x{i}.dxf", role)
    items = fstore.list_by_version(v.id)
    assert {f.dxf_role for f in items} == set(VALID_ROLES)


def test_set_match_saved(tmp_db):
    vstore, _, fstore = _bootstrap(tmp_db)
    _, v = vstore.create_product("Pkg-4", "v1")
    _add_file(fstore, v.id, "z1", "z.dxf", "SBT")
    assert fstore.get(v.id, "z1").match_saved is False
    fstore.set_match_saved(v.id, "z1", True)
    assert fstore.get(v.id, "z1").match_saved is True
    fstore.set_match_saved(v.id, "z1", False)
    assert fstore.get(v.id, "z1").match_saved is False


# ---- multi-DXF-per-role coexistence rules --------------------------------
def test_same_role_accumulates_files_under_multi(tmp_db):
    """All DXFs in a (version, role) are tagged `multi`; the binding table
    distinguishes them by file id alone. Three siblings coexist."""
    vstore, _, fstore = _bootstrap(tmp_db)
    _, v = vstore.create_product("Pkg-MV", "v1")
    _add_file(fstore, v.id, "f1", "a.dxf", "SBT", dxf_view="multi")
    _add_file(fstore, v.id, "f2", "b.dxf", "SBT", dxf_view="multi")
    _add_file(fstore, v.id, "f3", "c.dxf", "SBT", dxf_view="multi")
    ids = {f.id for f in fstore.list_by_version(v.id) if f.dxf_role == "SBT"}
    assert ids == {"f1", "f2", "f3"}


def test_same_role_allows_multiple_files(tmp_db):
    """A (version, role) accepts any number of DXFs; the DB enforces no
    per-slot unique constraint. View coverage is the job of
    `app.product_views.resolve_views`, not the schema."""
    vstore, _, fstore = _bootstrap(tmp_db)
    _, v = vstore.create_product("Pkg-Multi", "v1")
    _add_file(fstore, v.id, "a", "a.dxf", "BD", dxf_view="multi")
    # Adding a second file under the same (version, role) must succeed.
    _add_file(fstore, v.id, "b", "b.dxf", "BD", dxf_view="multi")
    ids = {f.id for f in fstore.list_by_version(v.id) if f.dxf_role == "BD"}
    assert ids == {"a", "b"}


# test_legacy_row_backfilled_to_multi was removed (two-tier scope removed
# 2026-06-10, openspec add-product-versioning): the in-place dxf_view
# backfill migration no longer exists — a pre-versioning DB (files table
# with product_id) is rebuilt from scratch by
# app.dbschema.ensure_versioned_schema. The rebuild semantics is covered
# by tests/test_files.py::test_legacy_db_is_rebuilt_from_scratch.

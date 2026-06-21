"""Template dedup-on-commit: signature properties, per-library dedup scope, startup warning."""

from __future__ import annotations

import logging
import math
import uuid


from app.library import (
    Library,
    LibraryRegistry,
    Store,
    Template,
    TEMPLATE_DEDUP_BUCKET,
    template_signature,
)
from app.versions import VersionStore


LIB_ID = "lib-dedup"


# ---- helpers ---------------------------------------------------------------

def _square(x: float, y: float, side: float = 1.0) -> list[tuple[float, float]]:
    """Unit-ish square at (x, y) — closed polyline (5 points)."""
    return [
        (x, y),
        (x + side, y),
        (x + side, y + side),
        (x, y + side),
        (x, y),
    ]


def _pentagon(x: float, y: float) -> list[tuple[float, float]]:
    """5-point regular pentagon at (x, y) — distinct from any square."""
    pts = []
    for k in range(5):
        a = 2 * math.pi * k / 5 + math.pi / 2
        pts.append((x + math.cos(a), y + math.sin(a)))
    pts.append(pts[0])  # close
    return pts


def _make_template(
    class_name: str,
    point_sets: list[list[tuple[float, float]]],
) -> Template:
    return Template.from_entities(class_name, point_sets)


def _lib(tmp_db, lib_id=LIB_ID) -> Library:
    """Open (creating on first call) a test library — production libraries
    are created by version creation; tests create one directly."""
    store = Store(tmp_db)
    if store.get_library(lib_id) is None:
        store.create_library(lib_id, "Dedup Lib")
    return LibraryRegistry(store).get(lib_id)


# ---- §6: signature property tests ------------------------------------------

def test_signature_invariant_under_translation():
    a = [_square(0.0, 0.0)]
    b = [_square(100.0, -50.0)]
    assert template_signature(a) == template_signature(b)


def test_signature_invariant_under_entity_order_permutation():
    ea = _square(0.0, 0.0)
    eb = _square(10.0, 0.0)
    assert template_signature([ea, eb]) == template_signature([eb, ea])


def test_signature_invariant_under_vertex_order_permutation():
    pts = _square(0.0, 0.0)
    shuffled = [pts[2], pts[0], pts[4], pts[1], pts[3]]
    assert template_signature([pts]) == template_signature([shuffled])


def test_signature_distinguishes_under_rotation():
    pts = _square(0.0, 0.0, side=1.0)
    # 90° rotation about the square's centre (0.5, 0.5)
    rotated = [(0.5 - (p[1] - 0.5), 0.5 + (p[0] - 0.5)) for p in pts]
    assert template_signature([pts]) != template_signature([rotated])


def test_signature_distinguishes_under_above_bucket_drift():
    # 2 × bucket grid (= 2 × 10⁻⁴ mm) — guaranteed to land in adjacent buckets.
    base = _square(0.0, 0.0)
    drifted = list(base)
    drifted[0] = (base[0][0] + 2.0 / TEMPLATE_DEDUP_BUCKET, base[0][1])
    assert template_signature([base]) != template_signature([drifted])


def test_signature_collapses_sub_bucket_drift():
    base = _square(0.0, 0.0)
    drifted = list(base)
    drifted[0] = (base[0][0] + 1e-6, base[0][1])  # 100× finer than bucket
    assert template_signature([base]) == template_signature([drifted])


def test_signature_function_is_deterministic():
    geom = [_square(0.0, 0.0), _square(10.0, 0.0), _square(0.0, 10.0)]
    assert template_signature(geom) == template_signature(geom)


# ---- §7: add_template_for_file dedup behaviour -----------------------------
# Dedup scope is (library_id, class_name) — the old product_id kwarg and
# the per-product scope split died with the two-tier scope removal
# (2026-06-10, openspec add-product-versioning).

def test_add_template_dedup_returns_existing_in_same_library(tmp_db):
    """Second add with translation-equivalent geometry is dedup'd."""
    lib = _lib(tmp_db)
    t1 = _make_template("SMD-2T", [_square(0.0, 0.0)])
    t2 = _make_template("SMD-2T", [_square(100.0, 100.0)])  # translation-equiv

    stored1, existed1 = lib.add_template_for_file(t1)
    stored2, existed2 = lib.add_template_for_file(t2)

    assert existed1 is False
    assert existed2 is True
    assert stored1.id == t1.id
    assert stored2.id == t1.id  # returns the existing one
    assert lib.count("SMD-2T") == 1

    # Persistent store sees only one row.
    _, _, by_cls = lib.store.load_library(lib.library_id)
    assert len(by_cls.get("SMD-2T", [])) == 1


def test_add_template_no_dedup_across_classes(tmp_db):
    lib = _lib(tmp_db)
    geom = [_square(0.0, 0.0)]
    t1 = _make_template("SMD-2T", geom)
    t2 = _make_template("FiducialCircle", geom)

    _, existed1 = lib.add_template_for_file(t1)
    _, existed2 = lib.add_template_for_file(t2)

    assert existed1 is False
    assert existed2 is False
    assert lib.count("SMD-2T") == 1
    assert lib.count("FiducialCircle") == 1


def test_add_template_no_dedup_across_libraries(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    lib_a = reg.create("Lib A")
    lib_b = reg.create("Other")

    geom = [_square(0.0, 0.0)]
    t1 = _make_template("SMD-2T", geom)
    t2 = _make_template("SMD-2T", geom)

    _, existed1 = lib_a.add_template_for_file(t1)
    _, existed2 = lib_b.add_template_for_file(t2)

    assert existed1 is False
    assert existed2 is False
    assert lib_a.count("SMD-2T") == 1
    assert lib_b.count("SMD-2T") == 1


# Replaces test_add_template_dedup_for_product_scoped_within_same_product /
# test_add_template_no_dedup_for_product_scoped_across_products (two-tier
# scope removed 2026-06-10, openspec add-product-versioning): there is no
# product scope anymore — each version owns its library, so "same product"
# became "same version's library" (dedup'd) and "different product" became
# "two different versions' libraries" (two rows).

def test_dedup_within_one_versions_library(tmp_db):
    store = Store(tmp_db)
    vstore = VersionStore(tmp_db)
    _, v = vstore.create_product("Prod-1", "v1")
    lib = Library(v.library_id, store)

    t1 = _make_template("Substrate", [_square(0.0, 0.0)])
    t2 = _make_template("Substrate", [_square(50.0, 0.0)])  # translation-equiv
    _, existed1 = lib.add_template_for_file(t1)
    _, existed2 = lib.add_template_for_file(t2)

    assert existed1 is False
    assert existed2 is True
    _, _, by_cls = store.load_library(v.library_id)
    assert len(by_cls.get("Substrate", [])) == 1


def test_no_dedup_across_two_versions_libraries(tmp_db):
    store = Store(tmp_db)
    vstore = VersionStore(tmp_db)
    _, v1 = vstore.create_product("Prod-1", "v1")
    _, v2 = vstore.create_product("Prod-2", "v1")
    lib1 = Library(v1.library_id, store)
    lib2 = Library(v2.library_id, store)

    t1 = _make_template("Substrate", [_square(0.0, 0.0)])
    t2 = _make_template("Substrate", [_square(50.0, 0.0)])  # translation-equiv
    _, existed1 = lib1.add_template_for_file(t1)
    _, existed2 = lib2.add_template_for_file(t2)

    assert existed1 is False
    assert existed2 is False

    # Each version's library sees its own row in isolation.
    _, _, by_cls_v1 = store.load_library(v1.library_id)
    _, _, by_cls_v2 = store.load_library(v2.library_id)
    assert len(by_cls_v1.get("Substrate", [])) == 1
    assert len(by_cls_v2.get("Substrate", [])) == 1


# ---- §9: startup WARNING ---------------------------------------------------

def test_load_library_warns_on_pre_dedup_duplicates(tmp_db, caplog):
    # Seed two same-signature rows directly via the store, bypassing dedup.
    store = Store(tmp_db)
    store.create_library(LIB_ID, "Dedup Lib")
    t1 = _make_template("SMD-2T", [_square(0.0, 0.0)])
    t2 = _make_template("SMD-2T", [_square(100.0, 100.0)])
    # Re-stamp t2 with a fresh id so the store's PK doesn't collide; same
    # signature is what matters.
    t2_clone = Template.from_entities(t2.class_name, t2.entity_point_sets)
    store.insert_template(LIB_ID, t1)
    store.insert_template(LIB_ID, t2_clone)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.library"):
        # Construct Library directly — that's what LibraryRegistry.get does
        # internally and where the warning fires.
        Library(LIB_ID, store)

    warn_records = [
        r for r in caplog.records
        if r.name == "app.library" and r.levelno == logging.WARNING
    ]
    assert len(warn_records) == 1
    msg = warn_records[0].getMessage()
    assert LIB_ID in msg
    assert "SMD-2T" in msg
    assert "2 templates" in msg


# ---- §8: commit endpoint round-trip ----------------------------------------

def test_commit_endpoint_surfaces_already_existed_flag():
    """Full TestClient round-trip: two identical commits → second carries
    `already_existed: true` with the SAME template_id, and `count` does
    not increment. Commits land in the version's library (commit is
    version-scoped via the `version_id` query param)."""
    import json
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE, READY
    from app.main import app
    from app.storage import parsed_path
    from app.versions import VERSION_STORE

    # Fresh product+version on the module singletons so prior tests' rows
    # can't collide; unique class/file ids for the same reason.
    cls = f"DedupTest-{uuid.uuid4().hex[:8]}"
    fid = f"dedup-test-{uuid.uuid4().hex[:8]}"
    _, version = VERSION_STORE.create_product(
        f"DedupProd-{uuid.uuid4().hex[:8]}", "v1",
    )

    # Hand-craft a parsed JSON with two distinct handles, each a polyline.
    primitives = [
        {
            "type": "polyline", "handle": "H1", "layer": "0",
            "points": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]],
        },
        {
            "type": "polyline", "handle": "H2", "layer": "0",
            "points": [[2.0, 0.0], [3.0, 0.0], [3.0, 1.0], [2.0, 1.0], [2.0, 0.0]],
        },
    ]
    pp = parsed_path(version.id, fid)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(json.dumps({
        "primitives": primitives,
        "bbox": [0.0, 0.0, 3.0, 1.0],
    }))

    try:
        FILE_STORE.register_content(fid, f"{fid}.dxf", 1)
        FILE_STORE.bind(version.id, "SBT", fid, initial_status=READY)
        with TestClient(app) as client:
            body = {"class_name": cls, "handles": ["H1", "H2"]}
            params = {"version_id": version.id}
            r1 = client.post(f"/api/files/{fid}/commit", params=params, json=body)
            assert r1.status_code == 200, r1.text
            d1 = r1.json()
            assert d1["already_existed"] is False
            assert d1["class_name"] == cls
            assert d1["library_id"] == version.library_id

            r2 = client.post(f"/api/files/{fid}/commit", params=params, json=body)
            assert r2.status_code == 200, r2.text
            d2 = r2.json()
            assert d2["already_existed"] is True
            assert d2["template_id"] == d1["template_id"]
            assert d2["count"] == d1["count"]
    finally:
        pp.unlink(missing_ok=True)


def test_load_library_no_warning_when_no_duplicates(tmp_db, caplog):
    store = Store(tmp_db)
    store.create_library(LIB_ID, "Dedup Lib")
    t = _make_template("SMD-2T", [_square(0.0, 0.0)])
    store.insert_template(LIB_ID, t)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.library"):
        Library(LIB_ID, store)

    warn_records = [
        r for r in caplog.records
        if r.name == "app.library" and r.levelno == logging.WARNING
    ]
    assert warn_records == []

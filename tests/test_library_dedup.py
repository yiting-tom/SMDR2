"""Template dedup-on-commit: signature properties, scope-aware dedup, startup warning."""

from __future__ import annotations

import logging
import math
import uuid

import pytest

from app.library import (
    DEFAULT_LIBRARY_ID,
    Library,
    LibraryRegistry,
    Store,
    Template,
    TEMPLATE_DEDUP_BUCKET,
    template_signature,
)


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


def _default_lib(tmp_db) -> Library:
    return LibraryRegistry(Store(tmp_db)).get(DEFAULT_LIBRARY_ID)


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

def test_add_template_dedup_returns_existing_for_library_scoped(tmp_db):
    """SMD-2T is library-scoped; second add with same geometry is dedup'd."""
    lib = _default_lib(tmp_db)
    t1 = _make_template("SMD-2T", [_square(0.0, 0.0)])
    t2 = _make_template("SMD-2T", [_square(100.0, 100.0)])  # translation-equiv

    stored1, existed1 = lib.add_template_for_file(t1, product_id=None)
    stored2, existed2 = lib.add_template_for_file(t2, product_id=None)

    assert existed1 is False
    assert existed2 is True
    assert stored1.id == t1.id
    assert stored2.id == t1.id  # returns the existing one
    assert lib.count("SMD-2T") == 1

    # Persistent store sees only one row.
    _, _, by_cls = lib.store.load_library(lib.library_id)
    assert len(by_cls.get("SMD-2T", [])) == 1


def test_add_template_no_dedup_across_classes(tmp_db):
    lib = _default_lib(tmp_db)
    geom = [_square(0.0, 0.0)]
    t1 = _make_template("SMD-2T", geom)
    t2 = _make_template("FiducialCircle", geom)

    _, existed1 = lib.add_template_for_file(t1, product_id=None)
    _, existed2 = lib.add_template_for_file(t2, product_id=None)

    assert existed1 is False
    assert existed2 is False
    assert lib.count("SMD-2T") == 1
    assert lib.count("FiducialCircle") == 1


def test_add_template_no_dedup_across_libraries(tmp_db):
    reg = LibraryRegistry(Store(tmp_db))
    lib_a = reg.get(DEFAULT_LIBRARY_ID)
    lib_b = reg.create("Other")

    geom = [_square(0.0, 0.0)]
    t1 = _make_template("SMD-2T", geom)
    t2 = _make_template("SMD-2T", geom)

    _, existed1 = lib_a.add_template_for_file(t1, product_id=None)
    _, existed2 = lib_b.add_template_for_file(t2, product_id=None)

    assert existed1 is False
    assert existed2 is False
    assert lib_a.count("SMD-2T") == 1
    assert lib_b.count("SMD-2T") == 1


def test_add_template_dedup_for_product_scoped_within_same_product(tmp_db):
    """Substrate is product-scoped; second add to same product is dedup'd."""
    lib = _default_lib(tmp_db)
    t1 = _make_template("Substrate", [_square(0.0, 0.0)])
    t2 = _make_template("Substrate", [_square(50.0, 0.0)])  # translation-equiv

    _, existed1 = lib.add_template_for_file(t1, product_id="prod-1")
    _, existed2 = lib.add_template_for_file(t2, product_id="prod-1")

    assert existed1 is False
    assert existed2 is True

    # Store-side check (cache is unreliable for product-scoped; rely on store)
    _, _, by_cls_p1 = lib.store.load_library(lib.library_id, product_id="prod-1")
    assert len(by_cls_p1.get("Substrate", [])) == 1


def test_add_template_no_dedup_for_product_scoped_across_products(tmp_db):
    lib = _default_lib(tmp_db)
    t1 = _make_template("Substrate", [_square(0.0, 0.0)])
    t2 = _make_template("Substrate", [_square(50.0, 0.0)])  # translation-equiv

    _, existed1 = lib.add_template_for_file(t1, product_id="prod-1")
    _, existed2 = lib.add_template_for_file(t2, product_id="prod-2")

    assert existed1 is False
    assert existed2 is False

    # Each product sees its own row in isolation.
    _, _, by_cls_p1 = lib.store.load_library(lib.library_id, product_id="prod-1")
    _, _, by_cls_p2 = lib.store.load_library(lib.library_id, product_id="prod-2")
    assert len(by_cls_p1.get("Substrate", [])) == 1
    assert len(by_cls_p2.get("Substrate", [])) == 1


# ---- §9: startup WARNING ---------------------------------------------------

def test_load_library_warns_on_pre_dedup_duplicates(tmp_db, caplog):
    # Seed two same-signature rows directly via the store, bypassing dedup.
    store = Store(tmp_db)
    t1 = _make_template("SMD-2T", [_square(0.0, 0.0)])
    t2 = _make_template("SMD-2T", [_square(100.0, 100.0)])
    # Re-stamp t2 with a fresh id so the store's PK doesn't collide; same
    # signature is what matters.
    t2_clone = Template.from_entities(t2.class_name, t2.entity_point_sets)
    store.insert_template(DEFAULT_LIBRARY_ID, t1, product_id=None)
    store.insert_template(DEFAULT_LIBRARY_ID, t2_clone, product_id=None)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.library"):
        # Construct Library directly — that's what LibraryRegistry.get does
        # internally and where the warning fires.
        Library(DEFAULT_LIBRARY_ID, store)

    warn_records = [
        r for r in caplog.records
        if r.name == "app.library" and r.levelno == logging.WARNING
    ]
    assert len(warn_records) == 1
    msg = warn_records[0].getMessage()
    assert DEFAULT_LIBRARY_ID in msg
    assert "SMD-2T" in msg
    assert "2 templates" in msg


# ---- §8: commit endpoint round-trip ----------------------------------------

def test_commit_endpoint_surfaces_already_existed_flag():
    """Full TestClient round-trip: two identical commits → second carries
    `already_existed: true` with the SAME template_id, and `count` does
    not increment."""
    import json
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE, READY
    from app.main import app
    from app.storage import parsed_path

    # Unique class so prior tests' rows in the singleton library don't
    # collide; unique file id so the parsed-file write doesn't collide.
    cls = f"DedupTest-{uuid.uuid4().hex[:8]}"
    fid = f"dedup-test-{uuid.uuid4().hex[:8]}"

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
    parsed_path(fid).write_text(json.dumps({
        "primitives": primitives,
        "bbox": [0.0, 0.0, 3.0, 1.0],
    }))

    try:
        FILE_STORE.register(fid, f"{fid}.dxf", 1, initial_status=READY)
        with TestClient(app) as client:
            body = {"class_name": cls, "handles": ["H1", "H2"]}
            r1 = client.post(f"/api/files/{fid}/commit", json=body)
            assert r1.status_code == 200, r1.text
            d1 = r1.json()
            assert d1["already_existed"] is False
            assert d1["class_name"] == cls

            r2 = client.post(f"/api/files/{fid}/commit", json=body)
            assert r2.status_code == 200, r2.text
            d2 = r2.json()
            assert d2["already_existed"] is True
            assert d2["template_id"] == d1["template_id"]
            assert d2["count"] == d1["count"]
    finally:
        parsed_path(fid).unlink(missing_ok=True)


def test_load_library_no_warning_when_no_duplicates(tmp_db, caplog):
    store = Store(tmp_db)
    t = _make_template("SMD-2T", [_square(0.0, 0.0)])
    store.insert_template(DEFAULT_LIBRARY_ID, t, product_id=None)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.library"):
        Library(DEFAULT_LIBRARY_ID, store)

    warn_records = [
        r for r in caplog.records
        if r.name == "app.library" and r.levelno == logging.WARNING
    ]
    assert warn_records == []

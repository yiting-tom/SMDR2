"""Smoke tests for the FastAPI surface — using TestClient against a temp DB."""

from __future__ import annotations

import os

import pytest


def test_classes_endpoint_lists_defaults(monkeypatch, tmp_path):
    # Point storage + DB at a tmp dir before importing main.
    monkeypatch.setenv("SMDR2_N_JOBS", "1")
    from fastapi.testclient import TestClient

    # Have to import after monkeypatching env / cwd to ensure
    # the singletons pick up the right paths. For safety, we reuse the
    # real singletons since they share a per-session SQLite under data/.
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/classes")
        assert r.status_code == 200
        classes = r.json()["classes"]
        names = {c["name"] for c in classes}
        # Default classes should be present.
        assert {"SMD-2T", "C4Ball", "BGABall", "Substrate"}.issubset(names)


def test_files_endpoint_returns_a_list():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.get("/api/files")
        assert r.status_code == 200
        assert "files" in r.json()


def test_upload_to_product_rejects_non_dxf():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        # Create a fresh product to upload into.
        cr = client.post("/api/products", json={"name": "test-product", "library_id": "default"})
        assert cr.status_code == 200
        pid = cr.json()["id"]
        r = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("a.txt", b"not dxf", "text/plain")},
            data={"dxf_role": "BD"},
        )
        assert r.status_code == 400


def test_match_endpoint_on_missing_file_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.post("/api/files/nonexistent/match", json={"handles": ["X"]})
        assert r.status_code == 404


def test_side_regions_patch_persists_and_normalises(tmp_path, monkeypatch):
    """PATCH /api/files/{id}/side-regions stores normalised rectangles and
    surfaces them on the next GET. Uses a freshly-registered stub file so
    we don't depend on the test.dxf preprocess pipeline."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app.main import app

    fid = "side-regions-test-1"
    FILE_STORE.register(fid, "stub.dxf", 1)

    with TestClient(app) as client:
        # Send a deliberately unnormalised top_view rect (x0 > x1), a normal
        # bottom_view rect, and a side_view rect.
        r = client.patch(
            f"/api/files/{fid}/side-regions",
            json={
                "top_view_rect": {"x0": 10, "y0": 5, "x1": 0, "y1": 0},
                "bottom_view_rect": {"x0": 50, "y0": 50, "x1": 60, "y1": 60},
                "side_view_rect": {"x0": 100, "y0": 100, "x1": 110, "y1": 110},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["top_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 5.0}
        assert body["bottom_view_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
        assert body["side_view_rect"] == {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0}
        assert body["match_saved"] is False

        # GET round-trips the rectangles on the file record.
        g = client.get(f"/api/files/{fid}").json()
        assert g["top_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 5.0}
        assert g["bottom_view_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
        assert g["side_view_rect"] == {"x0": 100.0, "y0": 100.0, "x1": 110.0, "y1": 110.0}


def test_side_regions_patch_clears_saved_match(tmp_path):
    """Editing regions must delete the cached match JSON and clear
    match_saved so the rule-checker won't see stale unprefixed keys."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app.main import app
    from app.storage import match_path

    fid = "side-regions-test-2"
    FILE_STORE.register(fid, "stub.dxf", 1)
    FILE_STORE.set_match_saved(fid, True)
    # Drop a placeholder match cache on disk to mimic a prior Save Match run.
    mp = match_path(fid)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text("{\"smd.0\": [[\"A\"]]}")

    with TestClient(app) as client:
        r = client.patch(
            f"/api/files/{fid}/side-regions",
            json={
                "top_view_rect": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
                "bottom_view_rect": None,
                "side_view_rect": None,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["match_saved"] is False

    assert not mp.exists(), "match cache should be deleted on region edit"
    assert FILE_STORE.get(fid).match_saved is False


def test_side_regions_patch_only_side_view_clears_saved_match(tmp_path):
    """PATCHing with only side_view_rect changing must also invalidate the
    saved Match JSON — the cache-invalidation hook covers all three rects."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app.main import app
    from app.storage import match_path

    fid = "side-regions-test-3"
    FILE_STORE.register(fid, "stub.dxf", 1)
    FILE_STORE.set_match_saved(fid, True)
    mp = match_path(fid)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text("{\"smd.0\": [[\"A\"]]}")

    with TestClient(app) as client:
        r = client.patch(
            f"/api/files/{fid}/side-regions",
            json={
                "top_view_rect": None,
                "bottom_view_rect": None,
                "side_view_rect": {"x0": 0, "y0": 0, "x1": 5, "y1": 5},
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["match_saved"] is False
        assert r.json()["side_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 5.0, "y1": 5.0}

    assert not mp.exists()
    assert FILE_STORE.get(fid).match_saved is False


def test_side_regions_patch_on_missing_file_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.patch(
            "/api/files/nonexistent/side-regions",
            json={
                "top_view_rect": None,
                "bottom_view_rect": None,
                "side_view_rect": None,
            },
        )
        assert r.status_code == 404


# ---- multi-DXF-per-role API tests ---------------------------------------
# Minimal valid DXF — enough bytes to pass the upload's "non-empty" check
# and the .dxf filename check. The upload pipeline parses asynchronously
# and tolerates malformed bytes by transitioning the file to status=error
# without breaking the upload-time contract we test below.
_STUB_DXF = b"0\nEOF\n"


def _new_product(client, name: str) -> str:
    r = client.post("/api/products", json={"name": name, "library_id": "default"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_upload_two_files_same_role_coexist():
    """A (product, role) accumulates DXFs; uploading a second file under
    the same role doesn't evict the first when no replace_file_id is sent."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid = _new_product(client, "two-files-coexist")

        r1 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        assert r1.status_code == 200, r1.text

        r2 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        assert r2.status_code == 200, r2.text

        g = client.get(f"/api/products/{pid}").json()
        ids = {f["id"] for f in g["files_by_role_all"]["SBT"]}
        assert ids == {r1.json()["file_id"], r2.json()["file_id"]}
        assert len(ids) == 2


def test_upload_with_replace_file_id_evicts_target():
    """`replace_file_id` evicts that specific file before the new one lands."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid = _new_product(client, "replace-specific")

        r1 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        old_id = r1.json()["file_id"]

        r2 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT", "replace_file_id": old_id},
        )
        assert r2.status_code == 200, r2.text

        g = client.get(f"/api/products/{pid}").json()
        ids = [f["id"] for f in g["files_by_role_all"]["SBT"]]
        assert old_id not in ids
        assert ids == [r2.json()["file_id"]]


def test_upload_replace_file_id_must_match_product_and_role():
    """Replacing across products or roles is rejected so the eviction
    can't cross slot boundaries by accident."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid_a = _new_product(client, "replace-cross-a")
        pid_b = _new_product(client, "replace-cross-b")

        r = client.post(
            f"/api/products/{pid_a}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        fid_a = r.json()["file_id"]

        # Try to replace product A's file via product B's endpoint.
        cross = client.post(
            f"/api/products/{pid_b}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT", "replace_file_id": fid_a},
        )
        assert cross.status_code == 400


def test_two_files_can_each_mark_same_view():
    """When two DXFs share a (product, role), each file may independently
    mark its own top/bottom/side region rectangles. Rule-check merges
    matches across files, so overlapping view labels are expected, not
    a conflict."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid = _new_product(client, "two-files-independent-views")

        r1 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        a_id = r1.json()["file_id"]
        r2 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        b_id = r2.json()["file_id"]

        ra = client.patch(
            f"/api/files/{a_id}/side-regions",
            json={
                "top_view_rect": {"x0": 0, "y0": 0, "x1": 5, "y1": 5},
                "bottom_view_rect": None,
                "side_view_rect": None,
            },
        )
        assert ra.status_code == 200, ra.text

        rb = client.patch(
            f"/api/files/{b_id}/side-regions",
            json={
                "top_view_rect": {"x0": 10, "y0": 10, "x1": 20, "y1": 20},
                "bottom_view_rect": None,
                "side_view_rect": None,
            },
        )
        assert rb.status_code == 200, rb.text

        ga = client.get(f"/api/files/{a_id}").json()
        gb = client.get(f"/api/files/{b_id}").json()
        assert ga["top_view_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 5.0, "y1": 5.0}
        assert gb["top_view_rect"] == {"x0": 10.0, "y0": 10.0, "x1": 20.0, "y1": 20.0}


def test_delete_one_of_many_keeps_siblings():
    """Removing one file from a multi-file role leaves the rest intact."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid = _new_product(client, "delete-keeps-siblings")
        a = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("a.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        b = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("b.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        c = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("c.dxf", _STUB_DXF + b"c", "application/dxf")},
            data={"dxf_role": "SBT"},
        )
        kept_ids = {a.json()["file_id"], c.json()["file_id"]}
        gone_id = b.json()["file_id"]

        d = client.delete(f"/api/products/{pid}/files/{gone_id}")
        assert d.status_code == 204

        g = client.get(f"/api/products/{pid}").json()
        remaining = {f["id"] for f in g["files_by_role_all"]["SBT"]}
        assert remaining == kept_ids


def test_delete_file_404_when_not_in_product():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid = _new_product(client, "delete-404")
        r = client.delete(f"/api/products/{pid}/files/no-such-file")
        assert r.status_code == 404


# ---- per-class match strategy API ---------------------------------------
def _fresh_library(client, name: str) -> str:
    r = client.post("/api/libraries", json={"name": name})
    assert r.status_code == 200
    return r.json()["id"]


def test_class_listing_includes_strategy_fields():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        lib_id = _fresh_library(client, "strategy-listing")
        r = client.get(f"/api/libraries/{lib_id}/classes")
        assert r.status_code == 200
        classes = r.json()["classes"]
        assert classes, "library should be seeded with default classes"
        for c in classes:
            assert c["match_strategy"] == "chamfer"
            assert c["bbox_ratio"] is None


def test_set_strategy_signature_defaults_bbox_ratio_to_005():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        lib_id = _fresh_library(client, "strategy-default-ratio")
        r = client.put(
            f"/api/libraries/{lib_id}/classes/Substrate/strategy",
            json={"strategy": "signature"},
        )
        assert r.status_code == 200
        assert r.json()["match_strategy"] == "signature"
        assert r.json()["bbox_ratio"] == 0.05
        sub = next(
            c for c in client.get(f"/api/libraries/{lib_id}/classes").json()["classes"]
            if c["name"] == "Substrate"
        )
        assert sub["match_strategy"] == "signature"
        assert sub["bbox_ratio"] == 0.05


def test_set_strategy_signature_with_explicit_bbox_ratio():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        lib_id = _fresh_library(client, "strategy-explicit-ratio")
        r = client.put(
            f"/api/libraries/{lib_id}/classes/Substrate/strategy",
            json={"strategy": "signature", "bbox_ratio": 0.1},
        )
        assert r.status_code == 200
        assert r.json()["bbox_ratio"] == 0.1


def test_flip_back_to_chamfer_clears_bbox_ratio():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        lib_id = _fresh_library(client, "strategy-clear")
        url = f"/api/libraries/{lib_id}/classes/Substrate/strategy"
        client.put(url, json={"strategy": "signature", "bbox_ratio": 0.05})
        r = client.put(url, json={"strategy": "chamfer"})
        assert r.status_code == 200
        assert r.json()["match_strategy"] == "chamfer"
        assert r.json()["bbox_ratio"] is None


def test_set_strategy_rejects_invalid_values():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        lib_id = _fresh_library(client, "strategy-invalid")
        url = f"/api/libraries/{lib_id}/classes/Substrate/strategy"
        # Unknown strategy value
        assert client.put(url, json={"strategy": "fuzzy"}).status_code == 400
        # bbox_ratio out of (0, 1] under signature
        for bad in (0, -0.1, 1.5):
            r = client.put(url, json={"strategy": "signature", "bbox_ratio": bad})
            assert r.status_code == 400, f"bbox_ratio={bad!r} should 400"


def test_set_strategy_unknown_library_or_class_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.put(
            "/api/libraries/no-such-lib/classes/Substrate/strategy",
            json={"strategy": "signature"},
        )
        assert r.status_code == 404
        lib_id = _fresh_library(client, "strategy-no-class")
        r2 = client.put(
            f"/api/libraries/{lib_id}/classes/DoesNotExist/strategy",
            json={"strategy": "signature"},
        )
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# RING / LID per-product mutual exclusion
# ---------------------------------------------------------------------------


def test_upload_lid_to_product_with_ring_returns_409():
    """RING and LID are mutually exclusive per product. The second
    upload to the opposite slot SHALL fail with 409 and the conflicting
    file id surfaced in the body so the dashboard can explain why."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid = _new_product(client, "ring-then-lid-rejected")

        r1 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("ring.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "RING"},
        )
        assert r1.status_code == 200, r1.text
        ring_id = r1.json()["file_id"]

        r2 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("lid.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "LID"},
        )
        assert r2.status_code == 409, r2.text
        detail = r2.json().get("detail", "")
        assert ring_id in detail
        assert "RING" in detail


def test_upload_ring_to_product_with_lid_returns_409():
    """Symmetric to the above: LID first, then RING is rejected."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid = _new_product(client, "lid-then-ring-rejected")

        r1 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("lid.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "LID"},
        )
        assert r1.status_code == 200, r1.text
        lid_id = r1.json()["file_id"]

        r2 = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("ring.dxf", _STUB_DXF + b"b", "application/dxf")},
            data={"dxf_role": "RING"},
        )
        assert r2.status_code == 409, r2.text
        detail = r2.json().get("detail", "")
        assert lid_id in detail
        assert "LID" in detail


def test_upload_lid_to_empty_product_succeeds():
    """A LID upload to a product holding neither RING nor LID should
    bind cleanly. Confirms LID is a first-class role, not just an
    enum value."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        pid = _new_product(client, "lid-on-empty-ok")

        r = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("lid.dxf", _STUB_DXF, "application/dxf")},
            data={"dxf_role": "LID"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["dxf_role"] == "LID"

        g = client.get(f"/api/products/{pid}").json()
        assert len(g["files_by_role_all"]["LID"]) == 1
        assert g["files_by_role_all"]["RING"] == []

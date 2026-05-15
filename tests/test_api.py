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
        assert {"smd", "bga_ball", "substrate"}.issubset(names)


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
        # Send a deliberately unnormalised frontside rect (x0 > x1) plus a
        # normal bottomside rect.
        r = client.patch(
            f"/api/files/{fid}/side-regions",
            json={
                "frontside_rect": {"x0": 10, "y0": 5, "x1": 0, "y1": 0},
                "bottomside_rect": {"x0": 50, "y0": 50, "x1": 60, "y1": 60},
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["frontside_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 5.0}
        assert body["bottomside_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}
        assert body["match_saved"] is False

        # GET round-trips the rectangles on the file record.
        g = client.get(f"/api/files/{fid}").json()
        assert g["frontside_rect"] == {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 5.0}
        assert g["bottomside_rect"] == {"x0": 50.0, "y0": 50.0, "x1": 60.0, "y1": 60.0}


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
                "frontside_rect": {"x0": 0, "y0": 0, "x1": 1, "y1": 1},
                "bottomside_rect": None,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["match_saved"] is False

    assert not mp.exists(), "match cache should be deleted on region edit"
    assert FILE_STORE.get(fid).match_saved is False


def test_side_regions_patch_on_missing_file_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.patch(
            "/api/files/nonexistent/side-regions",
            json={"frontside_rect": None, "bottomside_rect": None},
        )
        assert r.status_code == 404

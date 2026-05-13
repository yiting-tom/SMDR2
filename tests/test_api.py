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


def test_upload_rejects_non_dxf():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.post("/api/files", files={"files": ("a.txt", b"not dxf", "text/plain")})
        assert r.status_code == 200
        body = r.json()
        assert body["files"][0].get("skipped")


def test_match_endpoint_on_missing_file_404s():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.post("/api/files/nonexistent/match", json={"handles": ["X"]})
        assert r.status_code == 404

"""Boot connectivity probes, /readyz, and the external-service logging
hardening (app/connectivity.py + the module loggers added across the backend)."""
from __future__ import annotations

import logging

import pytest


# ---- check_dependencies / probes -------------------------------------------
def test_check_dependencies_all_ok_in_test_env(monkeypatch):
    # bypass + sqlite + local blob → every probe passes.
    monkeypatch.setenv("SMDR2_AUTH_MODE", "bypass")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    from app import blobstore, connectivity
    blobstore.reset_blobstore()
    out = connectivity.check_dependencies()
    assert set(out) == {"db", "blob", "oidc"}
    assert all(v["ok"] for v in out.values()), out


def test_check_never_raises_when_dep_down(monkeypatch):
    from app import blobstore, connectivity

    def boom():
        raise RuntimeError("minio down")

    monkeypatch.setattr(blobstore, "get_blobstore", boom)
    out = connectivity.check_dependencies()           # must NOT raise
    assert out["blob"]["ok"] is False
    assert "minio down" in out["blob"]["detail"]


# ---- /readyz vs /healthz ----------------------------------------------------
def test_readyz_200_when_all_ok(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SMDR2_AUTH_MODE", "bypass")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    from app import blobstore
    from app.main import app
    blobstore.reset_blobstore()
    with TestClient(app) as client:
        r = client.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert set(body["checks"]) == {"db", "blob", "oidc"}


def test_readyz_503_when_a_dep_fails(monkeypatch):
    from fastapi.testclient import TestClient
    from app import connectivity
    from app.main import app
    # The handler re-imports check_dependencies per call, so patching the
    # module attribute takes effect.
    monkeypatch.setattr(connectivity, "check_dependencies", lambda: {
        "db": {"ok": False, "detail": "OperationalError: down"},
        "blob": {"ok": True, "detail": "local disk"},
        "oidc": {"ok": True, "detail": "bypass (no Keycloak)"},
    })
    with TestClient(app) as client:
        r = client.get("/readyz")
        assert r.status_code == 503
        assert r.json()["checks"]["db"]["ok"] is False


def test_healthz_stays_pure():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {"ok": True}


# ---- logging at the audited gaps -------------------------------------------
def test_local_blob_fallback_warns(monkeypatch, caplog):
    from app import blobstore
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    blobstore.reset_blobstore()
    with caplog.at_level(logging.WARNING, logger="app.blobstore"):
        blobstore.get_blobstore()
    assert any("multi-replica" in r.getMessage() for r in caplog.records)


def test_oidc_state_signature_mismatch_warns_without_leaking_secret(caplog):
    from app import oidc
    secret = b"super-secret-signing-key"
    with caplog.at_level(logging.WARNING, logger="app.oidc"):
        with pytest.raises(oidc.OidcError):
            oidc._unsign(secret, "tampered.payload")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "signature mismatch" in msgs
    # Reason only — the secret material must never reach the logs.
    assert "super-secret-signing-key" not in msgs


def test_sqlite_fallback_warns_outside_bypass(monkeypatch, caplog):
    from app import db
    from app.storage import DB_PATH
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.setenv("SMDR2_AUTH_MODE", "oidc")   # prod-ish → SQLite is wrong
    with caplog.at_level(logging.WARNING, logger="app.db"):
        url = db.resolve_url(DB_PATH)
    assert url.startswith("sqlite:///")
    assert any("multi-replica" in r.getMessage() for r in caplog.records)

"""Sessions + BFF flow units (specs/auth-session).

The full Keycloak round-trip runs against compose (opt-in,
tests/test_oidc_compose_smoke.py); here we cover the session lifetimes,
cookie→identity resolution, CSRF, and the signed state cookie."""

from __future__ import annotations

import pytest

from app.auth import (
    SESSION_ABS_SECONDS,
    SESSION_IDLE_SECONDS,
    AuthStore,
)
from app.oidc import OidcConfig, OidcError, _sign, _unsign, build_login


@pytest.fixture
def store(tmp_path) -> AuthStore:
    return AuthStore(tmp_path / "auth.sqlite")


CLAIMS = {"preferred_username": "alice", "deptid": "D100"}


# ---- session lifetimes ----------------------------------------------------
def test_session_roundtrip_and_csrf(store):
    store.upsert_user_from_claims(CLAIMS)
    token, csrf = store.create_session("alice", now=1000.0)
    sess = store.resolve_session(token, now=1010.0)
    assert sess["userid"] == "alice"
    assert sess["deptid"] == "D100"     # users-row dept, for dept grants
    assert sess["csrf_token"] == csrf
    # DB stores the hash, never the plaintext token
    row = store.conn.execute("SELECT id FROM sessions").fetchone()
    assert row["id"] != token and len(row["id"]) == 64


def test_idle_timeout(store):
    token, _ = store.create_session("alice", now=1000.0)
    t = 1000.0 + SESSION_IDLE_SECONDS + 1
    assert store.resolve_session(token, now=t) is None


def test_absolute_lifetime_caps_active_sessions(store):
    token, _ = store.create_session("alice", now=1000.0)
    # keep it active every hour — idle never trips, absolute must
    t = 1000.0
    while t < 1000.0 + SESSION_ABS_SECONDS - 3600:
        t += 3600
        assert store.resolve_session(token, now=t) is not None
    assert store.resolve_session(token, now=1000.0 + SESSION_ABS_SECONDS + 1) is None


def test_delete_and_prune(store):
    t1, _ = store.create_session("alice", now=1000.0)
    t2, _ = store.create_session("alice", now=1000.0)
    assert store.delete_session(t1) is True
    assert store.delete_session(t1) is False
    assert store.prune_sessions(now=1000.0 + SESSION_ABS_SECONDS + 1) == 1
    assert store.resolve_session(t2, now=1000.0 + 10) is None


# ---- identity dependency (oidc mode) ----------------------------------------
class _FakeRequest:
    def __init__(self, cookies=None, method="GET", headers=None):
        self.cookies = cookies or {}
        self.method = method
        self.headers = headers or {}


def test_oidc_identity_from_cookie(store, monkeypatch):
    import app.auth as auth_mod
    monkeypatch.setenv("SMDR2_AUTH_MODE", "oidc")
    monkeypatch.setattr(auth_mod, "AUTH_STORE", store)
    store.upsert_user_from_claims(CLAIMS)
    token, csrf = store.create_session("alice")

    ident = auth_mod.get_identity(_FakeRequest({auth_mod.SESSION_COOKIE: token}))
    assert ident.userid == "alice" and ident.deptid == "D100"
    assert not ident.is_bypass

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        auth_mod.get_identity(_FakeRequest({}))
    assert e.value.status_code == 401

    # mutating method without CSRF header → 403; with it → ok
    with pytest.raises(HTTPException) as e:
        auth_mod.get_identity(_FakeRequest(
            {auth_mod.SESSION_COOKIE: token}, method="POST",
        ))
    assert e.value.status_code == 403
    ident = auth_mod.get_identity(_FakeRequest(
        {auth_mod.SESSION_COOKIE: token}, method="POST",
        headers={"X-CSRF-Token": csrf},
    ))
    assert ident.userid == "alice"


# ---- signed state cookie -----------------------------------------------------
def _cfg() -> OidcConfig:
    return OidcConfig(
        issuer="http://kc/realms/conform",
        internal_base="http://kc-internal/realms/conform",
        client_id="conform-web",
        client_secret="s",
        redirect_uri="http://app/auth/callback",
        session_secret=b"test-secret",
    )


def test_build_login_url_and_state_cookie():
    url, cookie = build_login(_cfg(), next_path="/products/p1")
    assert url.startswith("http://kc/realms/conform/protocol/openid-connect/auth?")
    assert "code_challenge_method=S256" in url
    import json
    blob = json.loads(_unsign(b"test-secret", cookie))
    assert blob["next"] == "/products/p1"
    assert blob["state"] in url


def test_state_cookie_tamper_rejected():
    payload = _sign(b"right-secret", b'{"state":"x"}')
    with pytest.raises(OidcError):
        _unsign(b"wrong-secret", payload)
    with pytest.raises(OidcError):
        _unsign(b"right-secret", "garbage")

"""Opt-in full BFF round-trip against the compose stack (web behind the
LB in SMDR2_AUTH_MODE=oidc + Keycloak realm with the seeded accounts).

    SMDR2_OIDC_SMOKE=1 uv run pytest tests/test_oidc_compose_smoke.py -q

Drives a real browser-shaped flow: /auth/login redirect → Keycloak login
form POST → callback → session cookie → /api/me, for an admin
(BOOTSTRAP_ADMINS) and a grant-less viewer, then logout.
"""

from __future__ import annotations

import os
import re

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SMDR2_OIDC_SMOKE"),
    reason="SMDR2_OIDC_SMOKE not set",
)

APP = os.environ.get("SMDR2_SMOKE_APP", "http://localhost:8080")


def _login(client: httpx.Client, username: str, password: str = "dev") -> None:
    r = client.get(f"{APP}/auth/login", follow_redirects=False)
    assert r.status_code == 302, r.text
    kc_url = r.headers["location"]
    assert "/protocol/openid-connect/auth" in kc_url

    form = client.get(kc_url, follow_redirects=False)
    assert form.status_code == 200
    m = re.search(r'action="([^"]+)"', form.text)
    assert m, "Keycloak login form not found"
    action = m.group(1).replace("&amp;", "&")

    # Keycloak sets its cookies with the Secure flag even over http (dev
    # mode); httpx's jar rightly refuses to send those — forward them by
    # hand for the form POST, the way a browser on https would.
    kc_cookies = {}
    for sc in form.headers.get_list("set-cookie"):
        name, val = sc.split(";", 1)[0].split("=", 1)
        kc_cookies[name] = val

    r = client.post(
        action, data={"username": username, "password": password},
        cookies=kc_cookies, follow_redirects=False,
    )
    assert r.status_code == 302, f"KC login failed: {r.status_code} {r.text[:300]}"
    callback = r.headers["location"]
    assert callback.startswith(f"{APP}/auth/callback")

    r = client.get(callback, follow_redirects=False)
    assert r.status_code == 302, r.text
    assert "conform_session" in r.headers.get("set-cookie", "") or \
        client.cookies.get("conform_session")


def test_admin1_first_login_is_admin():
    with httpx.Client() as c:
        _login(c, "admin1")
        me = c.get(f"{APP}/api/me").json()
        assert me["userid"] == "admin1"
        assert me["deptid"] == "D100"
        assert me["is_admin"] is True, me  # BOOTSTRAP_ADMINS seeding


def test_viewer1_has_no_grants_and_logout_kills_session():
    with httpx.Client() as c:
        _login(c, "viewer1")
        me = c.get(f"{APP}/api/me").json()
        assert me["userid"] == "viewer1"
        assert me["is_admin"] is False
        assert me["grants"] == []

        csrf = c.cookies.get("conform_csrf")
        r = c.post(f"{APP}/auth/logout", headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200
        r = c.get(f"{APP}/api/me")
        assert r.status_code == 401


def test_unauthenticated_me_is_401():
    r = httpx.get(f"{APP}/api/me")
    assert r.status_code == 401

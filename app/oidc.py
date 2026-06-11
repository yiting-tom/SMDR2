"""Keycloak BFF flow (specs/auth-session, design D6).

The backend owns the whole OIDC dance: Authorization Code + PKCE on the
browser-facing issuer URL, token exchange + JWKS verification over the
cluster-internal URL. The frontend only ever sees the HttpOnly session
cookie minted afterwards.

Dual-URL config (validated against compose Keycloak):
- OIDC_ISSUER         what tokens carry in `iss` (browser-facing)
- OIDC_INTERNAL_BASE  how this process reaches Keycloak (defaults to issuer)

In-flight login state (state + PKCE verifier + post-login redirect) rides
in a short-lived HMAC-signed cookie — no server storage, replicas
interchangeable: the login redirect can leave web-1 and the callback can
land on web-2.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

STATE_COOKIE = "conform_oidc"
STATE_TTL_SECONDS = 600.0


class OidcError(Exception):
    """Login-flow failure → HTTP 400 at the route."""


@dataclass
class OidcConfig:
    issuer: str
    internal_base: str
    client_id: str
    client_secret: str
    redirect_uri: str
    session_secret: bytes

    @classmethod
    def from_env(cls) -> "OidcConfig":
        issuer = os.environ.get("OIDC_ISSUER", "")
        if not issuer:
            raise OidcError("OIDC_ISSUER not configured")
        return cls(
            issuer=issuer.rstrip("/"),
            internal_base=(
                os.environ.get("OIDC_INTERNAL_BASE") or issuer
            ).rstrip("/"),
            client_id=os.environ.get("OIDC_CLIENT_ID", ""),
            client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
            redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
            session_secret=os.environ.get(
                "SESSION_SECRET", "dev-insecure"
            ).encode(),
        )


# ---- signed state cookie -----------------------------------------------------
def _sign(secret: bytes, payload: bytes) -> str:
    mac = hmac.new(secret, payload, hashlib.sha256).digest()
    return (base64.urlsafe_b64encode(payload).decode().rstrip("=")
            + "." + base64.urlsafe_b64encode(mac).decode().rstrip("="))


def _unsign(secret: bytes, value: str) -> bytes:
    try:
        p64, m64 = value.split(".", 1)
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        payload = base64.urlsafe_b64decode(pad(p64))
        mac = base64.urlsafe_b64decode(pad(m64))
    except Exception as e:
        raise OidcError("malformed state cookie") from e
    expect = hmac.new(secret, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expect):
        raise OidcError("state cookie signature mismatch")
    return payload


def build_login(cfg: OidcConfig, next_path: str = "/") -> tuple[str, str]:
    """→ (authorization_url, state_cookie_value)."""
    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).decode().rstrip("=")
    cookie = _sign(cfg.session_secret, json.dumps({
        "state": state, "verifier": verifier,
        "next": next_path, "ts": time.time(),
    }).encode())
    url = f"{cfg.issuer}/protocol/openid-connect/auth?" + urlencode({
        "client_id": cfg.client_id,
        "response_type": "code",
        "scope": "openid profile email",
        "redirect_uri": cfg.redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return url, cookie


def exchange_code(
    cfg: OidcConfig, code: str, state: str, state_cookie: str,
) -> tuple[dict, str]:
    """Validate state, swap the code for tokens over the internal URL,
    verify the id_token against JWKS. → (verified claims, next_path)."""
    import httpx
    from authlib.jose import JsonWebToken

    blob = json.loads(_unsign(cfg.session_secret, state_cookie))
    if time.time() - blob.get("ts", 0) > STATE_TTL_SECONDS:
        raise OidcError("login took too long — retry")
    if not state or not hmac.compare_digest(state, blob.get("state", "")):
        raise OidcError("state mismatch")

    token_url = f"{cfg.internal_base}/protocol/openid-connect/token"
    resp = httpx.post(token_url, data={
        "grant_type": "authorization_code",
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "code_verifier": blob["verifier"],
    }, timeout=10.0)
    if resp.status_code != 200:
        raise OidcError(f"token exchange failed: {resp.status_code} {resp.text[:200]}")
    tokens = resp.json()

    jwks = httpx.get(
        f"{cfg.internal_base}/protocol/openid-connect/certs", timeout=10.0
    ).json()
    jwt = JsonWebToken(["RS256"])
    claims = jwt.decode(
        tokens["id_token"], jwks,
        claims_options={
            "iss": {"essential": True, "value": cfg.issuer},
            "aud": {"essential": True, "value": cfg.client_id},
            "exp": {"essential": True},
        },
    )
    claims.validate()
    return dict(claims), blob.get("next") or "/"

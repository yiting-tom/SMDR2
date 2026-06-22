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
import logging
import os
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

logger = logging.getLogger(__name__)

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
        # No dev fallback: this key signs the in-flight state cookie. A weak
        # default silently shipped to production would let an attacker forge
        # login state. Fail loudly instead — `validate_startup_config` catches
        # this at boot so it never surfaces on a live login request.
        session_secret = os.environ.get("SESSION_SECRET", "")
        if not session_secret:
            raise OidcError("SESSION_SECRET not configured")
        return cls(
            issuer=issuer.rstrip("/"),
            internal_base=(
                os.environ.get("OIDC_INTERNAL_BASE") or issuer
            ).rstrip("/"),
            client_id=os.environ.get("OIDC_CLIENT_ID", ""),
            client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
            redirect_uri=os.environ.get("OIDC_REDIRECT_URI", ""),
            session_secret=session_secret.encode(),
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
        # Security signal: a forged/replayed state cookie. Reason only — the
        # cookie value and secret never go to the logs.
        logger.warning("OIDC state cookie signature mismatch")
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


def end_session_url(cfg: OidcConfig) -> str:
    """Keycloak RP-initiated logout URL (OIDC end_session_endpoint).

    Clearing our own session cookie is not enough: the Keycloak SSO
    session survives, so the very next /auth/login silently re-authes and
    the user appears never to have logged out. Sending the browser here
    terminates the IdP session too, then bounces back to the app root —
    which (now session-less AND SSO-less) lands on the Keycloak login.

    `post_logout_redirect_uri` is derived from the registered callback URL
    (the browser-facing origin), not request.base_url, which behind the
    BFF/proxy may be the cluster-internal host. Override with
    OIDC_POST_LOGOUT_REDIRECT_URI when the app origin differs.

    NOTE (ops): Keycloak validates `post_logout_redirect_uri` against the
    client's "Valid post logout redirect URIs". Register the app origin
    (e.g. https://app.example.com/* or `+`) or logout 400s at Keycloak.
    """
    post_logout = os.environ.get("OIDC_POST_LOGOUT_REDIRECT_URI", "")
    if not post_logout:
        u = urlsplit(cfg.redirect_uri)
        post_logout = f"{u.scheme}://{u.netloc}/"
    return f"{cfg.issuer}/protocol/openid-connect/logout?" + urlencode({
        "client_id": cfg.client_id,
        "post_logout_redirect_uri": post_logout,
    })


def exchange_code(
    cfg: OidcConfig, code: str, state: str, state_cookie: str,
) -> tuple[dict, str]:
    """Validate state, swap the code for tokens over the internal URL,
    verify the id_token against JWKS. → (verified claims, next_path)."""
    import httpx
    from authlib.jose import JsonWebToken

    from app.tlsconfig import ssl_verify
    verify = ssl_verify()

    blob = json.loads(_unsign(cfg.session_secret, state_cookie))
    if time.time() - blob.get("ts", 0) > STATE_TTL_SECONDS:
        logger.warning("OIDC login state expired (> %.0fs)", STATE_TTL_SECONDS)
        raise OidcError("login took too long — retry")
    if not state or not hmac.compare_digest(state, blob.get("state", "")):
        logger.warning("OIDC state mismatch on callback")
        raise OidcError("state mismatch")

    token_url = f"{cfg.internal_base}/protocol/openid-connect/token"
    resp = httpx.post(token_url, data={
        "grant_type": "authorization_code",
        "client_id": cfg.client_id,
        "client_secret": cfg.client_secret,
        "code": code,
        "redirect_uri": cfg.redirect_uri,
        "code_verifier": blob["verifier"],
    }, timeout=10.0, verify=verify)
    if resp.status_code != 200:
        # Body snippet (never the token — this is the error body) so a
        # login-broken incident has a server-side cause.
        logger.error("OIDC token exchange failed: status=%s body=%s",
                     resp.status_code, resp.text[:200])
        raise OidcError(f"token exchange failed: {resp.status_code} {resp.text[:200]}")
    tokens = resp.json()

    # JWKS fetch + id_token verification can raise (network, JWKS rotation, bad
    # signature, expired). Wrap so the callback returns a 400 instead of an
    # uncaught 500, and log the reason (no token material).
    try:
        jwks = httpx.get(
            f"{cfg.internal_base}/protocol/openid-connect/certs", timeout=10.0,
            verify=verify,
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
    except OidcError:
        raise
    except Exception as e:
        logger.error("OIDC id_token verification failed: %s: %s",
                     type(e).__name__, e)
        raise OidcError(f"id_token verification failed: {e}") from e
    return dict(claims), blob.get("next") or "/"

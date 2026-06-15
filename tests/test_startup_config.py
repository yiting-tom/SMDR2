"""validate_startup_config() fail-fast — no weak defaults reach production.

Covers the no-hardcode hardening: oidc/S3 secret groups must be fully set
once the corresponding mode is active; bypass mode (dev/tests) stays free.
"""
from __future__ import annotations

import pytest

from app.main import validate_startup_config

_OIDC_ENV = {
    "SMDR2_AUTH_MODE": "oidc",
    "SESSION_SECRET": "s",
    "OIDC_ISSUER": "http://kc/realms/conform",
    "OIDC_CLIENT_ID": "conform-web",
    "OIDC_CLIENT_SECRET": "cs",
    "OIDC_REDIRECT_URI": "http://app/auth/callback",
}


def test_bypass_mode_needs_no_secrets(monkeypatch):
    monkeypatch.setenv("SMDR2_AUTH_MODE", "bypass")
    for var in _OIDC_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SMDR2_AUTH_MODE", "bypass")
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    validate_startup_config()  # no raise


def test_oidc_mode_requires_full_group(monkeypatch):
    monkeypatch.delenv("S3_ENDPOINT_URL", raising=False)
    for k, v in _OIDC_ENV.items():
        monkeypatch.setenv(k, v)
    validate_startup_config()  # complete → ok
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        validate_startup_config()


def test_s3_endpoint_requires_credentials(monkeypatch):
    monkeypatch.setenv("SMDR2_AUTH_MODE", "bypass")
    monkeypatch.setenv("S3_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("S3_BUCKET", "conform")
    monkeypatch.delenv("S3_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("S3_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="S3_ACCESS_KEY_ID"):
        validate_startup_config()

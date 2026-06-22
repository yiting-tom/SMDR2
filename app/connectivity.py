"""Boot-time + `/readyz` connectivity checks for the external services
(MariaDB / MinIO / Keycloak).

Each probe is best-effort and NEVER raises — failures come back as
``{ok: False, detail: <reason>}`` so callers (the startup summary and the
``/readyz`` endpoint) stay in control. This is intentionally separate from
``validate_startup_config`` (which fails the pod fast on *missing config*):
these probes test *reachability*, are non-fatal at boot, and are what a k8s
readiness probe should gate traffic on.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def _check_db() -> tuple[bool, str]:
    try:
        from app import db
        from app.storage import DB_PATH
        conn = db.connect(DB_PATH)
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return True, "ok"
    except Exception as e:  # noqa: BLE001 — probe must never raise
        return False, f"{type(e).__name__}: {e}"


def _check_blob() -> tuple[bool, str]:
    endpoint = os.environ.get("S3_ENDPOINT_URL")
    try:
        from app.blobstore import get_blobstore
        get_blobstore().ping()
        return True, f"S3 {endpoint}" if endpoint else "local disk"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def _check_oidc() -> tuple[bool, str]:
    if os.environ.get("SMDR2_AUTH_MODE", "bypass") == "bypass":
        return True, "bypass (no Keycloak)"
    try:
        import httpx
        from app.oidc import OidcConfig
        from app.tlsconfig import ssl_verify
        cfg = OidcConfig.from_env()
        resp = httpx.get(
            f"{cfg.internal_base}/protocol/openid-connect/certs", timeout=5.0,
            verify=ssl_verify())
        resp.raise_for_status()
        return True, "JWKS ok"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


_CHECKS = {"db": _check_db, "blob": _check_blob, "oidc": _check_oidc}


def check_dependencies() -> dict[str, dict]:
    """Probe every external dependency. Never raises; returns
    ``{name: {"ok": bool, "detail": str}}``."""
    out: dict[str, dict] = {}
    for name, fn in _CHECKS.items():
        ok, detail = fn()
        out[name] = {"ok": ok, "detail": detail}
    return out


def log_startup_connectivity() -> dict[str, dict]:
    """Run the checks once at boot and log one line per dependency
    (INFO if reachable, ERROR if not). Non-fatal — the pod still starts."""
    results = check_dependencies()
    for name, r in results.items():
        if r["ok"]:
            logger.info("connectivity %s: ok (%s)", name, r["detail"])
        else:
            logger.error("connectivity %s: UNREACHABLE (%s)", name, r["detail"])
    return results

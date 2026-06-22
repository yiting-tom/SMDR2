"""TLS certificate-verification policy for outbound clients.

One env var, ``SSL_VERIFY``, controls how this process verifies the
certificates of the external services it calls over HTTPS — Keycloak (OIDC)
and MinIO/S3. On the company's internal network these are fronted by a
self-signed CA, so verification against the public trust store fails; this
switch lets the operator turn it off (or point at the internal CA bundle).

``SSL_VERIFY`` values:
- unset / "1" / "true" / "yes" / "on"   → ``True``  — verify (default, safe)
- "0" / "false" / "no" / "off"          → ``False`` — skip verification
- any other value                       → returned verbatim as a path to a CA
  bundle file or dir (both httpx and botocore accept a path for ``verify=``)

``ssl_verify()`` returns a value usable directly as the ``verify=`` argument of
``httpx`` and ``boto3.client``. Disabling verification is only safe on a
trusted internal segment — we emit one WARNING per process when it is off so a
production deploy that left it disabled is visible in the logs.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_warned = False


def ssl_verify() -> bool | str:
    """Resolve the ``verify=`` policy for outbound HTTPS clients from
    ``SSL_VERIFY``. See the module docstring for the accepted values."""
    raw = os.environ.get("SSL_VERIFY")
    if raw is None or not raw.strip():
        return True
    v = raw.strip()
    low = v.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        _warn_disabled()
        return False
    # Anything else is treated as a CA bundle path (custom internal CA).
    return v


def _warn_disabled() -> None:
    global _warned
    if not _warned:
        _warned = True
        logger.warning(
            "SSL_VERIFY is off — TLS certificate verification DISABLED for "
            "outbound calls (Keycloak/MinIO). Only safe on a trusted internal "
            "network; prefer pointing SSL_VERIFY at the internal CA bundle.",
        )

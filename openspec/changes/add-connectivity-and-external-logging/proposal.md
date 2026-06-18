## Why

The external services (MariaDB, MinIO, Keycloak) are reached lazily and
**logged nowhere** — there is no log when the app connects, succeeds, or fails;
a bad backend surfaces only as a request-time traceback on first use, and
`/healthz` returns `{ok:true}` without touching any dependency. Several other
failure points across the backend swallow errors or silently change behaviour
(DB reconnects, the LocalBlobStore fallback, OIDC token/JWKS failures, authz
denials). In a multi-replica k8s deploy this makes "login is broken" or
"files vanish across pods" incidents nearly undiagnosable.

This change adds (1) boot-time connectivity checks + a `/readyz` probe and a
startup summary, and (2) targeted logging at the silent failure points found in
a backend audit. It complements the job-layer logging from
`observability-launch-hardening` (different layer; no overlap).

## What Changes

**Connectivity (new):**
- `app/connectivity.py` — `check_dependencies()` probes DB (`SELECT 1`), blob
  store (`ping()` → S3 `head_bucket` / local dir writable), and Keycloak (JWKS
  fetch), returning per-service `{ok, detail}`. Each is best-effort, fast-timeout,
  and never raises.
- `BlobStore.ping()` on both `LocalBlobStore` and `S3BlobStore`.
- `GET /readyz` — runs the checks, returns 200 when all pass else 503 with the
  per-service detail (auth-exempt, for k8s readiness probes). `/healthz` stays
  pure liveness.
- `lifespan` logs a startup summary (auth mode, blob backend, DB dialect) and
  the connectivity results (INFO ok / ERROR fail) — non-fatal, so a degraded
  dependency is loud but the pod can still start and recover.

**Logging at audited gaps:**
- `app/db.py` (new logger): WARNING on the transparent reconnect+retry; INFO on
  engine creation (host/db only, never the password); WARNING when a store
  resolves to the SQLite fallback while not in bypass mode.
- `app/blobstore.py` (new logger): WARNING when falling back to `LocalBlobStore`
  (unsafe for multi-replica); INFO on S3 store init; WARNING (with context)
  before re-raising non-404 S3 `ClientError`s in the read/write ops.
- `app/oidc.py` (new logger): ERROR on token-exchange failure; ERROR on JWKS
  fetch / id_token verification failure **and wrap it in `OidcError`** so the
  callback returns 400 instead of an uncaught 500; WARNING on rejected login
  state (signature mismatch / expiry / state mismatch).
- `app/main.py`: INFO login success + `BOOTSTRAP_ADMINS` seed count in the
  lifespan/callback; the dead module `logger` gets used; WARNING when logout
  can't build the IdP end-session URL; WARNING on unreadable persisted artifacts;
  INFO/ERROR around `validate_startup_config`.
- `app/guards.py` (new logger): WARNING on authz (403) denials, INFO on
  edit-lock (423) denials — neither is in `audit_log` today, so a denied
  privileged action currently leaves no trace anywhere.
- `app/auth.py`: WARNING on CSRF-token mismatch in `get_identity`.
- `app/files.py` (new logger): WARNING when a DB JSON column (selected_layers /
  side-region rects / recover notes) is corrupt and silently nulled.

## Capabilities

### New Capabilities

- `connectivity-observability`: boot-time dependency checks, the `/readyz`
  probe, the startup summary, and the external-service / silent-failure logging
  contract.

## Impact

- **Code**: new `app/connectivity.py`; module loggers + log lines in `db.py`,
  `blobstore.py` (+ `ping()`), `oidc.py`, `guards.py`, `auth.py`, `files.py`,
  `main.py` (`/readyz`, lifespan summary). One behavioural fix: OIDC JWKS/JWT
  failure now → 400 (was an uncaught 500).
- **APIs**: adds `GET /readyz` (auth-exempt). No change to existing endpoints,
  payloads, DB schema, or migrations.
- **Secrets/logging hygiene**: log lines never emit secret material — DB URLs
  are logged host/db-only, OIDC logs reasons not tokens.
- **Tests**: `tests/test_connectivity.py` (checks + `/readyz` 200/503) and
  `caplog` assertions for the key WARNING/ERROR paths (DB reconnect, blob
  fallback, OIDC failure, authz denial). SQLite-suite behaviour unchanged.
- **Out of scope**: structured/JSON logging, log shipping, metrics/tracing — a
  stdlib-`logging`-only pass, consistent with the existing observability work.

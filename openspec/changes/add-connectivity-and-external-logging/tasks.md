## 1. Connectivity module + /readyz + startup summary

- [x] 1.1 Add `BlobStore.ping()` to `app/blobstore.py`: `LocalBlobStore` checks `DATA_DIR` exists/writable; `S3BlobStore` calls `head_bucket(Bucket=self.bucket)`. Raises on failure.
- [x] 1.2 New `app/connectivity.py`: `check_dependencies() -> dict[str, {ok, detail}]` probing db (`SELECT 1`), blob (`ping()`), oidc (JWKS fetch, only when `SMDR2_AUTH_MODE!=bypass`); each best-effort, never raises; fast timeouts. Plus `log_startup_connectivity()` that logs INFO/ERROR per service.
- [x] 1.3 `GET /readyz` in `app/main.py` (non-`/api`, auto-exempt): run checks → 200 / 503 with body. Keep `/healthz` as-is.
- [x] 1.4 `lifespan`: log startup summary (auth mode, blob backend, DB dialect) + call `log_startup_connectivity()`; log the `BOOTSTRAP_ADMINS` seed count.

## 2. Module logging at audited gaps

- [x] 2.1 `app/db.py`: add `logger`; WARNING on reconnect+retry (execute, ~line 276); INFO on engine creation (host/db only — never password) in `_engine_for`; WARNING in `resolve_url`/`resolve_database_url` path when falling back to SQLite while `SMDR2_AUTH_MODE!=bypass`.
- [x] 2.2 `app/blobstore.py`: add `logger`; WARNING on `LocalBlobStore` fallback in `get_blobstore`; INFO on `S3BlobStore` init; WARNING (with key/op) before re-raising non-404 `ClientError` in the read/write ops.
- [x] 2.3 `app/oidc.py`: add `logger`; ERROR on token-exchange non-200; wrap JWKS fetch + `jwt.decode/validate` in try → log ERROR and raise `OidcError` (so callback → 400 not 500); WARNING on state rejection (`_unsign` mismatch, TTL expiry, state mismatch).
- [x] 2.4 `app/guards.py`: add `logger`; WARNING before the 403 in `_enforce`; INFO before the 423; (optionally job_viewer_guard 403).
- [x] 2.5 `app/auth.py`: add `logger`; WARNING on CSRF mismatch in `get_identity`.
- [x] 2.6 `app/main.py`: INFO login-ok in `auth_callback`; WARNING in `auth_logout` when `end_session_url` can't be built; WARNING in `_load_json_or_http` on corrupt artifact; INFO/ERROR around `validate_startup_config`.
- [x] 2.7 `app/files.py`: add `logger`; WARNING on corrupt DB JSON column silently nulled (selected_layers / side-region rects / recover notes).

## 3. Tests

- [x] 3.1 `tests/test_connectivity.py`: `check_dependencies()` shape; all-ok → `/readyz` 200; a forced-failing check → `/readyz` 503; checks never raise when a dep is down (monkeypatch to raise).
- [x] 3.2 `caplog` assertions: DB reconnect WARNING; LocalBlobStore fallback WARNING; OIDC failure ERROR + 400 (not 500); authz 403 WARNING. Assert no password/token appears in emitted records.
- [x] 3.3 `uv run ruff check app tests` clean on touched files; `uv run pytest -q` green.

## 4. Docs

- [x] 4.1 `deploy/PRODUCTION_DEPLOY.md` + chart: mention `/readyz` for the k8s readiness probe (vs `/healthz` liveness); note the startup connectivity log.

## 5. Manual verification

- [ ] 5.1 **[USER]** In compose, hit `/readyz` (all green), then stop MariaDB and confirm `/readyz` → 503 and the startup/log lines name the failing service.

## 6. Archive

- [ ] 6.1 After tasks 1–5 pass, run `/opsx:archive add-connectivity-and-external-logging`.

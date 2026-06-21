## ADDED Requirements

### Requirement: Boot-time connectivity check and startup summary

The application SHALL probe its external dependencies at startup and log the
outcome. A `check_dependencies()` helper (`app/connectivity.py`) SHALL test the
database (a `SELECT 1`), the blob store (`BlobStore.ping()` — S3 `head_bucket`
or local-dir writability), and, in `oidc` auth mode, Keycloak (a JWKS fetch),
returning a per-service `{ok, detail}` map without ever raising. On startup the
`lifespan` SHALL log a one-line summary of the resolved auth mode and backends
plus each dependency result (INFO on success, ERROR on failure). A failed check
SHALL NOT by itself abort startup — the pod stays able to boot and recover —
distinct from `validate_startup_config`, which still fails fast on missing
required config.

#### Scenario: Startup logs each dependency result

- **WHEN** the app starts
- **THEN** it logs the auth mode and the chosen DB / blob backends
- **AND** it logs one result line per probed dependency (INFO if reachable,
  ERROR if not), without raising on an unreachable dependency

#### Scenario: A dependency check never raises

- **WHEN** a dependency (DB, blob, or Keycloak) is unreachable
- **THEN** `check_dependencies()` returns `{ok: false, detail: <reason>}` for it
- **AND** does not raise, so callers (startup, `/readyz`) stay in control

### Requirement: /readyz reports dependency reachability

The application SHALL expose `GET /readyz` (auth-exempt, like `/healthz`) that
runs the connectivity checks and returns HTTP 200 with the per-service results
when all pass, or HTTP 503 with the same body when any fail. `/healthz` SHALL
remain a pure liveness endpoint that does not touch any dependency.

#### Scenario: Ready when all dependencies pass

- **WHEN** `GET /readyz` is called and DB, blob, and Keycloak checks all pass
- **THEN** the response is HTTP 200 with each service marked ok

#### Scenario: Not ready when a dependency fails

- **WHEN** `GET /readyz` is called and at least one dependency check fails
- **THEN** the response is HTTP 503 with the failing service's detail

### Requirement: External-service and silent-failure events are logged

The backend SHALL emit log lines at the previously-silent failure and
state-change points, at appropriate levels, without ever logging secret
material (DB passwords, OIDC tokens):

- **DB** (`app/db.py`): WARNING on the transparent reconnect+retry of a dropped
  connection; INFO on engine creation (host/database only); WARNING when a store
  resolves to the SQLite fallback while not in bypass mode.
- **Blob** (`app/blobstore.py`): WARNING when falling back to `LocalBlobStore`
  (unsafe for multi-replica); INFO on S3 store initialisation; WARNING with
  context before re-raising a non-404 S3 error.
- **OIDC** (`app/oidc.py`): ERROR on token-exchange failure; ERROR on JWKS /
  id_token verification failure, surfaced as `OidcError` so the callback returns
  HTTP 400 rather than an uncaught 500; WARNING on rejected login state.
- **Authz** (`app/guards.py`): WARNING on a 403 role denial; INFO on a 423
  edit-lock denial.
- **Auth/session** (`app/auth.py`, `app/main.py`): WARNING on CSRF-token
  mismatch; INFO on login success and `BOOTSTRAP_ADMINS` seed count.
- **Artifacts** (`app/main.py`, `app/files.py`): WARNING on an unreadable
  persisted artifact and on a corrupt DB JSON column that is silently nulled.

#### Scenario: Dropped DB connection logs a reconnect

- **WHEN** a statement hits an invalidated DB connection outside a transaction
  and the layer transparently reconnects and retries
- **THEN** a WARNING is logged recording the reconnect

#### Scenario: Local blob fallback warns

- **WHEN** the blob store resolves with `S3_ENDPOINT_URL` unset and uses
  `LocalBlobStore`
- **THEN** a WARNING is logged that local storage is not safe for multi-replica

#### Scenario: OIDC verification failure is a logged 400

- **WHEN** the OIDC token exchange or id_token/JWKS verification fails during the
  callback
- **THEN** the failure is logged at ERROR (no token material)
- **AND** the callback responds HTTP 400, not an uncaught 500

#### Scenario: Authorization denial is logged

- **WHEN** a request is denied by the role guard (403) or the edit-lock guard
  (423)
- **THEN** a log line records the denial (WARNING for 403, INFO for 423),
  since these are not written to the audit log

#### Scenario: Secrets are never logged

- **WHEN** any of the above log lines is emitted
- **THEN** it contains no DB password and no OIDC token (DB URLs are logged
  host/database-only; OIDC logs reasons, not tokens)

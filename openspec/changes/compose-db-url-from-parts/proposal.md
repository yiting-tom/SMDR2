## Why

Production secrets come from the company Vault, which for the database stores
only a **username and password** — not a full connection URL. But the app and
Alembic today read a single `DATABASE_URL` (`app/db.py` `resolve_url`,
`alembic/env.py`), so an operator would have to hand-assemble the URL inside
Vault. Hand-building a URL is also a footgun: a DB password containing
`@ : / # ?` breaks a naively concatenated URL unless every special character is
percent-encoded.

Let the app compose the URL itself from parts, encoding the password safely via
SQLAlchemy's `URL.create`. Then Vault holds just `DB_USER` + `DB_PASSWORD`
(secret) and the non-secret host/port/db live in the ConfigMap — matching how
the Vault is actually structured, with no escaping hazard.

## What Changes

- Add `resolve_database_url()` in `app/db.py` resolving the DB URL from the
  environment with a fixed precedence:
  1. `DATABASE_URL` verbatim, if set (unchanged behaviour; compose/local/tests
     keep working exactly as before).
  2. else, if `DB_HOST` and `DB_USER` are set, compose via
     `sqlalchemy.engine.URL.create` from `DB_USER` / `DB_PASSWORD` (secret) and
     `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_DRIVERNAME` / `DB_CHARSET`
     (config) — the password is URL-encoded by `URL.create`.
  3. else the existing local SQLite fallback.
- `resolve_url` (per-store path → URL) and `alembic/env.py` both route through
  the new helper, so the app and migrations resolve the DB identically.
- Update the deploy docs / chart to document `DB_*` as an alternative to
  `DATABASE_URL`: `DB_USER` + `DB_PASSWORD` join the secret; `DB_HOST` /
  `DB_PORT` / `DB_NAME` go in the ConfigMap.

## Capabilities

### New Capabilities

- `database-connection`: how the app and Alembic resolve the database URL from
  the environment, including the parts-based fallback for secret stores that
  hold only username + password.

### Modified Capabilities

_None._

## Impact

- **Code**: `app/db.py` (new `resolve_database_url()`, `resolve_url` routes
  through it), `alembic/env.py` (use the helper).
- **Config contract**: `DATABASE_URL` stays the primary, highest-precedence
  input — nothing currently relying on it changes. `DB_USER` / `DB_PASSWORD`
  (secret) + `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_DRIVERNAME` / `DB_CHARSET`
  (config) are a new, optional alternative. Defaults: drivername
  `mysql+pymysql`, db `conform`, charset `utf8mb4`, port = driver default.
- **Secrets**: when splitting, the Vault secret keys become `DB_USER` +
  `DB_PASSWORD` instead of `DATABASE_URL`; everything else in the 5-key set is
  unchanged.
- **Tests**: `tests/test_db.py` gains cases for the parts fallback + password
  encoding + precedence. SQLite suite behaviour is unchanged.
- **APIs / DB schema / migrations**: none. This is connection-string plumbing,
  not a schema change.
- **Out of scope**: extending `validate_startup_config` to fail-fast on a
  missing DB group. Today a missing DB config silently falls back to SQLite;
  this change preserves that (no regression) and leaves a hard DB-config
  assertion as a separate decision.

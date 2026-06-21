## ADDED Requirements

### Requirement: Database URL resolved from env with a parts fallback

The app and Alembic SHALL resolve the database URL from the environment via a
single shared helper (`resolve_database_url()` in `app/db.py`, used by
`resolve_url` and `alembic/env.py`) with this fixed precedence:

1. If `DATABASE_URL` is set, it is used verbatim.
2. Else, if both `DB_HOST` and `DB_USER` are set, the URL is composed via
   `sqlalchemy.engine.URL.create` from `DB_USER` / `DB_PASSWORD` and
   `DB_HOST` / `DB_PORT` / `DB_NAME` / `DB_DRIVERNAME` / `DB_CHARSET`, so the
   password is URL-encoded and never hand-concatenated.
3. Else the local SQLite fallback is used.

Defaults when composing: `DB_DRIVERNAME` = `mysql+pymysql`, `DB_NAME` =
`conform`, `DB_CHARSET` = `utf8mb4`, `DB_PORT` = the driver default (unset).
A per-store path that is not the app's default DB path SHALL always resolve to
a SQLite file URL regardless of these variables, preserving test isolation.

#### Scenario: Explicit DATABASE_URL wins

- **WHEN** `DATABASE_URL` is set and the app resolves the default DB path
- **THEN** the resolved URL is `DATABASE_URL` verbatim, ignoring any `DB_*` parts

#### Scenario: Composed from parts when DATABASE_URL is absent

- **WHEN** `DATABASE_URL` is unset and `DB_HOST` + `DB_USER` (and optionally
  `DB_PASSWORD` / `DB_PORT` / `DB_NAME`) are set
- **THEN** the resolved URL is composed from those parts via `URL.create`
- **AND** the app and `alembic/env.py` resolve to the identical URL

#### Scenario: Password special characters are encoded

- **WHEN** the URL is composed from parts and `DB_PASSWORD` contains
  URL-significant characters (e.g. `@`, `:`, `/`, `#`, `?`)
- **THEN** the password is percent-encoded in the resolved URL such that the
  URL parses back to the original password (no manual escaping required)

#### Scenario: SQLite fallback when nothing is configured

- **WHEN** neither `DATABASE_URL` nor (`DB_HOST` + `DB_USER`) is set
- **THEN** the default DB path resolves to a local `sqlite:///…` file URL

#### Scenario: Non-default store paths stay SQLite

- **WHEN** a store opens a path other than the app's default DB path (e.g. a
  test's temp file) while DB env vars are set
- **THEN** that path resolves to a `sqlite:///…` URL, never the MariaDB URL

## 1. Code — DB URL resolution

- [x] 1.1 Add `resolve_database_url() -> str | None` in `app/db.py`: return `DATABASE_URL` if set; else if `DB_HOST` and `DB_USER` set, build via `sqlalchemy.engine.URL.create(drivername=DB_DRIVERNAME|"mysql+pymysql", username=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=int(DB_PORT) if set, database=DB_NAME|"conform", query={"charset": DB_CHARSET|"utf8mb4"})` rendered with the password visible; else `None`.
- [x] 1.2 Route `resolve_url` through it: for the default DB path, return `resolve_database_url()` when non-None; otherwise the SQLite path. Non-default paths stay SQLite (unchanged).
- [x] 1.3 In `alembic/env.py`, replace the inline `DATABASE_URL` read with `from app.db import resolve_database_url` → `_env_url = resolve_database_url() or f"sqlite:///{DB_PATH}"`.

## 2. Tests

- [x] 2.1 In `tests/test_db.py`: `DATABASE_URL` precedence over `DB_*` parts; compose-from-parts produces the expected `mysql+pymysql://…?charset=utf8mb4`; password with `@:/#?` round-trips (encode then `make_url` parses back to the original); SQLite fallback when nothing set; non-default path stays SQLite even with `DB_*` set.
- [x] 2.2 Run `uv run ruff check app tests` (touched files clean) and `uv run pytest -q`.

## 3. Docs / deploy

- [x] 3.1 `deploy/PRODUCTION_DEPLOY.md` §2: document `DB_USER` + `DB_PASSWORD` as the secret keys for the split option (vs the all-in-one `DATABASE_URL`), and `DB_HOST` / `DB_PORT` / `DB_NAME` as ConfigMap values; note the precedence.
- [x] 3.2 `deploy/helm/conform/values.yaml` + `deploy/k8s/conform.yaml`: mention the `DB_*` alternative in the secret/config comments.
- [x] 3.3 `deploy/README.md`: update the "5 把 secret" note to "`DATABASE_URL` 或 `DB_USER`+`DB_PASSWORD`".

## 4. Manual verification

- [ ] 4.1 **[USER]** With the company Vault holding only `DB_USER` + `DB_PASSWORD` and the ConfigMap holding `DB_HOST` / `DB_PORT` / `DB_NAME`, confirm the migrate Job and web pods connect (no `DATABASE_URL` set).

## 5. Archive

- [ ] 5.1 After tasks 1–4 pass, run `/opsx:archive compose-db-url-from-parts`.

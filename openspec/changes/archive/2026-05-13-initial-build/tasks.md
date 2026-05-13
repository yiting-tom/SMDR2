<!--
  This change records work that has already shipped. All tasks below are
  marked complete; the file exists so the change can be archived and its
  specs promoted to `openspec/specs/`.
-->

## 1. Project scaffold

- [x] 1.1 Initialize uv project, add core deps (fastapi, ezdxf, scipy, shapely, numpy, jinja2, python-multipart, pillow)
- [x] 1.2 Add dev deps (pytest, ruff, httpx) and pytest config (`pythonpath = ["."]`)
- [x] 1.3 `.gitignore` covering venv, generated `data/` subtrees, SQLite, pycache, pytest cache

## 2. DXF parsing and storage

- [x] 2.1 `app/storage.py` — directory layout + path helpers (`upload_path`, `parsed_path`, `prematch_path`, `match_path`, `rule_check_path`)
- [x] 2.2 `app/dxf.py` — `JSONBackend` subclass of `ezdxf.addons.drawing.backend.BackendInterface`, flattens to JSON primitives, returns `RenderOutput(primitives, bbox, background)`
- [x] 2.3 Bbox tracking inside the backend so the viewer can fit-to-screen on load

## 3. Library (SQLite + multi-library)

- [x] 3.1 `app/library.py` — `Template` dataclass + `Store` (single shared SQLite, WAL mode, RLock-protected writes)
- [x] 3.2 Schema: `libraries`, `classes` PK `(library_id, name)`, `templates` PK `id` + FK to libraries
- [x] 3.3 Migrations: detect pre-multi-library DBs, rebuild `classes` and `templates` to drop the stale `classes(name)` FK and add `library_id` columns
- [x] 3.4 `Library` class scoped to one `library_id`; `LibraryRegistry` manages per-library caches over a shared Store
- [x] 3.5 Default library `default` auto-created on every Store init; cannot be deleted

## 4. File metadata

- [x] 4.1 `app/files.py` — `FileRecord` + `FileStore` with status lifecycle constants
- [x] 4.2 Schema migration: rename legacy `queued/parsing/done` to `preprocessing/ready_to_match`, add `library_id` column
- [x] 4.3 `register`, `update_status`, `update_parsed`, `update_library`, `get`, `list_all`

## 5. Background jobs

- [x] 5.1 `app/jobs.py` — `ProcessPoolExecutor` with lazy init + module-level lifetime, `MAX_WORKERS = 2`
- [x] 5.2 `_preprocess_worker`: parse DXF → save parsed JSON → build entity index → run pre-match against the file's library → save prematch JSON
- [x] 5.3 `submit_preprocess(file_id, library_id)` + done-callback that updates FileStore with success/error
- [x] 5.4 Clean shutdown hook on FastAPI lifespan exit

## 6. Pattern matching

- [x] 6.1 `app/matching.py` — `EntityShape` with `from_points` that drops closing duplicate before centroid/PCA
- [x] 6.2 `signatures_compatible` pre-filter
- [x] 6.3 `align_score` — PCA-based alignment with 4 sign-variant search, scale check `[0.95, 1.05]`, chamfer scoring
- [x] 6.4 `_chamfer_brute` for clouds under `BRUTE_FORCE_CUTOFF = 50` points
- [x] 6.5 `_match_single_serial` — per-candidate match with cached template state and early-exit
- [x] 6.6 `_match_single` dispatcher with `n_jobs` parameter; chunks candidates across a lazy `ProcessPoolExecutor` when `n_jobs > 1`
- [x] 6.7 `_match_multi` — pose-based subgraph match (seed + PCA-local positions for other template entities + per-position shape verify)
- [x] 6.8 `find_matches` and `find_matches_from_pointsets` public entry points; both return `MatchOutput(matches, near_misses)`
- [x] 6.9 `N_JOBS` read from `SMDR2_N_JOBS` env var, default 1; lazy `_get_match_pool` keeps a single pool alive for module lifetime
- [x] 6.10 `shutdown_pool` hook for FastAPI lifespan

## 7. FastAPI surface

- [x] 7.1 `app/main.py` — FastAPI app with `lifespan` that auto-registers `data/test.dxf` and shuts down pool + jobs on exit
- [x] 7.2 LRU cache `_cached_parsed` + `_cached_shapes` keyed by `(path, mtime_ns)`, size 4
- [x] 7.3 Pages: `GET /` → dashboard, `GET /viewer/{file_id}` → viewer
- [x] 7.4 `POST /api/files` (multipart, accepts `library_id`), `GET /api/files`, `GET /api/files/{id}`, `PATCH /api/files/{id}` (reassign library + re-preprocess), `GET /api/jobs/{id}`
- [x] 7.5 Library endpoints: `GET /api/libraries`, `POST /api/libraries`, `DELETE /api/libraries/{id}` (rejects default), `GET /api/libraries/{id}/classes`, `GET /api/libraries/{id}/templates`
- [x] 7.6 Backwards-compat aliases `GET /api/classes` + `GET /api/templates` accept `?file_id=` to resolve the library
- [x] 7.7 Per-file processing endpoints: `/primitives`, `/match`, `/commit`, `/scan-all`, `/prematch`, `/match-json` (POST + GET), `/rule-check` (POST + GET)
- [x] 7.8 Template CRUD: `DELETE /api/templates/{id}`, `PATCH /api/templates/{id}` (move to another class)

## 8. Frontend

- [x] 8.1 `app/templates/dashboard.html` — library bar (selector + "+ New library"), drop-zone upload, files table with inline library dropdown
- [x] 8.2 `app/templates/viewer.html` — canvas, class toolbar nav, library switcher, rule-check side panel, library modal scaffolding
- [x] 8.3 `app/static/style.css` — dark theme, status pills, library pill / dropdown, class-coloured toolbar via `--class-color`, modal layout, rule panel
- [x] 8.4 `app/static/dashboard.js` — drag-and-drop upload, polling for in-progress files, library load + create + per-row PATCH
- [x] 8.5 `app/static/canvas.js` — Canvas 2D viewer with screen↔world transform, `ResizeObserver` for layout shifts
- [x] 8.6 AutoCAD-style interactions (middle-drag pan, L→R window vs R→L crossing, left-click pick with pickbox, shift toggle, Esc cascade)
- [x] 8.7 Chain mode toggle with lazy spatial-hash connectivity
- [x] 8.8 Per-class hotkeys (`1-0`, `q-p`), `S` = scan current selection, `Enter` = commit, `Esc` cascade
- [x] 8.9 Match preview overlay (cyan) + near-miss overlay (orange)
- [x] 8.10 `A` / button = scan-all overlay with per-class colours; auto-populated from prematch JSON on viewer load
- [x] 8.11 "Save Match" button → `POST /api/files/{id}/match-json`
- [x] 8.12 Library modal: list-by-class with foldable groups (sessionStorage-persisted), thumbnails, move + delete buttons
- [x] 8.13 Rule-check panel: list rules with pass/fail icons, hover and click-pin highlights, panel close
- [x] 8.14 Header library switcher (`<select>`) that reassigns the file's library on change with reload

## 9. Mock design rule check

- [x] 9.1 `app/rule_check.py` — `check_rules(dxf_path, match_json, entity_shapes=None) → RuleResult`
- [x] 9.2 Rule1: substrate-to-first-SMD combined-centroid distance > 5 mm

## 10. Tests

- [x] 10.1 `tests/conftest.py` — `test_dxf_path` and `tmp_db` fixtures
- [x] 10.2 `tests/test_dxf.py` — flatten smoke + handle index correctness
- [x] 10.3 `tests/test_library.py` — Library CRUD, persistence round-trip, multi-library isolation, default-library protection
- [x] 10.4 `tests/test_files.py` — FileStore lifecycle, idempotent re-register, ordering, error path
- [x] 10.5 `tests/test_matching.py` — signature filter, alignment under translate/rotate/mirror/scale, single + multi-entity match, n_jobs equivalence
- [x] 10.6 `tests/test_rule_check.py` — Rule1 pass/fail under threshold, missing-component fallbacks
- [x] 10.7 `tests/test_api.py` — TestClient smoke for `/api/classes`, `/api/files`, upload rejection, 404

## 11. Documentation

- [x] 11.1 `openspec init` and capture this change in `openspec/changes/initial-build/`

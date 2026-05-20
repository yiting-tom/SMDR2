## 1. Schema + persistence layer

- [x] 1.1 `app/library.py` — add `match_strategy TEXT NOT NULL DEFAULT 'chamfer'` and `bbox_ratio REAL` to the `classes` CREATE TABLE in `SCHEMA`
- [x] 1.2 `app/library.py:_migrate` — idempotent column-add guards (`has_col`) for both `match_strategy` (with default `'chamfer'`) and `bbox_ratio`
- [x] 1.3 `app/library.py` — `Store.load_library` now returns `(classes, configs, templates)` where `configs[class_name]` is `{"match_strategy": str, "bbox_ratio": float | None}`
- [x] 1.4 `app/library.py` — `Store.update_class_strategy(library_id, name, strategy, bbox_ratio)` writes both fields atomically; `app/main.py`'s PUT handler clears bbox_ratio to NULL when strategy flips to chamfer
- [x] 1.5 `app/library.py` — `Library.strategy_of(name) -> tuple[str, float | None]` and `Library.set_strategy(name, strategy, bbox_ratio)` (delegates to Store)
- [x] 1.6 `app/library.py` — `Library.summary()` returns `[{"name", "count", "match_strategy", "bbox_ratio"}]`

## 2. Matcher pipeline

- [x] 2.1 `app/matching.py` — `signatures_compatible(a, b, *, path_length_ratio=..., radius_ratio=...)` accepts per-call overrides on the two dimensional gates (σ-ratio stays global)
- [x] 2.2 `app/matching.py` — `find_matches(..., *, strategy="chamfer", bbox_ratio=None)` and `find_matches_from_pointsets(..., *, strategy="chamfer", bbox_ratio=None)`: when `strategy == "signature"` AND template is single-entity, run `_match_signature_mode` instead of the chamfer pipeline. Multi-entity templates log + fall back to chamfer.
- [x] 2.3 `app/matching.py` — `_match_signature_mode` iterates `drawing` once; matches get `score=0.0`, `scale=candidate.radius/template.radius` (guarded against zero); no near-misses

## 3. HTTP API

- [x] 3.1 `app/main.py` — `PUT /api/libraries/{library_id}/classes/{class_name}/strategy` body `{strategy, bbox_ratio?}`. Validation + defaults: signature without bbox_ratio → 0.05; chamfer → store NULL regardless of input; reject unknown strategy / bbox_ratio outside (0, 1]
- [x] 3.2 `app/main.py` — `GET /api/libraries/{library_id}/classes` returns `match_strategy` and `bbox_ratio` for every class
- [x] 3.3 `app/main.py` — `scan_all` and `save_match_json` resolve `lib.strategy_of(cls_name)` and pass `strategy=` / `bbox_ratio=` to `find_matches_from_pointsets`
- [x] 3.4 `app/main.py` — `MatchRequest` gains optional `class_name`; `match` endpoint resolves class strategy and threads through
- [x] 3.5 `app/jobs.py` — prematch worker reads `(strategy, bbox_ratio)` per class and threads to `find_matches_from_pointsets`

## 4. Viewer UI

- [x] 4.1 `app/static/canvas.js` — class buttons show a `sig·5%` (or `sig`) badge when `match_strategy == "signature"`; no badge for chamfer. Tooltip explains current mode and how to edit.
- [x] 4.2 `app/static/canvas.js` — right-click opens a two-step prompt (strategy, then bbox_ratio when signature); PUTs the `/strategy` endpoint
- [x] 4.3 `app/static/canvas.js` — `scanCurrentSelection` sends `class_name: addModeClass` when in add-mode so the live preview uses the same (strategy, bbox_ratio) as scan-all
- [x] 4.4 `app/static/style.css` — `.class-strategy-tag` styled tidily next to the class name

## 5. Tests

- [x] 5.1 `tests/test_library.py` — migration adds both columns to a pre-change DB, every existing row defaults to (chamfer, NULL)
- [x] 5.2 `tests/test_library.py` — round-trip: `set_strategy("Substrate", "signature", 0.05)`, reload, persists
- [x] 5.3 `tests/test_api.py` — `PUT .../strategy` validation: accept signature default-to-0.05, accept signature with explicit ratio, accept chamfer (clears ratio); reject unknown strategy / bbox_ratio ∉ (0, 1]
- [x] 5.4 `tests/test_api.py` — class listing surfaces both fields for every class
- [x] 5.5 `tests/test_matching.py` — signature mode matches a substrate-like template + candidate that chamfer mode parks in near-miss (`test_signature_mode_matches_where_chamfer_fails`)
- [x] 5.6 `tests/test_matching.py` — signature mode rejects a 15 %-larger candidate (no match, no near-miss)
- [x] 5.7 `tests/test_matching.py` — signature mode accepts rotation (parametrized: 30°, 45°, 137°, 270°) and mirror
- [x] 5.8 `tests/test_matching.py` — multi-entity template under `strategy="signature"` falls back to chamfer (behavior matches the no-strategy call)
- [x] 5.9 `uv run pytest -q` — 223 passed, 5 skipped (+20 new)

## 6. Spec sync

- [x] 6.1 `openspec validate add-per-class-match-strategy --strict` passes
- [ ] 6.2 At archive time, merge: (a) the new "Per-class match strategy and bbox-ratio override" requirement into `openspec/specs/template-library/spec.md`, (b) the modified "Transform-invariant matching" requirement (with new signature-mode scenarios) into `openspec/specs/pattern-matching/spec.md`

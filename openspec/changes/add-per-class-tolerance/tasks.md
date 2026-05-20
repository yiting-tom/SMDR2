## 1. Schema + persistence layer

- [x] 1.1 `app/library.py` — add `tolerance REAL` to the `classes` CREATE TABLE in `SCHEMA`
- [x] 1.2 `app/library.py:_migrate` — extend the existing `cls_cols` rebuild branch (or add a sibling guard) to ALTER TABLE `classes` ADD COLUMN `tolerance REAL` when missing
- [x] 1.3 `app/library.py` — `Store.upsert_class` and `Store.load_library` round-trip the tolerance value (NULL by default)
- [x] 1.4 `app/library.py` — `Library` gains `tolerance_of(name) -> float | None`, `set_tolerance(name, value: float | None)` (mirrors through to `Store.update_class_tolerance`)
- [x] 1.5 `app/library.py` — `Library.summary()` returns `[{"name", "count", "tolerance"}]`

## 2. HTTP API

- [x] 2.1 `app/main.py` — `PUT /api/libraries/{library_id}/classes/{class_name}/tolerance` body validator: `tolerance` field is `None | float`; reject negative / zero / >100 / non-finite / non-numeric with HTTP 400
- [x] 2.2 `app/main.py` — `GET /api/libraries/{library_id}/classes` response: each entry includes `tolerance` field
- [x] 2.3 `app/main.py` — `scan_all` and `save_match_json` resolve `lib.tolerance_of(cls_name)` and pass it as `tolerance=` to `find_matches_from_pointsets`; fall back to `TOLERANCE_ABS` when `None`
- [x] 2.4 `app/main.py` — `MatchRequest` gains optional `class_name: str | None = None`; the `match` endpoint resolves `class_tol_or_default` and passes it to `find_matches(..., tolerance=...)`
- [x] 2.5 `app/jobs.py` — prematch worker does the same per-class resolution before calling `find_matches_from_pointsets`, so the cached overlay honors per-class tolerance at preprocess time

## 3. Dashboard / viewer UI

- [~] 3.1 (revised: classes are only listed in the viewer toolbar, not the dashboard) Each class button in `canvas.js`'s class toolbar gains a tolerance badge `ε=<value>` when overridden, plus a tooltip explaining the current value and that right-click edits.
- [x] 3.2 Right-click a class button opens a `prompt()` for the new tolerance value; empty input clears (PUT `{tolerance: null}`), numeric input sets (PUT `{tolerance: number}`). Validation cap mirrors the server's (>0 and ≤100).
- [x] 3.3 Tooltip / prompt copy includes the default-value hint: "default 0.05 mm (BGA balls); typical override 0.5 mm for substrates".
- [x] 3.4 `canvas.js` — `scanCurrentSelection` sends `class_name: addModeClass` when in add-mode so the live preview uses the same tolerance scan-all will use.

## 4. Tests

- [x] 4.1 `tests/test_library.py` — migration test: pre-change DB without `tolerance` column → migration adds column, existing rows report `tolerance == None`
- [x] 4.2 `tests/test_library.py` — round-trip: `set_tolerance("Substrate", 0.5)`, reload, persists
- [x] 4.3 `tests/test_api.py` — `PUT .../tolerance` validation: reject `-0.1`, `0`, `200`, `"loose"`, accept `0.5`, accept `null`; 404 on unknown library / class
- [x] 4.4 `tests/test_matching.py` — direct matcher test: a candidate that chamfers ~0.19 mm against a 25 mm substrate stays in `near_misses` at `tolerance=0.05` but moves to `matches` at `tolerance=0.5`. The HTTP-level integration (scan-all honors class tolerance) is covered by the API tests + library round-trip + the matcher test together.
- [x] 4.5 `tests/test_api.py` — listing endpoint surfaces `tolerance` field for every class
- [x] 4.6 `uv run pytest -q` — 213 passed, 5 skipped

## 5. Spec sync

- [x] 5.1 `openspec validate add-per-class-tolerance --strict` passes
- [ ] 5.2 At archive time, merge: (a) the new "Per-class chamfer tolerance override" requirement into `openspec/specs/template-library/spec.md`, (b) the modified "Transform-invariant matching" requirement (with new scenarios) into `openspec/specs/pattern-matching/spec.md`

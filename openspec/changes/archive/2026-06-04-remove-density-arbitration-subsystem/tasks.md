## 1. Remove the call sites + relocate the one live helper

- [x] 1.1 Add `parse_match_key` to `app/side_regions.py` (relocated from `class_arbitration._parse_key`); it's the inverse of the `<prefix>.<base_key>` keys `split_matches_by_side` emits.
- [x] 1.2 `app/jobs.py` prematch worker: drop the `arbitrate(...)` call; collapse via `parse_match_key`.
- [x] 1.3 `app/jobs.py` save-match worker: drop the `arbitrate(...)` call + the `view_drops` side-count adjustment + the `arbitration_counts` return field.
- [x] 1.4 `app/main.py` scan-all: drop the `arbitrate(...)` call; collapse via `parse_match_key`; drop the `class_arbitration` import.

## 2. Remove the subsystem

- [x] 2.1 Delete `app/class_arbitration.py`.
- [x] 2.2 Remove from `app/library.py`: `ArbitrationGroup` / `MinNeighbors` / `MaxNeighbors` / `NeighborRule` / `CLASS_ARBITRATION_GROUPS` / `_build_arbitration_index` / `_ARBITRATION_INDEX` / `arbitration_group_for`.
- [x] 2.3 `app/static/canvas.js`: remove `CLASS_ARBITRATION_MEMBERS` + `isArbitrationMember()` + the commit-time full-rescan branch.

## 3. Tests + spec

- [x] 3.1 Delete `tests/test_class_arbitration.py`.
- [x] 3.2 Remove arbitration tests/imports from `test_library.py`.
- [x] 3.3 Remove `arbitration_counts` asserts from `test_match_json_constraints.py`.
- [x] 3.4 Remove the arbitration-members drift test + import from `test_canvas_constants.py` (the `CLASS_VIEW_CONSTRAINTS` drift test stays).
- [x] 3.5 Retire the `class-arbitration` capability spec (REMOVED delta).

## 4. Suite

- [x] 4.1 `pytest -q` — 508 passing; 1 unrelated pre-existing flake.
- [x] 4.2 `python -c "import app.main, app.jobs, app.library, app.side_regions"` — clean (no dangling imports).

## 5. Archive

- [x] 5.1 Run `/opsx:archive remove-density-arbitration-subsystem` after `disambiguate-bga-fiducial-by-view` is archived (this builds on it).

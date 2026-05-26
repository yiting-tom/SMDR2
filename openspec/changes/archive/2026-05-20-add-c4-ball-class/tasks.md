## 1. Core constant updates (`app/library.py`)

- [x] 1.1 Insert `"C4Ball"` into `DEFAULT_CLASSES` immediately before `"BGABall"` (between `"SMD-2T"` and `"BGABall"`)
- [x] 1.2 Add `"C4Ball": "c4_ball"` to `CLASS_JSON_KEY` (keep table ordered to mirror `DEFAULT_CLASSES`: place between `"SMD-2T"` and `"BGABall"`)
- [x] 1.3 Confirm no entry is needed in `LEGACY_CLASS_RENAME` (no historical class to rename)

## 2. Viewer color (`app/static/canvas.js`)

- [x] 2.1 Add `"C4Ball": "#ffab40"` (same orange as BGABall — user chose visual unification over per-class distinction) to `CLASS_COLORS`, placed directly above the `"BGABall"` line to mirror canonical order
- [x] 2.2 Update the color-family comment above `CLASS_COLORS` to note that C4Ball / BGABall share the same orange because both are ball-type interconnect

## 3. README

- [x] 3.1 Update §6 table line for `DEFAULT_CLASSES` to list 16 classes including `C4Ball` between `SMD-2T` and `BGABall`

## 4. Tests

- [x] 4.1 In `tests/test_library.py`, update any assertion that checks the size or exact contents of `DEFAULT_CLASSES` (e.g., `set(lib.classes) >= set(DEFAULT_CLASSES)` already covers it; add an explicit assertion that `C4Ball` is present and ordered immediately before `BGABall`)
- [x] 4.2 Add a test that `CLASS_JSON_KEY["C4Ball"] == "c4_ball"`
- [x] 4.3 Add a test that simulates a legacy library missing `C4Ball` and asserts that booting the Store seeds the row and re-ranks it immediately before `BGABall` (mirrors the existing `Re-rank places new defaults at their canonical position` scenario)
- [x] 4.4 In `tests/test_api.py`, extend the seeded-class assertion to include `C4Ball` (or add a separate assertion) without breaking existing checks

## 5. Verification

- [x] 5.1 Run `pytest tests/test_library.py tests/test_api.py -x` and confirm all pass — 41 passed
- [x] 5.2 Run the full test suite `pytest -x` and confirm no regressions — 237 passed; 3 pre-existing failures on the `bga-ball-render` branch (`test_dxf.py::test_circle_entity_emits_circle_primitive`, `test_matching.py::test_align_within_scale_tolerance`, `test_matching_circle_fast_path.py::test_legacy_template_does_not_use_fast_path`) are caused by WIP in `app/dxf.py` and `app/matching.py` — verified by stashing the C4Ball changes and rerunning; they pass on the bare branch, confirming no regression introduced by this change
- [ ] 5.3 Boot the app against an existing `data/library.sqlite`, open the viewer, and verify `C4Ball` appears in the toolbar between `SMD-2T` and `BGABall` with the new amber color — **deferred to user** (requires manual browser verification)
- [x] 5.4 Confirm `GET /api/libraries/default/classes` returns 16 entries in canonical order with `C4Ball` at position 10 (1-indexed) — verified via TestClient

## 6. OpenSpec finalization

- [x] 6.1 Run `openspec validate add-c4-ball-class --strict` and resolve any warnings — `Change 'add-c4-ball-class' is valid`
- [x] 6.2 After implementation merges and ships, archive the change with `/openspec-archive-change` — archived to `openspec/changes/archive/2026-05-20-add-c4-ball-class/` with main spec sync

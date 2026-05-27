## 1. Core constant updates (`app/library.py`)

- [x] 1.1 Insert `"FiducialSquare"` into `DEFAULT_CLASSES` immediately after `"FiducialCross"` (between `"FiducialCross"` and `"SMD-2T"`), bumping the canonical class count from 16 to 17
- [x] 1.2 Add `"FiducialSquare": "fiducial_square"` to `CLASS_JSON_KEY` (keep table ordered to mirror `DEFAULT_CLASSES`: place between `"FiducialCross"` and `"SMD-2T"`)
- [x] 1.3 Confirm no entry is needed in `LEGACY_CLASS_RENAME` (no historical class to rename) and no entry is needed in `CLASS_VIEW_CONSTRAINTS` (fiducials are view-unconstrained)

## 2. Viewer color (`app/static/canvas.js`)

- [x] 2.1 Add `"FiducialSquare": "#00acc1"` to `CLASS_COLORS`, placed directly below the `"FiducialCross"` line to mirror canonical order; comment `// even darker teal — sibling of FiducialCircle / FiducialCross`
- [x] 2.2 Confirm the color-family comment above `CLASS_COLORS` still reads sensibly (Teal = Fiducial family) — no rewrite required since the family taxonomy already accommodates a third teal sibling

## 3. README

- [x] 3.1 Update §6 table line for `DEFAULT_CLASSES` to list **17** classes and insert `FiducialSquare` between `FiducialCross` and `SMD-2T` in the parenthetical list

## 4. Tests

- [x] 4.1 In `tests/test_library.py`, add a test `test_fiducial_square_ordered_immediately_after_fiducial_cross` mirroring the existing `test_c4ball_ordered_immediately_before_bgaball`
- [x] 4.2 Add a test `test_fiducial_square_json_key_mapping` asserting `CLASS_JSON_KEY["FiducialSquare"] == "fiducial_square"`
- [x] 4.3 Add a test `test_legacy_library_gets_fiducial_square_seeded_and_ranked` mirroring `test_legacy_library_gets_c4ball_seeded_and_ranked` — seed every default except `FiducialSquare`, boot the Store, and assert the row appears immediately after `FiducialCross`
- [x] 4.4 In `tests/test_api.py`, extend the seeded-class assertion at line 26 to include `FiducialSquare` in the issubset check (or add a separate assertion) without breaking existing checks

## 5. Verification

- [x] 5.1 Run `pytest tests/test_library.py tests/test_api.py -x` and confirm all pass — 67 passed
- [x] 5.2 Run the full test suite `pytest -x` and confirm no regressions — 452 passed
- [ ] 5.3 Boot the app against an existing `data/library.sqlite`, open the viewer, and verify `FiducialSquare` appears in the toolbar between `FiducialCross` and `SMD-2T` with the new `#00acc1` color — **deferred to user** (requires manual browser verification)
- [x] 5.4 Confirm `GET /api/libraries/default/classes` returns 17 entries in canonical order with `FiducialSquare` at position 9 (1-indexed) — verified via TestClient

## 6. OpenSpec finalization

- [x] 6.1 Run `openspec validate add-fiducial-square-class --strict` and resolve any warnings — `Change 'add-fiducial-square-class' is valid`
- [ ] 6.2 After implementation merges and ships, archive the change with `/opsx:archive` (syncs the delta into the main `template-library` spec) — deferred to post-merge

## 1. Schema & data model

- [x] 1.1 Add startup migration that runs `ALTER TABLE files ADD COLUMN user_unit_override TEXT NULL` (idempotent — skip if column exists)
- [x] 1.2 Add `user_unit_override: Optional[Literal["mm","cm","m","inch","μm"]]` to the in-process file-row dataclass / TypedDict
- [x] 1.3 Define a single `UNIT_TO_SCALE: dict[str, float]` constant in `app/dxf.py` (`{"mm":1.0,"cm":10.0,"m":1000.0,"inch":25.4,"μm":0.001}`) and a reverse `SCALE_TO_UNIT` for picker pre-selection

## 2. Preprocess: override path

- [x] 2.1 In `app/dxf.py`, extend `flatten_for_render` (or `_maybe_rescale`) signature to accept `user_unit_override: Optional[str]`
- [x] 2.2 When override is set, derive `M` from `UNIT_TO_SCALE[user_unit_override]` and skip `detect_scale_factor` entirely
- [x] 2.3 When override is `NULL`, fall through to the existing detector path unchanged
- [x] 2.4 In `app/files.py`, plumb `user_unit_override` from the file row into the `flatten_for_render` call
- [x] 2.5 In `app/files.py`, after preprocess completes, compare the detector's would-be choice against the override and write `user_unit_override = NULL` when the multipliers match (clears redundant overrides)

## 3. API endpoint

- [x] 3.1 In `app/main.py`, add `POST /api/files/{file_id}/unit-override` that accepts `{"unit": str}`
- [x] 3.2 Validate `unit` against the five-string allowlist; return `400` for anything else (including missing/null field)
- [x] 3.3 On valid POST, check the existing-job table for an in-flight unit-override job for this `file_id`; return `409` with the live `job_id` if found
- [x] 3.4 Enqueue a preprocess job whose first step persists `user_unit_override` to the file row, then runs the standard preprocess pipeline
- [x] 3.5 Return `202 Accepted` with `{"job_id": <id>}`

## 4. Cache & invalidation

- [x] 4.1 Verify the existing "Auto-rescale invalidates saved Match JSON" trigger fires when override-driven preprocess changes `applied_scale` (it should — the trigger is `applied_scale differs from prior` — add a regression test if not covered)
- [x] 4.2 Confirm `data/prematch/{file_id}.json` is rebuilt on the override-driven preprocess (it already is — covered by existing requirement, just verify with a test)

## 5. Dashboard payload & pill

- [x] 5.1 In the per-file dashboard payload builder, add `user_unit_override` field (string or null)
- [x] 5.2 In `app/static/dashboard.js`, when rendering the `ℹ rescaled <human>` pill, append ` (user override)` to the text when `user_unit_override` is non-null
- [x] 5.3 Append `user_unit_override=<value>` to the pill's `title` attribute when set

## 6. Viewer picker UI

- [x] 6.1 Add the picker control markup to the viewer header, adjacent to `#library-switcher` — `<select id="unit-picker">` labelled `Unit:` with the five options in fixed order
- [x] 6.2 On viewer load, pre-select the option matching `applied_scale` via the `SCALE_TO_UNIT` map; for any unrecognised scale, select `mm` and render an `(actual ×<scale>)` trailing badge
- [x] 6.3 Render `set by you` badge to the right of the dropdown when `user_unit_override` is non-null in the file payload
- [x] 6.4 Render the inline soft hint `⚠ Differs from file declaration (<unit>)` whenever the selected option's multiplier disagrees with the source `INSUNITS` mapping
- [x] 6.5 Wire the dropdown's `change` event to open the confirm modal (do NOT fire the POST yet)

## 7. Confirm modal

- [x] 7.1 Add a confirm modal component listing the four enumerated points from the spec: preprocess re-runs, connectivity/pre-match rebuilt, Match JSON cleared for affected products, override is reversible (state the detector's choice)
- [x] 7.2 Modal SHALL display the count of affected products and the names of the first three (then "and N more" when applicable)
- [x] 7.3 Cancel button: revert dropdown selection, do not POST
- [x] 7.4 Confirm button: POST to `/api/files/{file_id}/unit-override`; on `202`, switch picker into job-in-flight state with returned `job_id`; on `409`, switch into the same state with the conflict's `job_id`

## 8. Job-in-flight state & polling

- [x] 8.1 While a recompute job is in flight, the picker SHALL be disabled and display the in-flight `job_id`
- [x] 8.2 Poll the existing job-status endpoint; on success, re-enable the picker and refresh the file payload so dropdown / badges reflect post-recompute state
- [x] 8.3 Cross-session recovery: on viewer reload while a job is in flight, the picker reads in-flight job state from the dashboard payload (same pattern as rule-check) and resumes the disabled state

## 9. Tests

- [x] 9.1 `tests/test_dxf.py` (or a new `tests/test_dxf_user_unit_override.py`): unit tests for `flatten_for_render` with each of the five override strings, asserting `applied_scale` and bbox math
- [x] 9.2 Test: override == `NULL` falls through to the detector and the existing detector scenarios continue to pass
- [x] 9.3 `tests/test_files.py`: integration test that preprocess with override `"inch"` on a unitless DXF persists `applied_scale == 25.4`
- [x] 9.4 `tests/test_files.py`: integration test that override matching detector's choice writes `NULL` back to the row
- [x] 9.5 `tests/test_files.py`: integration test that override-driven `applied_scale` change drops `data/match/{file_id}.json` and resets `match_saved`
- [x] 9.6 `tests/test_main.py` (or wherever endpoint tests live): `POST /api/files/{file_id}/unit-override` happy path returns `202` with `job_id`
- [x] 9.7 Endpoint test: POST with unknown unit returns `400`; file row unchanged
- [x] 9.8 Endpoint test: POST while an in-flight job exists for the same file returns `409` with the live `job_id`

## 10. Manual verification

- [ ] 10.1 Run the app, upload a known 1000×-too-big DXF that the detector currently handles, open viewer — picker pre-selects whatever the detector chose, no `set by you` badge
- [ ] 10.2 Override that file to `mm`, confirm modal lists the right product count → picker enters job-in-flight state → after completion, dropdown shows `mm`, `set by you` badge visible, dashboard pill suffixes `(user override)`
- [ ] 10.3 Override the file back to the detector's natural pick → after completion, dropdown reflects that pick, `set by you` badge gone, dashboard pill loses suffix
- [ ] 10.4 Upload a declared-inch DXF, override to `mm` → soft hint `⚠ Differs from file declaration (inch)` appears next to picker; modal still confirms; override applies

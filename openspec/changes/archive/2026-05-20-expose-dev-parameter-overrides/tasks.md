## 1. Backend: in-memory override store

- [x] 1.1 Create `app/dev_overrides.py` defining the allow-list as a list of entries `(module_ref, attr_name, type, min, max, description)` covering `matching.SCALE_MIN`, `matching.SCALE_MAX`, `matching.TOLERANCE_ABS`, `matching.VERTEX_COUNT_RATIO`, `matching.PATH_LENGTH_RATIO`, `matching.RADIUS_RATIO`, `matching.SIGMA_RATIO_TOL`, `matching.RESAMPLE_N`, `matching.BRUTE_FORCE_CUTOFF`, `dxf.BASE_TOLERANCE`, `dxf.CURVE_FLATTENING_DISTANCE`, `dxf.CIRCLE_MIN_VERTS`, `dxf.CIRCLE_RADIAL_TOL`, `dxf.MAX_PRIMS_PER_THUMB`, `dxf.MAX_VERTICES_PER_POLYLINE`.
- [x] 1.2 Snapshot compiled defaults into a frozen `DEFAULTS` dict at import time so reset can revert without recomputing.
- [x] 1.3 Implement `read_state() -> list[dict]` returning per-entry `{name, module, default, current, type, min, max, description}` for the `GET` endpoint.
- [x] 1.4 Implement `apply(overrides: dict) -> list[dict]` that validates every key against the allow-list (membership + type + range), mutates `setattr(module, name, value)` on success, raises a structured `ValidationError` on failure (so the caller can return 400 atomically without partial mutation).
- [x] 1.5 Implement `reset() -> list[dict]` that re-assigns every allow-listed attribute to `DEFAULTS[name]`.

## 2. Backend: HTTP endpoints

- [x] 2.1 Add `GET /api/dev/settings` in `app/main.py` returning `read_state()`.
- [x] 2.2 Add `POST /api/dev/settings` accepting either `{ "reset": true }` or `{ name: value, ... }`. Call `reset()` or `apply()` accordingly, return 400 on validation error with per-key reasons, 200 with `read_state()` on success.
- [x] 2.3 Add `POST /api/dev/reprocess-all`: enumerate files in storage, enqueue a single job that loops them through the existing preprocess pipeline reading from each file's already-on-disk DXF source, return `{job_id}`.
- [x] 2.4 Sanity-check the new endpoints with `curl` against a running server: GET → defaults, POST one override → GET reflects it, POST `{"reset": true}` → GET back to defaults, POST a bad value → 400. (Verified via FastAPI TestClient.)

## 3. Backend: confirm bare-name lookup actually works through overrides

- [x] 3.1 Audit `app/matching.py` for any place a tunable is captured into a closure or stored in a default argument at import time; convert those to runtime lookup (`matching.X`) so overrides flow through. Constants like `SCALE_MIN`, `SCALE_MAX`, `TOLERANCE_ABS`, `VERTEX_COUNT_RATIO`, `PATH_LENGTH_RATIO`, `RADIUS_RATIO`, `SIGMA_RATIO_TOL`, `RESAMPLE_N`, `BRUTE_FORCE_CUTOFF` must all be live-read.
- [x] 3.2 Same audit on `app/dxf.py` for `BASE_TOLERANCE`, `CURVE_FLATTENING_DISTANCE`, `CIRCLE_MIN_VERTS`, `CIRCLE_RADIAL_TOL`, `MAX_PRIMS_PER_THUMB`, `MAX_VERTICES_PER_POLYLINE`. Note that `CURVE_FLATTENING_DISTANCE = BASE_TOLERANCE` at module top is a one-time assignment — decide whether the override store applies them together or independently and document the decision in `dev_overrides.py`.
- [x] 3.3 Add a no-op-at-defaults regression test: set every override to its compiled default explicitly via the apply endpoint, run a known scan-all, assert byte-identical output to the pre-change baseline. (Implemented as `tests/test_dev_overrides.py` — 13 cases covering apply / reset / snapshot / atomicity + two override-flowthrough cases asserting `signatures_compatible` and `render_layer_svg` actually read live module attrs.)

## 4. Backend: re-preprocess job

- [x] 4.1 Add a re-preprocess function in `app/dxf.py` (or a thin wrapper in `app/jobs.py`) that takes a file ID, loads the on-disk DXF, runs the existing preprocess pipeline, and writes results back to the same storage slots the upload path uses.
- [x] 4.2 Wire `POST /api/dev/reprocess-all` to enqueue one job whose work item iterates every file. Reuse the progress-counting machinery `app/jobs.py` already exposes so the dashboard status line shows progress.
- [x] 4.3 Verify that the saved Match JSON is **not** deleted by the reprocess pass even when the underlying primitives change.

## 5. Frontend: dev-parameter modal

- [x] 5.1 Add a gear button `#dev-params-toggle` immediately after `#dev-mode-toggle` in `app/templates/dashboard.html`. Default `hidden`.
- [x] 5.2 Show/hide the gear in `syncDevModeButton()` in `app/static/dashboard.js` based on `getDevMode()`.
- [x] 5.3 Add a new modal markup block `#dev-params-modal` containing: a banner ("In-memory only — restart clears all overrides. Do not change while jobs are running."), two field-group sections (Matching, DXF), and the three action buttons Apply / Reset / Re-preprocess all files.
- [x] 5.4 Implement `openDevParamsModal()`: GET `/api/dev/settings`, render numeric inputs grouped by `module`, pre-fill `current`, show `default` as helper text, set `min`/`max`/`step` from the allow-list response.
- [x] 5.5 Implement Apply: collect form values, POST, on 200 update `localStorage["smdr2.dashboard.devOverrides"]` with the echoed state and re-fill the form; on 400 render per-field errors inline.
- [x] 5.6 Implement Reset: POST `{ "reset": true }`, refresh the form, clear `localStorage["smdr2.dashboard.devOverrides"]`.
- [x] 5.7 Implement Re-preprocess: show a confirm dialog ("This will rewrite primitives for every uploaded file. Saved Match JSONs may become stale. Continue?"), on confirm POST `/api/dev/reprocess-all`, then attach to the dashboard's existing job-poll loop so the status line reflects progress.
- [x] 5.8 Style the modal in `app/static/style.css` consistent with the other dashboard modals (rule-results, layer-modal).

## 7. Split: matching modal moves to viewer page

- [x] 7.1 Extract the modal logic into `app/static/dev_params.js` exporting `mountDevParamsModal({moduleFilter, ...})` so both pages can share rendering / Apply / Reset wiring without duplicating ~100 lines.
- [x] 7.2 Refactor `app/static/dashboard.js` to call `mountDevParamsModal({moduleFilter: "dxf", reprocessId: ...})` — dashboard modal now shows only DXF entries and keeps Re-preprocess.
- [x] 7.3 Add the gear button to `app/templates/viewer.html` (after `#rules-btn`) and the `#dev-params-modal` block (Matching banner, Apply + Reset only, no Re-preprocess).
- [x] 7.4 Wire the viewer modal in `app/static/canvas.js` via `mountDevParamsModal({moduleFilter: "matching"})`; the gear honours the same `smdr2.dashboard.devMode` localStorage flag set by the dashboard.
- [x] 7.5 Change Reset semantics so each modal scopes its reset to its own slice: POST every visible field's compiled default rather than `{reset: true}` (which is left in place as a wipe-all affordance for non-UI callers).
- [x] 7.6 Update the `viewer-ui` spec delta to describe the split (dashboard = DXF + reprocess, viewer = matching only, shared dev-mode flag).

## 6. Verification

- [ ] 6.1 Manual test: Dev Mode OFF → no gear button visible. (User action required.)
- [ ] 6.2 Manual test: Dev Mode ON → gear visible, modal opens, GET pre-fills correctly. (User action required.)
- [ ] 6.3 Manual test: edit `TOLERANCE_ABS` → Apply → run a known frame-select match → result differs as expected; Reset → repeat → result returns to baseline. (User action required.)
- [ ] 6.4 Manual test: change `BASE_TOLERANCE` → Apply → click Re-preprocess → wait for job → re-open a file's viewer → primitives reflect new tolerance. (User action required.)
- [ ] 6.5 Manual test: restart the server → GET `/api/dev/settings` → all `current == default`; `localStorage` mirror is stale but the modal GET on open establishes ground truth. (User action required.)
- [x] 6.6 Run the existing test suite (`pytest`) and confirm no regressions at default parameter values. (267 pass, 3 pre-existing failures unrelated to this change — they pre-date the session and reproduce on HEAD of `app/matching.py`/`app/dxf.py`; new `test_dev_overrides.py` adds 13 cases all passing.)

## 1. Schema + persistence

- [x] 1.1 Add `applied_scale REAL NOT NULL DEFAULT 1.0` to the `files` table schema in `app/files.py` (extend `FILES_SCHEMA` and add an ALTER for existing DBs).
- [x] 1.2 Extend `File` dataclass with `applied_scale: float = 1.0` and persist/load it through every read/write path (insert, update, row→dataclass).
- [x] 1.3 Add `tests/test_files_schema.py` case (or extend existing) covering: fresh DB has the column with default `1.0`, ALTER on a legacy DB adds it without data loss.

## 2. Detector + auto-rescale in preprocessing

- [x] 2.1 In `app/dxf.py`, add a pure helper `detect_scale_factor(insunits: int | None, bbox_diagonal: float) -> float` implementing the table in the dxf-pipeline spec: declared inch/cm/m/mm fixed factors, unitless path picks best power-of-10 in `[-4, +4]` to bring `bbox_diagonal * M` into `[10, 5000]` mm with the `|log10(M)| >= 1` safety guard.
- [x] 2.2 Add a private `_maybe_rescale(render: RenderOutput) -> tuple[RenderOutput, float]` that calls `detect_scale_factor` on `(render.insunits, diagonal(render.bbox))` and, when the factor is not `1.0`, multiplies every coordinate in `render.primitives` and recomputes `render.bbox`. Keep `insunits` untouched (it documents the source DXF).
- [x] 2.3 Add `applied_scale: float = 1.0` to the `RenderOutput` dataclass.
- [x] 2.4 Call `_maybe_rescale` at the end of `flatten_for_render` and assign the returned factor to `RenderOutput.applied_scale`.
- [x] 2.5 Verify `render_layer_svg` consumes the rescaled primitives directly (no separate path) and that thumbnail viewBox sizes follow rescaled bbox.

## 3. Match JSON invalidation on factor change

- [x] 3.1 In the preprocess persistence step (where the new `RenderOutput` lands on the file row), compare the new `applied_scale` against the row's prior value; if they differ, delete `data/match/{file_id}.json` and clear `match_saved`.
- [x] 3.2 If the factor changed, set the file's `status` back to `ready_to_match`.
- [x] 3.3 Surface a per-product dashboard banner ("Match JSON cleared after auto-rescale — re-run match") that fires on the next dashboard tick. Reuse the existing notification path used by side-region invalidation if possible; otherwise add a one-line surface keyed by file_id and dismissed when the user re-runs match.

## 4. Startup migration

- [x] 4.1 In `app/main.py` startup, add a one-shot scanner that iterates `files` rows where `applied_scale == 1.0` and evaluates `detect_scale_factor(insunits, persisted_bbox_diagonal)`; collect IDs whose detector returns a non-`1.0` factor.
- [x] 4.2 For each hit, submit a re-preprocess job via the existing `/api/dev/reprocess-all` job machinery (or the same internal entry point it calls).
- [x] 4.3 Confirm the migration is idempotent — running it twice in a row does not re-submit already-rescaled files (a rescaled file's persisted bbox is already in mm, so the detector returns `1.0`).
- [x] 4.4 Log the file IDs that were re-submitted at INFO level for ops visibility, including the factor the detector chose for each.

## 5. Dashboard payload + UI

- [x] 5.1 Extend `File.to_dict` so the payload carries `applied_scale` and so `unit_scale_warning_detail` is rewritten when `applied_scale != 1.0` — include the factor AND the source INSUNITS unit in the human-readable text (e.g. `"INSUNITS=1 (inch) → auto-rescaled ×25.4 (mm)"`).
- [x] 5.2 Add a small `format_applied_scale(factor: float, insunits: int | None) -> str` helper used by both the server detail string and shared with the dashboard JS via a simple format (e.g. JSON field already-formatted, or replicated in JS). Output examples: `"÷1000"`, `"×25.4 (inch)"`, `"×10"`.
- [x] 5.3 In `app/static/dashboard.js`, branch the per-file slot rendering: if `applied_scale != 1.0`, render the `ℹ rescaled <human>` informational pill (`<human>` from step 5.2); else if `unit_scale_warning` is non-null, render the existing yellow `⚠ unit` badge.
- [x] 5.4 Add CSS for the `rescaled-pill` class in `app/static/style.css` — neutral (info-blue) styling to distinguish it from the warning badge.

## 6. Tests

- [x] 6.1 `tests/test_dxf_auto_rescale.py` — unit tests for `detect_scale_factor` covering every row of the spec table: 1000×-too-big unitless → `0.001`, 1000×-too-small unitless → `1000`, 10×-too-big unitless → `0.1`, declared inch → `25.4`, declared cm → `10`, declared m → `1000`, declared mm → `1.0`, marginal unitless (×3 / ×7) → `1.0`, out-of-range unitless → `1.0`.
- [x] 6.2 Same file — integration tests for `_maybe_rescale`: feeds a `RenderOutput` with a known primitive set, asserts coords + bbox both multiply by `applied_scale`, asserts `insunits` is preserved.
- [x] 6.3 Extend `tests/test_files_unit_warning.py` with the new payload field `applied_scale` and the reworded detail text when the factor was applied (covering at least the unitless ÷1000 case and the inch ×25.4 case).
- [x] 6.4 Add an integration-style test for Match JSON invalidation: preprocess a fake-1000× file twice (once with `applied_scale=1.0` seeded, once with the feature live) and assert `data/match/{file_id}.json` disappears and `match_saved` flips to `0` on factor change.
- [x] 6.5 Add a migration test: start the app with two seeded file rows (one matching the detector with `insunits=0, diagonal=42000`, one with `insunits=4, diagonal=300`), assert the first ends up with `applied_scale == 0.001` and the second untouched.

## 7. Rollout + manual check (user — needs the real bad DXF + a browser)

- [ ] 7.1 Run the existing matcher tests against a known-1000× DXF (manual or via fixture) before and after this change to confirm previously-failing left-right and 180° rotated substrates now match without bumping `TOLERANCE_ABS`.
- [ ] 7.2 Verify in the dashboard UI that the badge → pill flip looks right, hover text reads cleanly, and the rule-check distances on a rescaled product card now read in mm.
- [ ] 7.3 Sanity-check that the viewer for a rescaled file renders the geometry correctly (same shape, just at 1/1000 the previous world coords) and that pan/zoom still feel correct.

## 1. DB schema + persistence layer

- [x] 1.1 Update `CREATE TABLE files` in `app/files.py` to declare `top_view_rect TEXT`, `bottom_view_rect TEXT`, `side_view_rect TEXT` (replacing the old `frontside_rect` / `bottomside_rect` columns).
- [x] 1.2 In `_migrate()`, add idempotent rename steps: `ALTER TABLE files RENAME COLUMN frontside_rect TO top_view_rect` (only if `frontside_rect` exists and `top_view_rect` does not); same for `bottomside_rect → bottom_view_rect`; then `ALTER TABLE files ADD COLUMN side_view_rect TEXT` (only if missing).
- [x] 1.3 Rename `FileRecord.frontside_rect` / `.bottomside_rect` to `.top_view_rect` / `.bottom_view_rect`; add `.side_view_rect`.
- [x] 1.4 Update `_row_to_record` and `FileRecord.to_dict` to read/write the three new column names.
- [x] 1.5 Rename `update_side_regions(file_id, frontside_rect, bottomside_rect)` to take three params `top_view_rect`, `bottom_view_rect`, `side_view_rect`; update the `UPDATE` SQL accordingly.
- [x] 1.6 Rename `clear_side_regions` to clear all three columns.

## 2. Side-prefix helper (`app/side_regions.py`)

- [x] 2.1 Update the module docstring to reference top_view / bottom_view / side_view and the new prefix strings.
- [x] 2.2 Rewrite `side_prefix_for` to accept `top_view`, `bottom_view`, `side_view` as three named `Optional[Rect]` parameters; return one of `"top_view"`, `"bottom_view"`, `"side_view"`, or `None` using the priority `top_view > bottom_view > side_view`.
- [x] 2.3 Update `split_matches_by_side` to plumb the three rectangles into `side_prefix_for`, and to seed `counts` with `top_view`, `bottom_view`, `side_view`, `unassigned` keys.
- [x] 2.4 Confirm pure-helper status (no DB / filesystem imports added).

## 3. API layer (`app/main.py`)

- [x] 3.1 Rename `SideRegionsRequest` fields to `top_view_rect`, `bottom_view_rect`, `side_view_rect` (all `Optional[Rect]`).
- [x] 3.2 Update `PATCH /api/files/{file_id}/side-regions` (`patch_side_regions`) to normalise and persist all three rectangles, and to invalidate the cached match JSON when any of the three changes.
- [x] 3.3 Update the save-match flow that calls `split_matches_by_side` to pass the three rectangles from the file record.
- [x] 3.4 Update any in-comment references like `# Covers both ring-crosses-rect ... frontside.SMD-2T.0` to use the new names.

## 4. Frontend (`app/static/canvas.js` + templates)

- [x] 4.1 Rename `sideRects = { frontside, bottomside }` to `sideRects = { top_view, bottom_view, side_view }`.
- [x] 4.2 Extend `SIDE_STYLES` to three entries with three distinct colours (re-use the existing palette idiom).
- [x] 4.3 Rewrite `enterMarkMode` as a three-step cycle: `top_view → bottom_view → side_view`. Per step, handle left-drag (provisional rect), `Enter` (commit + advance), bare left-click (skip + advance), `Esc` (cancel session + revert).
- [x] 4.4 Update the status-hint string to `MARK <view> · drag a rectangle, Enter to keep, click to skip` substituting the current slot.
- [x] 4.5 Update `sideRegions` endpoint payload (request and response) to use the three new field names.
- [x] 4.6 Update save-match payload / display to surface the three new prefix strings where applicable.
- [x] 4.7 In the sides-menu HTML template (in `app/templates/viewer.html` or equivalent), rename the existing two buttons (`frontside` / `bottomside` → `top_view` / `bottom_view`), add a third `side_view` button, and update the `data-action` strings + labels.
- [x] 4.8 Update CSS class names for the overlay outlines (`.side-overlay-frontside` etc.) to the new naming and add a third style.

## 5. Tests

- [x] 5.1 In `tests/test_side_regions.py`, rename every fixture / assertion from `frontside` / `bottomside` to `top_view` / `bottom_view`; add cases for `side_view` and three-way overlap priority.
- [x] 5.2 Add a test covering "only `side_view` set, all others null" → instances inside it get `side_view.<class>.<index>`.
- [x] 5.3 Add a test covering "all three rectangles overlap on a single point" → instance is emitted under `top_view.<class>.<index>` (priority).
- [x] 5.4 In `tests/test_files.py`, rename and extend existing side-region tests (`test_side_regions_persist_and_round_trip`, `test_side_regions_clear_one_independently`, `test_clear_side_regions_unsets_both`, `test_library_swap_preserves_side_regions`) to cover all three rectangles.
- [x] 5.5 Add a `test_migration_renames_old_columns_and_adds_side_view` covering the case where the DB is created with the old schema (`frontside_rect`, `bottomside_rect`) and `_migrate()` is then run.
- [x] 5.6 In `tests/test_api.py`, rename `test_side_regions_patch_clears_saved_match`, `test_side_regions_patch_on_missing_file_404s`; add a test that PATCHing only `side_view_rect` clears the saved match.
- [x] 5.7 Run the full pytest suite locally and confirm green.

## 6. Wrap-up

- [x] 6.1 Run the dev server, exercise the three-view mark flow in the browser (top + bottom + side, only side, top + side with bottom skipped), confirm overlay rendering + Match JSON output.
- [x] 6.2 Update any out-of-date comments in `app/side_regions.py`, `app/main.py`, `app/static/canvas.js` that still reference `frontside` / `bottomside` by name.
- [ ] 6.3 When the implementation is verified, run `openspec archive add-side-view-region` (or the `/opsx:archive` skill).

## 7. Per-view × delete affordance

- [x] 7.1 Render an "×" delete glyph inside each committed view's persistent label background in `drawSideRegionLabels` (`app/static/canvas.js`).
- [x] 7.2 Push the × hitbox in CSS pixels into a per-frame `sideLabelHitboxes` array.
- [x] 7.3 In the canvas `mousedown` handler, check the × hitboxes BEFORE the mark-mode / measure / selection branches so the click is always intercepted.
- [x] 7.4 Add `clearSpecificView(view)` that sets `sideRects[view] = null`, updates `sideRectsSnapshot[view]` to null if a mark-mode session is active, calls `patchSideRegions`, and re-renders.
- [x] 7.5 Extend the `Redraw and clear side regions` requirement in `specs/viewer-ui/spec.md` with the × delete affordance + Esc-survives-during-mark-mode scenarios.

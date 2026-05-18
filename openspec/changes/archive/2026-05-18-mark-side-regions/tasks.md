## 1. Backend — schema + storage

- [x] 1.1 Add `frontside_rect TEXT` and `bottomside_rect TEXT` columns to the `files` table schema in `app/files.py` (FILES_SCHEMA) and the migration block (mirror the `selected_layers` ALTER TABLE pattern).
- [x] 1.2 Extend `FileRecord` with `frontside_rect: dict | None` and `bottomside_rect: dict | None` fields and serialize them in `to_dict()`.
- [x] 1.3 Add `FileStore.update_side_regions(file_id, frontside_rect, bottomside_rect)` and `FileStore.clear_side_regions(file_id)` methods, JSON-encoding rectangles before write.
- [x] 1.4 Update `_row_to_record` to decode both columns into dicts, tolerating malformed JSON by treating as None.

## 2. Backend — side-region helper module

- [x] 2.1 Create `app/side_regions.py` with `Rect = TypedDict("Rect", {"x0": float, "y0": float, "x1": float, "y1": float})` and `normalise_rect(rect) -> Rect` that orders coords so `x0<=x1`, `y0<=y1`.
- [x] 2.2 Add `point_in_rect(p, rect) -> bool` (closed-interval containment).
- [x] 2.3 Add `side_prefix_for(handles, shapes, frontside, bottomside) -> str | None` that computes the combined bbox of the given handles via `shapes[h].points`, takes its center, returns `"frontside"`, `"bottomside"`, or `None` per the containment rules (frontside wins on overlap; None when both rects are unset or center is outside both).
- [x] 2.4 Write unit tests in `tests/test_side_regions.py` covering: center inside frontside, inside bottomside, in overlap, in neither, missing rects, normalisation, and a handles list whose shapes form a non-trivial multi-entity bbox.

## 3. Backend — match JSON serializer

- [x] 3.1 In `app/main.py:save_match_json`, after computing the raw matches per template, call `side_prefix_for` for each instance and emit it under `{prefix}.{class}.{index}` when a prefix is returned, else `{class}.{index}`. Group instances correctly so the same template can produce both `frontside.smd.0` and `bottomside.smd.0` keys.
- [x] 3.2 Include `{"frontside": N, "bottomside": M, "unassigned": K}` counts in the API response so the UI can show a quick check.
- [x] 3.3 ~~Update the preprocess prematch writer~~ — **dropped**: prematch JSON is `{by_class: {<class>: [handle...]}}` (flat handle list per class for the viewer's color overlay), not per-instance, so side-prefixing doesn't fit its schema. See updated design.md Decision 3.
- [x] 3.4 Leave `POST /api/files/{file_id}/match` (the in-flight per-class match endpoint) untouched — the per-class colored overlay does not need side prefixes.

## 4. Backend — side-regions endpoint + cache invalidation

- [x] 4.1 Add a Pydantic model `SideRegionsRequest`. Body always carries both rectangles; null = clear that side. Simpler than "absent vs null" because the frontend keeps full local state.
- [x] 4.2 Add `PATCH /api/files/{file_id}/side-regions` in `app/main.py`. Resolve file, normalise rectangles, persist via `FILE_STORE.update_side_regions`.
- [x] 4.3 In the same handler, on any change: delete `data/match/{file_id}.json` if present and call `FILE_STORE.set_match_saved(file_id, False)`. Prematch is not regenerated (see updated design Decision 3 — prematch has no side labels).
- [x] 4.4 Include both rectangles in the existing `GET /api/files/{file_id}` response (already wired via `FileRecord.to_dict()`).

## 5. Frontend — viewer state machine

- [x] 5.1 In `app/static/canvas.js`, add `markMode` state (`null | "frontside" | "bottomside"`) and `sideRects = { frontside: null, bottomside: null }`. Fetch initial values from `GET /api/files/{file_id}` on viewer load.
- [x] 5.2 Add `enterMarkMode()` / `exitMarkMode()` helpers; ensure they're no-ops while `addModeClass` or `measureMode` is active.
- [x] 5.3 Bind the `R` hotkey to `toggleMarkMode` in the global keydown handler (after the `D` hotkey block); make it a no-op when other modes are active, mirroring the measure-mode guard.
- [x] 5.4 Extend the Esc cascade between the "cancel measurement" and "clear scan-all" branches to: cancel in-progress side rectangle drag → exit mark mode if no drag in progress.

## 6. Frontend — rectangle capture

- [x] 6.1 In mark mode, intercept left mousedown/mousemove/mouseup on the canvas before the existing box-drag logic. Treat the gesture as a one-shot rectangle capture; render it live using a dedicated `SIDE_STYLES` colour per side.
- [x] 6.2 On mouseup, normalise to `{x0,y0,x1,y1}` (world coordinates), store on the side currently being captured, then advance the `markQueue` (or exit when empty).
- [x] 6.3 PATCH `/api/files/{id}/side-regions` with the full state after every captured rectangle (so partial/redraw flows save immediately). Failure surfaces via `setBaseStatus`; local state already updated so the user can retry.
- [x] 6.4 Tiny rectangle (area < `MARK_MIN_AREA`) is dropped; mark mode stays on the same side awaiting another drag.

## 7. Frontend — persistent overlay + toolbar

- [x] 7.1 Add a `drawSideRegionsOverlay(hairline)` step in the render pipeline that strokes `sideRects.frontside` (green tint) and `sideRects.bottomside` (orange tint) at low opacity, plus a dashed in-progress drag rect. Called right after the geometry pass.
- [x] 7.2 Add a "Sides" toolbar button in `app/templates/viewer.html` next to the Measure button. Style its popup menu in `app/static/style.css`.
- [x] 7.3 Right-click on the button opens a popup menu with `Redraw both` / `Redraw frontside only` / `Redraw bottomside only` / `Clear both`.
- [x] 7.4 Status hint reads `MARK frontside · drag a rectangle (Esc to cancel)` etc. while mark mode is active.

## 8. Testing

- [x] 8.1 Unit-test `split_matches_by_side` directly (more reliable than going through the full /match-json plumbing): assert keys split as `frontside.smd.0` / `bottomside.smd.0` for instances on the appropriate side, and instance order is preserved within each side. See `tests/test_side_regions.py`.
- [x] 8.2 API test (`tests/test_api.py::test_side_regions_patch_clears_saved_match`): PATCH side-regions deletes `data/match/{file_id}.json` and clears `match_saved`.
- [x] 8.3 Unit test in `tests/test_side_regions.py::test_split_matches_keeps_unassigned_unprefixed` — with both rectangles null, keys are unprefixed (regression guard).
- [x] 8.4 `tests/test_files.py::test_library_swap_preserves_side_regions` — library swap leaves frontside/bottomside rectangles intact.
- [ ] 8.5 Frontend smoke test (manual, post-merge): paint a frontside box covering the left half and a bottomside box covering the right half of the bundled test DXF, Save Match, open `data/match/{file_id}.json`, verify both prefixed keys appear.
- [ ] 8.6 Rule-check end-to-end check on a file with prefixed keys (manual, post-merge).

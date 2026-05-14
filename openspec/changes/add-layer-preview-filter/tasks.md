## 1. Storage and schema

- [x] 1.1 Add `LAYER_PREVIEW_DIR = DATA_DIR / "layer_preview"` to `app/storage.py` and append it to the startup `mkdir` loop; add `layer_preview_dir(file_id)`, `layer_preview_svg_path(file_id, safe_name)`, and `layer_manifest_path(file_id)` helpers.
- [x] 1.2 Add a `selected_layers TEXT` column to `files` in `app/files.py` (`FILES_SCHEMA` + the `PRAGMA table_info` migration pattern). Default NULL.
- [x] 1.3 Extend `FileRecord` with `selected_layers: list[str] | None` and round-trip it through `to_dict` / row construction; helpers `update_selected_layers(file_id, layers)` and `clear_selected_layers(file_id)` on `FileStore`.
- [x] 1.4 Add `DISCOVERING_LAYERS = "discovering_layers"` and `AWAITING_LAYERS = "awaiting_layers"` to the lifecycle constants in `app/files.py`; extend `ALL_STATUSES`.

## 2. Layer discovery worker (Phase 1)

- [x] 2.1 In `app/dxf.py`, factor out a `flatten_for_render` variant (or post-process step) that groups primitives by `layer` and returns `{ layers: {name: [prim_idx, ...]}, bbox, background, primitives }`. *(Done as `group_primitives_by_layer(prims) -> {name: [idx, ...]}`; the bbox/background/primitives stay on `flatten_for_render`'s `RenderOutput`.)*
- [x] 2.2 Add `render_layer_svg(primitives, layer_indices, bbox) -> str` in `app/dxf.py` that emits a compact SVG: shared `viewBox`, primitives stroked in their own color (decoratives skipped, `filled_polygon` rendered with 50% alpha fill), stroke width tuned for the file bbox. *(Decimates dense layers — `MAX_PRIMS_PER_THUMB=600`, `MAX_VERTICES_PER_POLYLINE=24` — to keep thumbnails small.)*
- [x] 2.3 Add `sanitize_layer_name(name) -> str` helper that URL-encodes filesystem-unsafe characters; expose both the sanitized filename and the original name in the manifest.
- [x] 2.4 Add `_discover_layers_worker(file_id, src)` in `app/jobs.py` that: parses the DXF once, writes per-layer SVGs into `data/layer_preview/{file_id}/`, writes a `layers.json` manifest (`{layers: [{name, safe_name, svg_filename, entity_count}], bbox, background}`), and writes the full primitive set to a transient `data/layer_preview/{file_id}/primitives.json` for Phase 2 reuse.
- [x] 2.5 Add `submit_discover_layers(file_id)` mirroring the existing `submit_preprocess`, including a `phase: "discover"` field in the in-memory job dict.

## 3. Wire Phase 1 into upload paths

- [x] 3.1 In `app/main.py`'s `_ensure_test_dxf_registered`, replace the `submit_preprocess` call with `submit_discover_layers` for any file lacking a layer manifest.
- [x] 3.2 In `POST /api/products/{product_id}/files` and `POST /api/files`, after registering, submit a layer-discovery job instead of going straight to full preprocess; new files SHALL land in `discovering_layers`. *(`POST /api/files` doesn't actually exist — only the product-scoped upload — so only the product route was touched.)*
- [x] 3.3 On `submit_discover_layers` completion, transition the file's status to `awaiting_layers` (unless an exception was raised → `error`). *(Wired in `_on_discover_done` in `app/jobs.py`.)*

## 4. Layer-confirm endpoint and Phase 2 filter

- [x] 4.1 Add `POST /api/files/{file_id}/layers` in `app/main.py` accepting `{layers: [name, ...]}`; validate each name against the file's manifest, persist via `update_selected_layers`, flip status to `preprocessing`, and submit Phase 2.
- [x] 4.2 Add `GET /api/files/{file_id}/layers` returning `{manifest, selected_layers}` from the on-disk `layers.json` plus the DB column.
- [x] 4.3 Add `GET /api/files/{file_id}/layer-preview/{safe_name}.svg` serving the SVG from `data/layer_preview/{file_id}/`, with a 404 when the file isn't ready or the layer name isn't in the manifest. *(Also added `POST /api/files/{file_id}/discover-layers` to let the viewer kick off Phase 1 on a legacy file — needed for task 7.4.)*
- [x] 4.4 In `app/dxf.py`, add `filter_primitives(primitives, layer_set) -> list[dict]` that drops primitives whose `layer` is not in `layer_set`; decoratives filtered alongside on the same rule.
- [x] 4.5 Update `_preprocess_worker` in `app/jobs.py` to: read the transient `primitives.json` if present (skip re-parsing), apply `filter_primitives` against the file's `selected_layers`, then continue with the existing handle-index / shape-index / scan-all flow; delete the transient `primitives.json` on success.
- [x] 4.6 Make `_preprocess_worker` write `selected_layers` into the top-level of `parsed/{file_id}.json` alongside `primitives` / `bbox` / `background`.

## 5. Library / role swap reuses selection

- [x] 5.1 In `PATCH /api/files/{file_id}` (library swap), keep the existing flow but pass the file's `selected_layers` through to `submit_preprocess`; do NOT re-run discovery.
- [x] 5.2 In `POST /api/products/{product_id}/files` when overwriting an existing slot, clear `selected_layers` and re-run discovery (since the bytes are new); existing tests for slot reuse must still pass.

## 6. Dashboard modal UI

- [x] 6.1 Add a `#layer-modal` markup block to `app/templates/dashboard.html` with the structure described in design D6 (header / grid body / footer with confirm + select-all + select-none).
- [x] 6.2 In `app/static/dashboard.js`, add modal open/close helpers, layer-card render, checkbox state tracking, and the confirm POST flow. *(Extracted as a reusable ES module at `app/static/layer_modal.js` so the viewer can import the same flow.)*
- [x] 6.3 Extend the existing product polling logic to detect `awaiting_layers` per file and auto-open the modal once per status transition. *(Dedupe via `handledAwaitingLayers` set so polling doesn't re-pop after the user cancels.)*
- [x] 6.4 Show distinct row indicators for `discovering_layers` ("scanning layers") and `awaiting_layers` ("Action needed" badge) in the product card rendering. *(Uses an inline "Pick layers" button + amber status color instead of a generic badge — clearer call to action.)*
- [x] 6.5 Add a per-file "Layers" button on the row that opens the same modal pre-checked against `selected_layers` (when available) for any post-Phase-1 file.
- [x] 6.6 Style the modal grid (~160 × 120 px thumbnails, monospace layer labels) in `app/static/style.css`, reusing existing modal patterns.

## 7. Viewer "Edit layers" button

- [x] 7.1 Add a `#layers-btn` to `app/templates/viewer.html` next to the library/measure buttons in the header.
- [x] 7.2 In `app/static/canvas.js`, wire the button to fetch `GET /api/files/{file_id}/layers`, open the modal (reuse the dashboard partial via a small shared helper module if it stays small, else duplicate the renderer in `canvas.js`). *(Reused `layer_modal.js` — same module the dashboard imports.)*
- [x] 7.3 On confirm, post the new selection, poll `/api/files/{file_id}` until `status === "ready_to_match"`, then `location.reload()` to re-fetch primitives.
- [x] 7.4 When the file has no manifest (legacy), the button SHALL request layer discovery first and show a "scanning layers…" placeholder. *(`triggerDiscovery: !hasManifest` flag in the `openLayerModal` call hits `POST /api/files/{file_id}/discover-layers` and polls for the manifest before rendering.)*
- [x] 7.5 Live layer-visibility toggle in the viewer (added after the proposal landed, per user follow-up): new "👁 Visibility" header button + floating side panel in `viewer.html`; `hiddenLayers: Set<string>` in `canvas.js` (persisted in `sessionStorage` per file_id); hidden layers are skipped in `render()`, `pickIndexAt`, `selectByBox`, `buildConnectivity`, and snap (via a new `isHidden` predicate on `measure_core.resolveSnap`). Independent of `selected_layers` — purely visual, no backend round-trip.

## 8. Tests

- [x] 8.1 Unit test `filter_primitives` (in / out / decorative behavior, empty filter rejected upstream).
- [x] 8.2 Unit test `sanitize_layer_name` round-trip across spaces, slashes, dots, unicode.
- [x] 8.3 Integration test: upload a small fixture DXF with 2 layers, assert status flows `discovering_layers → awaiting_layers`, manifest contains 2 entries, both SVG files exist.
- [x] 8.4 Integration test: confirm 1 of 2 layers, assert `parsed/{file_id}.json` has zero primitives from the excluded layer, `prematch/{file_id}.json` references no excluded handles, top-level `selected_layers` matches.
- [x] 8.5 Integration test: library swap on a file with `selected_layers=["BD"]` preserves the filter and does NOT re-enter `awaiting_layers`.
- [x] 8.6 Integration test: legacy file (selected_layers=NULL, no manifest) stays in `ready_to_match` after deploy; "Edit layers" forces discovery and the modal eventually opens with all layers checked.
- [ ] 8.7 Frontend smoke test in browser: upload, modal pops, deselect one layer, confirm, viewer reloads, the deselected layer's geometry is absent from the canvas. *(Manual browser test — outside pytest's scope; flagged for verification before merge.)*

## 9. Migration and rollout

- [x] 9.1 Verify the `ALTER TABLE` migration in `app/files.py` runs cleanly against the existing `data/library.sqlite` (back up before testing). *(Verified manually: `PRAGMA table_info(files)` shows `selected_layers` is present in the production DB; existing rows are untouched.)*
- [x] 9.2 Create `data/layer_preview/` on app startup (`app/storage.py`).
- [x] 9.3 Document the new statuses in `openspec/specs/dxf-pipeline/spec.md` after archive (handled by `/opsx:archive`); confirm `MEMORY.md` references to the pipeline (`project_smdr2_pipeline`, `project_smdr2_workflow`) are still accurate — update the workflow memory to mention the layer-gate if the user wants. *(Both memories updated to describe the two-phase flow + the auto-opening modal + the viewer "Layers" button. The dxf-pipeline canonical spec update will happen on `/opsx:archive`.)*

## 1. Upload endpoint (`app/main.py`)

- [x] 1.1 In `upload_product_file` (`app/main.py:379` area), add `skip_layer_pick: bool = Form(False)` to the signature. The field is optional and defaults to false, so existing clients keep working untouched.
- [x] 1.2 In the **new-file** branch (the `existing is None` block at `app/main.py:436`): when `skip_layer_pick` is true, call `FILE_STORE.register(..., initial_status=PREPROCESSING)` (instead of `DISCOVERING_LAYERS`), then submit `jobs.submit_preprocess(fid, library_id=product.library_id, selected_layers=None)` (instead of `jobs.submit_discover_layers(fid)`). When false, the existing call path is unchanged.
- [x] 1.3 In the **deduped-rebind** branch (the `else` at `app/main.py:443`): when `skip_layer_pick` is true, the in-place SQL `UPDATE` SHALL set `status = PREPROCESSING` and `selected_layers = NULL` (instead of `status = DISCOVERING_LAYERS, selected_layers = NULL`), and SHALL submit `jobs.submit_preprocess(...)` after commit. When false, the existing path stands.
- [x] 1.4 Update the response dict so the returned `status` reflects the actual starting state: `PREPROCESSING` on skip path, `DISCOVERING_LAYERS` on default path. The response keeps the same shape — `{file_id, product_id, dxf_role, library_id, status, job_id}` — only the `status` value changes per branch.

## 2. Frontend upload zone (`app/static/dashboard.js` + template)

- [x] 2.1 Identify the dashboard's upload-zone container in the existing template / DOM (the file picker + drop zone region near `$fileInput` at `dashboard.js:24`). Mount a hidden-by-default `<label><input type="checkbox" id="skip-layer-pick"> Skip layer picker (dev: use all layers)</label>` element adjacent to (or inside) the upload zone. Add minimal CSS if needed so the checkbox aligns with the existing controls.
- [x] 2.2 Add a localStorage helper pair near `getDevMode` / `setDevMode` (around `dashboard.js:69`): `const SKIP_LAYER_PICK_KEY = "smdr2.dashboard.skipLayerPick"; function getSkipLayerPick() { return localStorage.getItem(SKIP_LAYER_PICK_KEY) === "1"; } function setSkipLayerPick(v) { if (v) localStorage.setItem(SKIP_LAYER_PICK_KEY, "1"); else localStorage.removeItem(SKIP_LAYER_PICK_KEY); }`. Wire the checkbox's `change` event to call `setSkipLayerPick(e.target.checked)`.
- [x] 2.3 On dashboard render (and any time dev mode toggles), set the checkbox's `hidden` flag based on `getDevMode()`. When showing the checkbox, set its `.checked` from `getSkipLayerPick()`. Reuse the existing dev-mode-toggle render hook (`dashboard.js:1081` area) so the visibility flips live without a reload.
- [x] 2.4 In the upload helper(s) that build the `FormData` body for `POST /api/products/{pid}/files`, append `formData.append("skip_layer_pick", "true")` ONLY when `getDevMode() && getSkipLayerPick()`. When either is false, do not append (server defaults to false). Apply this in every code path that submits an upload — drop handler AND file-picker submit AND any "Replace" flow that re-uploads (audit the call sites of `pickFile` / `uploadFile` / equivalent).

## 3. Tests

- [x] 3.1 Add `tests/test_upload_skip_layer_pick.py` with a TestClient that POSTs a tiny DXF to `/api/products/{pid}/files` with `skip_layer_pick=true`. Assert: response `status` is `preprocessing`, the in-memory `jobs._jobs[job_id]["kind"]` is `preprocess` (not `discover` / `discover_layers`), no entry of kind `discover_layers` was registered for this file_id, and `FILE_STORE.get(fid).selected_layers` is `None`. Use a synchronous executor / direct worker drive if needed to keep the test in-process (mirror the pattern from `test_dxf_recover.py::test_on_preprocess_done_persists_recover_notes`).
- [x] 3.2 Sanity baseline `test_upload_without_skip_flag_uses_phase1` — POST to the same endpoint with the flag omitted; assert response `status` is `discovering_layers` and a `discover_layers` job was registered. This locks in the no-regression guarantee.
- [x] 3.3 `test_dedup_rebind_with_skip_flag_routes_to_phase2` — register a file row in `awaiting_layers`, then POST another bytes-identical upload to a different `(pid, role)` slot with `skip_layer_pick=true`. Assert: the row's `status` flips to `preprocessing`, `selected_layers` is `NULL`, the submitted job is preprocess (not discover), and no new file_id is generated.
- [x] 3.4 `test_skip_flag_does_not_write_layer_manifest` — after the skip-path upload completes, assert `data/layer_preview/{file_id}/layers.json` does NOT exist. (Phase 1's manifest write would have created it.)
- [x] 3.5 Verify existing layer-preview / discover tests still pass unmodified — the skip path is purely additive.

## 4. Manual verification

- [ ] 4.1 With dev mode off (default), open the dashboard and confirm the upload zone shows no "Skip layer picker" checkbox.
- [ ] 4.2 Enable dev mode via the existing toggle; confirm the checkbox appears, default state matches `localStorage` (unchecked on first run). Tick it, reload the page, confirm it stays ticked.
- [ ] 4.3 With the checkbox ticked + dev mode on, drag a DXF onto the upload zone. Confirm: DevTools Network shows `skip_layer_pick=true` in the multipart body; the file row never shows `scanning layers` / `pick layers`; the file goes straight to `preprocessing` then `ready_to_match`.
- [ ] 4.4 With the checkbox unticked + dev mode on, upload the same DXF. Confirm: the form field is NOT in the body; the file goes through the normal Phase 1 / `awaiting_layers` flow.
- [ ] 4.5 Turn dev mode off while the checkbox was previously ticked; upload a file. Confirm: no `skip_layer_pick` in the request body; flow is the normal one. Turn dev mode back on; confirm the checkbox renders re-ticked.
- [ ] 4.6 Multi-file batch: drop 5 DXFs at once with the checkbox ticked + dev mode on. Confirm all 5 go straight to preprocessing without any of them ever hitting `awaiting_layers`.

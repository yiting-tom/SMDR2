## 1. Backend role enum + upload validation

- [x] 1.1 Widen `VALID_ROLES` in `app/products.py` to `("SBT", "BD", "POD", "RING", "LID")`.
- [x] 1.2 Update the docstring at the top of `app/products.py` to mention LID and the RING-XOR-LID exclusion.
- [x] 1.3 Update the docstring at the top of `app/product_views.py` to list LID alongside RING.
- [x] 1.4 In `app/main.py::upload_product_file` (the `POST /api/products/{product_id}/files` handler around `main.py:313`), after the existing `dxf_role not in VALID_ROLES` check, add a RING-XOR-LID guard: when `dxf_role` is `RING` or `LID`, query `FILE_STORE.list_by_product(product_id)` for any sibling with the opposite role and, if any exist, raise HTTPException 409 with a message naming at least one conflicting file id.
- [x] 1.5 Ensure `replace_file_id` cannot cross from RING to LID or vice versa (the existing same-role check already enforces this; add an inline comment referencing the new exclusion rule so future readers see why no extra check is needed).

## 2. Rule-check + DRC handoff manifest

- [x] 2.1 Update the docstring in `app/rule_check.py` (the `(SBT, BD, POD, RING)` listing) to include LID.
- [x] 2.2 Update the `tests/test_rule_check.py:77` assertion `part in {"SBT", "BD", "POD", "RING"}` to include `"LID"`.
- [x] 2.3 Widen the `role` enum in `openspec/specs/design-rule-checking/drc-manifest.schema.json` from `["SBT", "BD", "POD", "RING"]` to `["SBT", "BD", "POD", "RING", "LID"]`.
- [x] 2.4 Bump `bundle_version` example in the schema from `"1.0.0"` to `"1.1.0"` and update any string literal in the bundle-emit code path (search for `bundle_version` in `app/`).
- [x] 2.5 In the bundle-emit code (search `app/` for the manifest builder), add a defensive assertion that the emitted `files` array does not simultaneously contain entries with `role: "RING"` and `role: "LID"`. Fail loudly if so.

## 3. Dashboard slot grid (split-half 4th cell)

- [x] 3.1 In `app/static/dashboard.js`, keep the grid at 4 columns but replace the simple `for (const role of ROLES)` loop with: render `SBT`/`BD`/`POD` as today, then call a new `ringLidPairCell(product)` that returns one grid cell containing two adjacent `slotCell`-style halves.
- [x] 3.2 Implement `ringLidPairCell(product)`: build the left (RING) and right (LID) halves by reusing the existing `slotCell` rendering for each role, wrapped in a flex container split 50/50. Each half retains its own drop-zone, file rows, and add-file button — the populated state is identical to today's single RING slot.
- [x] 3.3 Add a `disabled` modifier to the empty-half rendering: when `product.files_by_role_all["RING"]` is non-empty, render the LID half as `slot.empty.disabled` with `cursor: not-allowed`, suppress its click + drag-and-drop handlers, and set `title` to name one of the conflicting RING file ids. Symmetric for the RING half when LID is non-empty.
- [x] 3.4 Add minimal CSS for `.slot.disabled` (dimmed background, no hover effect) and for the 4th cell's split-half flex container.
- [x] 3.5 Verify the rule-check Highlights path (around `dashboard.js:547`) that reads `product.files_by_role_all[sub.part]` still works — the backend already keys this map by `dxf_role`, so a sub-rule with `part: "LID"` lights up the LID half without further changes. Add a comment confirming this.

## 4. Viewer role switcher (`canvas.js`)

- [x] 4.1 In `app/static/canvas.js::renderRoleSwitcher` (around line 153), keep the SBT/BD/POD slot loop as-is for the first three positions, then append two adjacent role-btns for RING and LID at the 4th position (built by a new `renderRingLidPair(product, file)` helper that mirrors the existing `role-btn` rendering for each half).
- [x] 4.2 In `renderRingLidPair`, mirror the dashboard's disabled-half rule: when the product has ≥1 file under the opposite role and 0 under this one, render this half with the existing empty styling **plus** a `disabled` modifier (no click handler, dimmed colour, `title` naming a conflicting file id).
- [x] 4.3 Apply `.current` to whichever half holds the currently-loaded viewer file (e.g., when `file.dxf_role === "LID"`, the LID half is `.current`).
- [x] 4.4 Ensure the dropdown menu logic for multi-DXF roles still works when there are ≥2 files under RING or LID (each half independently can become a `role-btn--multi` button).

## 5. Tests

- [x] 5.1 Add a test in `tests/test_files.py` (or wherever upload-role validation is exercised) that posts a LID file to a product holding RING and expects HTTP 409 with the conflicting file id in the body.
- [x] 5.2 Add the symmetric test: posting RING to a product holding LID → 409.
- [x] 5.3 Add a positive test: LID upload to a product with neither RING nor LID succeeds and the row's `dxf_role` is `"LID"`.
- [x] 5.4 Extend `tests/test_drc_bundle.py` (which already calls `seed("RING")` at lines 147 and 243) to add a parallel `seed("LID")` case and assert the manifest's `role` enum accepts it.
- [x] 5.5 Update any test that hardcodes `("SBT", "BD", "POD", "RING")` to use `("SBT", "BD", "POD", "RING", "LID")` (search the `tests/` tree).

## 6. Spec sync

- [x] 6.1 After implementation lands, run `openspec validate split-ring-into-ring-or-lid` (or the project's standard spec-validate command) and resolve any drift.
- [ ] 6.2 Confirm the dashboard's behaviour matches the `viewer-ui` spec deltas by loading a product through each of the three 4th-slot states (RING / LID / placeholder) in a browser.

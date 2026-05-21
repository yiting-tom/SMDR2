## 1. Server: remove upload-layer XOR

- [x] 1.1 Delete the `RING/LID` mutual-exclusion branch in
  `app/main.py:403-421` (the `if dxf_role in ("RING", "LID")` block
  with the opposite-sibling lookup and HTTP 409 raise)
- [x] 1.2 Update the surrounding docstring (`app/main.py:386-394`) to
  drop the XOR-rule mention; keep the per-role validation paragraph
  — docstring did not mention the XOR rule, no edit needed
- [x] 1.3 Drop the docstring lines about XOR in `app/products.py:5-6`
  and `app/product_views.py:3-4`; keep the role enumeration

## 2. DRC bundle: allow both roles in the manifest

- [x] 2.1 Delete the `if "RING" in roles and "LID" in roles: raise`
  block in `app/drc_bundle.build_manifest`
  (`app/drc_bundle.py:83-92`) and the surrounding comment about the
  external rule-checking team's contract
- [x] 2.2 Confirm no other call site in `drc_bundle.py` or
  `app/main.py:/api/products/{pid}/drc-bundle` re-applies an XOR
  check; if any helper does, drop it too — no other XOR check found

## 3. Rule check: confirm role-bundle generality

- [x] 3.1 Update the `app/rule_check.py` module docstring (lines 4-5)
  to remove "the latter two are mutually exclusive per product"; both
  RING and LID are first-class role keys in `RoleBundle`
- [x] 3.2 No code change to `check_rules` or `_rule_check_worker`
  expected — verified: `_rule_check_worker` (jobs.py:422-456) builds
  `dxfs_by_role` per role-spec generically; `check_rules` only reads
  `dxfs_by_role["BD"|"SBT"|"POD"]` by name; RING/LID pass through

## 4. Dashboard UI: independent slot halves

- [x] 4.1 In `app/static/dashboard.js`, simplify `ringLidPairCell`
  (~lines 528-544) so neither half computes a `disabledReason` from
  the opposite half's file count — both halves call `slotCell` with
  no `disabledReason`
- [x] 4.2 Update the file-header comment (`app/static/dashboard.js`
  lines 7-11) and the `ringLidPairCell` block comment (~lines 523-527)
  to describe the two halves as independent slots rather than
  "mutually exclusive — uploading to one disables the other"
- [x] 4.3 Confirm `slotCell`'s `disabledReason` parameter is left in
  place (still used by the `slot.empty.disabled` styling); it just
  isn't supplied by the RING/LID pair anymore — parameter retained
  at dashboard.js:476-494 with default `null`

## 5. Viewer UI: independent role-btn halves

- [x] 5.1 In `app/static/canvas.js`, simplify `renderRingLidPair`
  (~lines 200-220) to drop the `ringDisabled` / `lidDisabled` branches
  derived from the opposite role; render both halves through the
  single-role renderer using their own file lists
- [x] 5.2 Remove the `console.warn("Product has both RING and LID
  files (server upload-handler should reject this)", ...)` block
  (~lines 205-209) — both-present is now the normal case
- [x] 5.3 Update the surrounding comment block (~lines 199-201) so
  it reflects the new "independent halves" rendering rule

## 6. CSS: keep selectors, drop pair-specific comments

- [x] 6.1 Update the comments in `app/static/style.css` (~lines
  90, 794-795) so they no longer claim the disabled half mirrors a
  server-side XOR rule; leave the selectors themselves alone — the
  `.slot.empty.disabled` / `.role-btn.empty.disabled` styles remain
  reachable via the `disabledReason` parameter

## 7. Tests: replace 409 cases with coexistence cases

- [x] 7.1 In `tests/test_api.py`, delete
  `test_upload_lid_to_product_with_ring_returns_409` and
  `test_upload_ring_to_product_with_lid_returns_409`
- [x] 7.2 Add `test_upload_lid_to_product_with_ring_succeeds` and
  `test_upload_ring_to_product_with_lid_succeeds` — each posts the
  second-role file, asserts HTTP 200, and asserts
  `files_by_role_all` carries both roles
- [x] 7.3 Keep `test_upload_lid_to_empty_product_succeeds` as-is
  (positive-on-empty case still holds)
- [x] 7.4 In `tests/test_drc_bundle.py`, if there is a test that
  asserts `build_manifest` raises when both roles are present, delete
  it and add a positive case that asserts `manifest["files"]` carries
  one RING entry and one LID entry when both are uploaded —
  `test_build_bundle_refuses_mixed_ring_and_lid` replaced by
  `test_build_bundle_includes_both_ring_and_lid`
- [x] 7.5 In `tests/test_rule_check.py`, add a case where a product
  has files under SBT / BD / POD / RING / LID and assert the resulting
  `dxfs_by_role` (or the job result) has all five keys populated
  without error — `test_check_rules_accepts_both_ring_and_lid_in_bundle`

## 7B. Design-rule-checking spec + manifest JSON schema

- [x] 7B.1 Add a `specs/design-rule-checking/spec.md` delta to this
  change that MODIFIES "External DRC handoff bundle format": drop the
  XOR claim in the `role` row, replace the "Manifest never mixes RING
  and LID for one product" Scenario with a positive
  "Product carries both RING and LID" Scenario
- [x] 7B.2 Update the role property description in
  `openspec/specs/design-rule-checking/drc-manifest.schema.json` to
  drop the "RING and LID are mutually exclusive" wording; leave the
  `enum` list unchanged

## 8. Spec archive readiness

- [x] 8.1 Re-run `openspec validate drop-ring-lid-exclusion` and fix
  any structural issues the linter flags — valid
- [x] 8.2 Run `pytest tests/test_api.py tests/test_drc_bundle.py
  tests/test_rule_check.py tests/test_products.py` and confirm green
  — 73 passed
- [ ] 8.3 Manually upload a RING and then a LID via the dashboard for
  the same product; confirm both halves render populated and Rule
  Check completes — left for the user to verify in the browser

## Why

Today a product can only hold DXFs under one of `RING` or `LID`, never both
— the second upload to the opposite slot is rejected with HTTP 409. Real
packages routinely involve both a RING and a LID, and forcing the user to
pick one means a single product card can't represent the whole part. The
exclusion was a guardrail from when downstream rule-check / DRC bundle
couldn't reason about both roles at once; that constraint has since
relaxed, so the upload-layer XOR is now pure friction.

## What Changes

- **BREAKING (server)**: `POST /api/products/{pid}/files` no longer
  rejects RING uploads when the product already has LID files (and
  vice versa). The HTTP 409 path for the RING/LID pair is removed.
- **BREAKING (DRC bundle)**: `drc_bundle.build_manifest` no longer
  raises when a product holds both RING and LID files; both roles
  appear in the same manifest as first-class entries.
- **BREAKING (rule check)**: `rule_check` includes both `RING` and
  `LID` role bundles when both have files; downstream rule executors
  see the package as having both halves.
- **UI (dashboard)**: the 4th grid cell still places RING on the
  left and LID on the right, but each half is now an independent
  slot — both can be filled, neither half disables the other.
- **UI (viewer)**: the role switcher renders both RING and LID
  buttons concurrently when both roles have files; neither side is
  rendered as a locked / disabled placeholder anymore.
- Tests asserting the 409 rejection (`test_upload_lid_to_product_with_ring_returns_409`
  and its mirror) are replaced with positive cases that confirm both
  uploads succeed and the product surfaces files under both roles.

## Capabilities

### New Capabilities

(none — this change relaxes existing requirements)

### Modified Capabilities

- `product-files`: removes the "RING / LID per-product mutual
  exclusion" Requirement entirely. The "Multiple DXFs per (product,
  role)" Requirement's reference to that rule is removed.
- `viewer-ui`: removes the split-pair disabled-half rules in the
  role-switcher / dashboard slot-grid descriptions; the 4th cell's
  RING and LID halves become independently fillable.
- `design-rule-checking`: the "External DRC handoff bundle format"
  Requirement's `role` row drops the XOR claim; the "Manifest never
  mixes RING and LID for one product" Scenario is replaced with a
  positive "Product carries both RING and LID" Scenario.

## Impact

- **Code**
  - `app/main.py` — drop the RING/LID XOR branch in the upload handler
  - `app/drc_bundle.py` — drop the `roles_present` XOR guard; let the
    bundle carry both roles
  - `app/static/dashboard.js` — `ringLidPairCell` no longer computes
    a `disabledReason`; both halves render as plain slots
  - `app/static/canvas.js` — `renderRingLidPair` drops the
    locked-half branch and the "both files present" console warning
  - `app/static/style.css` — the `.slot.empty.disabled` styling tied
    to the pair is no longer reached from the RING/LID pair (kept
    intact in case future code needs it)
  - `app/products.py`, `app/product_views.py` — drop docstring
    references to the XOR rule
- **Tests**
  - `tests/test_api.py` — replace the two 409 tests with positive
    "both roles coexist" tests
  - `tests/test_drc_bundle.py` — replace any "raises on both" test
    with a manifest-shape assertion that includes both roles
  - `tests/test_rule_check.py` — extend coverage so a product with
    both RING and LID feeds both bundles into the rule executor
- **DRC manifest JSON schema**
  - `openspec/specs/design-rule-checking/drc-manifest.schema.json` —
    update the `role` property's description text to drop the XOR
    claim (the `enum` itself is unchanged)
- **No DB schema change** — the constraint was always application-
  layer, not a DB CHECK.

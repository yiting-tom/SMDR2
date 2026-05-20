## Why

The current `VALID_ROLES = ("SBT", "BD", "POD", "RING")` collapses two
physically distinct packaging parts onto one slot: some products use a
**RING** (open-frame stiffener), others use a **LID** (closed-top cover).
They are alternative configurations of the same product family — a
product is built with exactly one of them, never both — so engineers
have been overloading the RING slot to upload either kind and can no
longer tell from the dashboard which configuration a product is in.

Splitting RING into RING / LID restores that visibility and lets future
DRC rules target each part separately (e.g., lid-specific clearance
rules) without re-mining the file's geometry to guess which it is.

## What Changes

- **BREAKING**: Add `LID` to `VALID_ROLES`. New valid set is
  `("SBT", "BD", "POD", "RING", "LID")`.
- Enforce per-product **mutual exclusion** of `RING` and `LID`: a
  product MAY hold any number of DXFs under at most one of the two,
  never both. The first upload to either slot fixes the choice for
  that product; subsequent uploads to the other are rejected.
- Dashboard slot grid stays at 4 columns; the 4th slot resolves to
  `RING` or `LID` based on which (if any) the product already holds.
  Brand-new products with neither show a combined `RING / LID` empty
  placeholder that lets the engineer pick on first upload.
- Viewer role switcher renders the same 4 slots with the same
  RING-or-LID resolution rule.
- DRC handoff manifest `role` enum and the RuleChecking JSON `part`
  enum both widen to include `"LID"`. Existing per-role merge logic
  is unchanged — it already treats role as an opaque key.
- Tests covering role enums (`tests/test_rule_check.py`,
  `tests/test_drc_bundle.py`) gain LID coverage and an exclusion case.

## Capabilities

### New Capabilities
- (none)

### Modified Capabilities
- `product-files`: VALID_ROLES widens to include `LID`; a new
  per-product RING-XOR-LID exclusion requirement is added.
- `design-rule-checking`: manifest schema `role` enum and the
  RuleChecking sub-rule `part` enum widen to include `"LID"`.
- `viewer-ui`: per-role sibling-DXF dropdown switcher's 4th slot
  resolves to RING or LID per product (instead of hardcoded RING).

## Impact

- Code: `app/products.py` (VALID_ROLES), `app/files.py` (upload
  validation), `app/static/dashboard.js` (slot grid),
  `app/static/canvas.js` (role switcher), `app/rule_check.py`
  (docstring + part enum), DRC manifest schema.
- Specs: `product-files`, `design-rule-checking`, `viewer-ui`.
- Tests: enum assertions and a new mutual-exclusion test case.
- Migration: no existing production data assumed; any local dev
  rows with `dxf_role = 'RING'` remain valid (RING is not removed).

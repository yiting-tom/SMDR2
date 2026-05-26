## Why

The backend `DELETE /api/products/{product_id}/files/{file_id}`
endpoint (`app/main.py:287`) already detaches a file from its product
slot, and the front-end has a working `deleteProductFile` helper. But
the Delete button is only rendered in `compact` mode — `buildFileActions
(product, role, f, compact)` in `app/static/dashboard.js:440` gates the
button behind `if (compact)`, which is true only for the 2+ DXF
"stacked rows" case. A single-file slot exposes only Replace.

This was an acceptable trade-off when Replace was enough to swap any
file. With the RING / LID per-product mutual exclusion landed in
`2026-05-20-split-ring-into-ring-or-lid`, it now blocks a real flow:
to convert a product from a RING configuration to a LID configuration,
the engineer must first detach the lone RING file — but the
single-file slot has no Delete affordance, so the only escape is to
delete the entire product and rebuild it. That's a documented
regression from the design doc of the RING/LID change, which said
"the engineer MUST detach every RING file first".

## What Changes

- Expose the existing Delete button on **single-file slots** as well as
  multi-DXF slots. The button reuses the existing `deleteProductFile`
  path (confirm dialog → `DELETE /api/products/{pid}/files/{fid}` →
  refresh).
- No backend changes: the DELETE endpoint and the FILE_STORE detach
  logic are already in place and covered by tests
  (`tests/test_api.py:344` and surrounding).
- Update the dashboard slot UI requirement so single-file slots are
  documented to expose Delete alongside Replace.
- Out of scope for this change: the viewer header currently exposes no
  file-mutation affordances. Adding Delete there is deferred — the
  dashboard is the canonical file-management surface, and pushing a
  destructive action into the viewer chrome would surprise engineers
  mid-pattern-matching.

## Capabilities

### New Capabilities
- (none)

### Modified Capabilities
- `viewer-ui`: the dashboard product card's per-file action set widens
  to always include Delete, removing the single-file/multi-file split.

## Impact

- Code: `app/static/dashboard.js` — drop the `if (compact)` gate around
  the Delete button in `buildFileActions`; the rest of the function and
  the `deleteProductFile` helper are unchanged.
- Specs: `viewer-ui`.
- Tests: an integration / playwright-style UI test would be ideal but
  the project doesn't have one for slot actions today; behavior is
  covered indirectly by the existing backend DELETE test.
- Risks: an engineer mid-task could fat-finger Delete instead of
  Replace. The existing `confirm()` dialog (`"Remove "<name>" from
  <ROLE>?"`) is the safety net; this change leaves it in place.

## Context

`buildFileActions(product, role, f, compact)` in
`app/static/dashboard.js` renders the per-file action bar (Open,
Layers, Replace, optional Download Match, optional Delete). Today the
Delete button is gated by `if (compact)` — true only when the slot
has 2+ DXFs and renders the stacked-row layout. The original
rationale (preserved as a comment): "The delete control is only
useful when there are siblings to keep; for the single-file slot,
'Replace' already covers the swap path."

That rationale is now stale. The RING / LID mutual-exclusion change
introduced a flow where the engineer needs to **empty** a slot (not
swap it), and they may have only one file in that slot at the time.
Replace doesn't help because it requires uploading a new DXF.

## Goals / Non-Goals

**Goals:**
- A single-file slot exposes Delete with the same visual treatment
  and confirm dialog as the multi-DXF case.

**Non-Goals:**
- No backend change. The DELETE endpoint, FILE_STORE detach, and
  Match JSON cleanup are already in place.
- No new viewer-header Delete affordance. The dashboard remains the
  canonical file-management surface.
- No undo / trash flow. The current Delete is a soft detach (the
  file row stays; only the product binding clears), so the file can
  be recovered out-of-band if needed.

## Decisions

### Drop the `compact` gate in `buildFileActions`

The minimal change: render the Delete button unconditionally. The
existing styling and `deleteProductFile` handler need no changes —
they already work for the single-file case (the function takes
`(product, role, file)` and doesn't care about siblings).

**Alternative considered — add a separate Delete affordance only for
single-file slots**: rejected. Two parallel code paths for the same
action invite drift.

### Keep the existing `confirm()` dialog

The current prompt — `Remove "<name>" from <ROLE>?` — is sufficient
for the new case. We do not need to add LID/RING-specific language;
the engineer reading the prompt knows they're emptying a slot. The
detach is reversible by re-uploading the same DXF (content-addressable
storage will reuse the existing file row).

## Risks / Trade-offs

- **Risk — accidental click on Delete instead of Replace** for a
  user who has only ever seen one button.
  → Mitigation: the `confirm()` dialog already guards every Delete;
  no further hand-holding warranted for a soft detach.

- **Risk — confusion if Delete behaves differently from "Delete
  product"** (which destroys the product).
  → Mitigation: the slot-level button reads "✕" (per the existing
  multi-DXF style); the product-level button reads "Delete". The
  tooltips differentiate ("Remove this DXF from the role" vs "Delete
  this product"). No change needed.

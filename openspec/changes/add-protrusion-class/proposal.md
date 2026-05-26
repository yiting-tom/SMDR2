## Why

The default class taxonomy covers BGA balls, SMD pads (2T/3T/8T/14T),
lid / substrate frames, fiducials, and barcodes — but lacks a label
for protrusion features that appear on certain IC packages
(die-attached protrusions, frame protrusions, mould-side bumps). Users
were committing such templates under arbitrary classes for lack of a
canonical one. Adding `Protrusion` to the seeded default class list
gives them an unambiguous home.

## What Changes

- `DEFAULT_CLASSES` SHALL include a new canonical class `Protrusion`
  inserted between `BGABall` and `2DBarcode` (default order position
  11 of 15).
- `CLASS_JSON_KEY` SHALL map `Protrusion` → `protrusion` so the
  match-JSON / rule-checker handoff sees `protrusion` (snake_case)
  keys.
- The viewer SHALL render `Protrusion` toolbar buttons with the
  accent colour `#80d8ff` (light blue), distinct from the SMD red
  family and the BGA-ball orange.
- Existing libraries SHALL pick up the new class on next boot via the
  existing `add_class` seeding pass in `Library.__init__`. No
  migration / data-rewrite is needed.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `template-library`: the canonical default class count moves from
  14 to 15 and the seed-order requirement gets a new entry at
  position 11.

## Impact

- `app/library.py`: `DEFAULT_CLASSES` insert + `CLASS_JSON_KEY` add.
- `app/static/canvas.js`: `CLASS_COLORS["Protrusion"]` entry.
- `openspec/specs/template-library/spec.md`: count update (14 → 15),
  list update with Protrusion at position 11, scenario update.
- Existing tests parametrise over `DEFAULT_CLASSES` so they auto-cover
  the new class — no test changes needed.

This change was implemented in commit `b630c26` (2026-05-20) and this
OpenSpec record is being added retroactively so the change-tracking
log stays complete.

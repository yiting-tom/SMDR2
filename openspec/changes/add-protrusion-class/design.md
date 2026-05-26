## Context

The `template-library` spec lists canonical default classes in a fixed
order; the spec is explicit that order = toolbar / class-list order in
the UI. Adding a class is therefore a small but spec-bearing change:
both the count and the position need to be agreed.

## Goals / Non-Goals

**Goals:**
- Add `Protrusion` to the seeded class list with a stable canonical
  name + snake_case JSON key + UI accent colour.
- Keep the change non-destructive: existing libraries get the new
  class on next boot, no data migration.

**Non-Goals:**
- Not changing match-strategy defaults — Protrusion starts at
  `chamfer` like every other class.
- Not adding rule-checker-side handling for Protrusion. Rules that
  involve Protrusion can be added later via the existing rule
  authoring flow.

## Decisions

**Position 11 (between BGABall and 2DBarcode)** (over: trailing
position; over: inside the collapsed SMD fold group).

- Protrusion is a positive-relief feature like BGA balls and SMD pads
  (height above the substrate) rather than a 2D feature like the
  barcode or substrate frame. Grouping it next to BGABall reads
  naturally in the toolbar. Position 11 keeps the `2DBarcode → SMD-3T
  → SMD-8T → SMD-14T` tail unchanged.

**`#80d8ff` light blue accent** (over: reusing a colour from another
class family).

- Each existing class has its own hue or hue-family: SMD reds,
  lid purples, fiducial teals, BGABall orange, Substrate mint,
  DieArea yellow, 2DBarcode lime. Light blue is the unused gap;
  it's distinct from teal (fiducial) and the rest.

**No JSON-key migration** (existing libraries don't have any data
filed under `Protrusion`, so there's nothing to rename).

## Risks / Trade-offs

- [Position 11 disrupts users' muscle memory who knew "position 11 is
  2DBarcode"] → Mitigation: the toolbar shows class names, not
  positions, so the visual order shift is the change the user
  actually sees. One-time orientation cost.
- [Future class additions need their own design decisions on
  position / colour] → Acceptable; each addition is small and the
  spec captures the canonical order.

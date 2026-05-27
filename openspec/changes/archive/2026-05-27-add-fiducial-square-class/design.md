## Context

The canonical class list is owned by `app/library.py` in two parallel
constants:

- `DEFAULT_CLASSES: list[str]` — seeded into every library on creation
  and re-ranked on every boot to track the canonical order.
- `CLASS_JSON_KEY: dict[str, str]` — display ID → snake_case key written
  into `data/match/{file_id}.json` and consumed by the rule checker.

Display colors live separately in `app/static/canvas.js` (`CLASS_COLORS`).

The Store's `_migrate()` already idempotently `INSERT OR IGNORE`s every
`DEFAULT_CLASSES` entry into every existing library on boot and then
re-ranks rows by their canonical position. This means adding a new entry
to `DEFAULT_CLASSES` automatically propagates to all libraries without a
bespoke migration step. This was the path used by the
`add-c4-ball-class` change (archived 2026-05-20).

Two fiducial classes already exist: `FiducialCircle` and `FiducialCross`.
A square fiducial — a geometrically distinct alignment mark common in
substrate / PCB layouts — currently has no canonical home, forcing
users to mis-file it under one of the existing fiducial classes.

## Goals / Non-Goals

**Goals:**
- Make `FiducialSquare` a first-class default class, ranked immediately
  after `FiducialCross`, with the snake_case JSON key `fiducial_square`.
- Surface a distinct toolbar color in the existing fiducial-family
  gradient so users can tell square fiducials from circle / cross at a
  glance while still reading the trio as one cluster.
- Existing libraries pick up the new class on next boot with no manual
  step.

**Non-Goals:**
- Auto-classifying existing fiducial-labelled templates as
  `FiducialSquare`. Re-tagging is a manual user decision.
- Adding `FiducialSquare`-specific rules to the rule checker. Any
  fiducial-population checks involving the new class SHALL be filed as a
  separate change if needed.
- Changing existing `FiducialCircle` / `FiducialCross` colors or any
  other class's appearance.
- Adding `FiducialSquare` to any arbitration group. Square fiducials
  have a distinct shape signature from the BGA / circle-fiducial
  same-size collision that motivates the existing arbitration registry,
  so pure pattern matching is sufficient.

## Decisions

### Position: directly after `FiducialCross`

The canonical order groups visually/functionally similar classes
together (SMD variants cluster, Lid variants cluster, ball-type cluster
from the prior C4Ball change). All three fiducial classes share the
"alignment marker" role; grouping them lets the toolbar read as a
"fiducial" cluster. Resulting order:

```
…DieArea → FiducialCircle → FiducialCross → FiducialSquare → SMD-2T…
```

**Alternatives considered:**
- *Append to end of list.* Easier (avoids any ordinal renumber in the
  spec), but separates the new fiducial class from its two siblings,
  which is semantically wrong and hurts toolbar scanability.
- *Between `FiducialCircle` and `FiducialCross`.* Breaks the existing
  Circle → Cross adjacency that has been the canonical order since the
  fiducial split. The user's explicit instruction ("在 FiducialCross
  後面") also rules this out.

### Color: `#00acc1` — one shade darker teal

The existing fiducial family in `CLASS_COLORS` is:
- `FiducialCircle`: `#4dd0e1` (teal)
- `FiducialCross`: `#26c6da` (darker teal — sibling of FiducialCircle)

`FiducialSquare` continues the gradient with `#00acc1` (one shade darker
still). This keeps the family readable as a teal cluster in the Scan All
overlay while keeping the three siblings mutually distinguishable when
they appear in the same view.

**Alternatives considered:**
- *Reuse one of the existing fiducial colors (analogue to the
  C4Ball/BGABall unification).* Rejected: the C4/BGA unification was
  driven by the user explicitly saying both classes should read as one
  ball-type group in the overlay. No equivalent user signal exists for
  fiducials — and unlike balls, the three fiducial shapes are visually
  distinct on the DXF itself, so a shared overlay color would be
  redundant rather than clarifying.
- *Step outside the teal family (e.g., a green or magenta).* Breaks the
  visual "this is a fiducial" cue established by the existing two
  classes, and pushes the toolbar palette further from the deliberate
  family taxonomy (Red = SMD, Purple = Lid, Teal = Fiducial,
  Orange = ball-type).

### Migration: lean on the existing boot loop

The current `_migrate()` boot loop already iterates `DEFAULT_CLASSES`
and `INSERT OR IGNORE`s missing rows per library, then re-ranks every
library against the canonical order. Adding `FiducialSquare` to
`DEFAULT_CLASSES` is the only change required — no bespoke migration
code, no version bump. This mirrors the `add-c4-ball-class` migration.

The `LEGACY_CLASS_RENAME` map is intentionally **not** touched. There
is no historical class to rename to `FiducialSquare`; users with
existing custom "square fiducial" classes can move templates manually
via the existing template-move endpoint.

### No `CLASS_VIEW_CONSTRAINTS` entry

`FiducialCircle` and `FiducialCross` are both view-unconstrained
(absent from the `CLASS_VIEW_CONSTRAINTS` map), meaning they can appear
in any view including the "unassigned" position. Square fiducials share
the same physical role and SHALL match this default. Constrained-view
support is reserved for classes with a real physical restriction
(C4Ball top-only, BGABall bottom/side).

## Risks / Trade-offs

- **Risk**: Users who previously hand-labelled square fiducials under
  `FiducialCircle` or `FiducialCross` now see a third fiducial class in
  the toolbar and may not realise they should re-tag. → **Mitigation**:
  no automatic re-classification, and the new class's toolbar slot
  appears empty on first boot until the user files a template into it
  — which is the natural cue to re-classify any historical squares.
- **Risk**: Rule checks that count fiducials by JSON key (if any exist)
  see a new key `fiducial_square` they don't know about. → **Mitigation**:
  audit `rule_check.py` for hard-coded fiducial keys during
  implementation; this change MAY require touching no rule code at all
  if the existing rules count by class name from the live list rather
  than from a hard-coded enum. Out of scope to add new
  `FiducialSquare`-aware rules in this change.
- **Trade-off**: The spec's `### Requirement: Display name vs.
  match-JSON key separation` table is the source of truth for valid
  display → JSON mappings. Adding `FiducialSquare` requires editing the
  table *and* the numbered list *and* every count assertion (16 → 17)
  — three touchpoints to keep in sync, but no worse than the prior
  `add-c4-ball-class` change.

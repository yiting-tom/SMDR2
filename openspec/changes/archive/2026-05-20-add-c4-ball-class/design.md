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
bespoke migration step.

## Goals / Non-Goals

**Goals:**
- Make `C4Ball` a first-class default class, ranked immediately before
  `BGABall`, with the snake_case JSON key `c4_ball`.
- Surface a distinct toolbar color so users can tell C4 from BGA at a
  glance.
- Existing libraries pick up the new class on next boot with no manual
  step.

**Non-Goals:**
- Auto-classifying existing BGA-labelled templates as C4Ball. Re-tagging
  is a manual user decision.
- Adding C4Ball-specific rules to the rule checker. Rule changes (e.g.,
  parity between SBT and POD on C4 counts) are out of scope for this
  proposal and SHALL be filed as a separate change if needed.
- Changing the BGA color or any other class's appearance.

## Decisions

### Position: directly before `BGABall`

The canonical order groups visually/functionally similar classes
together (SMD variants cluster, Fiducial variants cluster, Lid variants
cluster). Both C4Ball and BGABall are ball-type interconnect; grouping
them lets the toolbar read as a "ball" cluster and matches how
packaging engineers think about the stack (chip → C4 bumps → substrate
→ BGA balls → board). Resulting order:

```
…SMD-2T → C4Ball → BGABall → Protrusion…
```

**Alternatives considered:**
- *Append to end of list.* Easier (no rank shifts in existing libraries
  beyond what the re-rank pass handles anyway), but groups C4 with the
  SMD-3T/8T/14T fold cluster, which is semantically wrong.
- *After DieArea.* C4 bumps physically sit under the die, so spatial
  proximity is real, but DieArea/Fiducial are area/marker classes — the
  ball-with-ball grouping reads better in the toolbar.

### Color: same orange as BGABall (`#ffab40`)

`BGABall` currently uses `#ffab40` (orange). `C4Ball` SHALL use the
**exact same color**. Both are ball-type interconnect; visually
unifying them in the Scan All overlay makes the "all the balls"
gestalt read clearly in one glance, and the toolbar label + class
filter already disambiguate the two when the user needs to act on a
specific class. This keeps the color taxonomy lean (Red family = SMD,
Purple family = Lid, Teal family = Fiducial, Orange = ball-type).

**Alternatives considered:**
- *Distinct amber sibling (`#ffd180`).* Initial proposal — keeps them
  in the same family but still tells them apart side-by-side. Rejected
  by user: ball-type interconnect should read as one visual group;
  per-class distinction belongs to the toolbar/legend, not the overlay
  color.

### Migration: lean on the existing boot loop

The current `_migrate()` boot loop already iterates `DEFAULT_CLASSES`
and `INSERT OR IGNORE`s missing rows per library, then re-ranks every
library against the canonical order. Adding `C4Ball` to `DEFAULT_CLASSES`
is the only change required — no bespoke migration code, no version
bump.

The `LEGACY_CLASS_RENAME` map is intentionally **not** touched. There is
no historical class to rename to `C4Ball`; users with existing custom
"C4" classes can move templates manually via the existing template-move
endpoint.

## Risks / Trade-offs

- **Risk**: Users who previously hand-labelled C4 bumps as `BGABall`
  now see two classes in the toolbar that render in the **same**
  overlay color and may struggle to tell them apart in the rendered
  layer. → **Mitigation**: no automatic re-classification, and the
  toolbar label (display ID) is the primary disambiguator — the
  overlay color is intentionally unifying. If side-by-side
  distinction in the overlay becomes a real pain point, revisit and
  split the color (e.g., back to `#ffd180` for C4) in a follow-up
  change.
- **Risk**: Rule 2 (SBT/POD BGA-ball parity in `rule_check.py`) counts
  only `bga_ball` keys. If users start filing C4 templates under
  `c4_ball`, that does not retroactively break Rule 2 — but it does
  mean C4 counts are unchecked by any rule until a follow-up change
  adds a parallel parity rule. → **Mitigation**: explicitly out of
  scope; called out in Non-Goals so the gap is visible.
- **Trade-off**: The spec's existing `### Requirement: Display name vs.
  match-JSON key separation` table is the source of truth for valid
  display → JSON mappings. Adding `C4Ball` requires editing the table
  *and* the numbered list — two touchpoints to keep in sync, but no
  worse than every prior class addition.

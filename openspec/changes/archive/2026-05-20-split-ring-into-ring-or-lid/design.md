## Context

`VALID_ROLES` is the closed enum that gates DXF uploads into a product
slot. Today it lists `("SBT", "BD", "POD", "RING")` — four hardcoded
roles consumed by the upload handler (`app/files.py`), the dashboard
slot grid (`app/static/dashboard.js`), the viewer role switcher
(`app/static/canvas.js`), the rule-check merge (`app/rule_check.py`),
and the external DRC handoff manifest (JSON Schema enum). RING is
overloaded — engineers upload either a ring stiffener or a closed lid
into it, and the only way to tell which is to open the DXF.

The 4-slot grid is load-bearing UX: it acts as an upload-progress
checklist (see `viewer-ui` spec "the toolbar always renders four slots
in a stable order — empty roles included"). Splitting RING into two
separate slots would expand the grid to 5 columns, which (a) breaks the
visual symmetry the engineers rely on and (b) wastes one slot on every
product since RING and LID are alternatives, never coexistent.

## Goals / Non-Goals

**Goals:**
- Make `LID` a first-class role distinct from `RING`.
- Enforce at upload time that a product cannot hold both RING and LID
  DXFs simultaneously.
- Keep the 4-column dashboard grid and the 4-slot viewer role switcher
  visually stable.
- Widen the DRC handoff manifest and RuleChecking JSON enums so the
  external rule team can target LID-specific rules in the future.

**Non-Goals:**
- Auto-detecting RING vs LID from DXF geometry. The engineer chooses.
- Adding LID-specific DRC rules in this change. (Rule_check.py
  currently has no RING-only rule; LID inherits the same no-op
  treatment until a future change introduces lid rules.)
- Migrating any existing RING data to LID. Pre-existing RING rows
  remain valid RING rows.

## Decisions

### Mutual exclusion enforced at the upload handler, not the DB

`dxf_role` is a free-form `TEXT` column today; there is no CHECK
constraint and no per-product uniqueness. Adding a DB-level CHECK for
"RING xor LID" would require a partial unique index on a computed
predicate, which SQLite supports but makes the schema noisier than the
business rule warrants.

**Decision:** validate on write in `app/files.py` (the path that already
calls `validate_role`). Before binding a new file to `(product, RING)`,
query whether any sibling file in the same product has
`dxf_role = 'LID'` (and vice versa). If yes, return HTTP 409 with a
clear message naming the conflicting file. This keeps the rule in one
auditable function and lets us reuse the existing upload-rejection UX.

**Alternative considered — DB CHECK + partial unique index**: rejected
as over-engineered for a single binary constraint. We can always
promote to a DB constraint later if app-layer validation proves leaky.

### Replace-file semantics across RING/LID is forbidden

The existing `replace_file_id` flow only allows replacing a file within
the same `(product, role)` (see `product-files` spec "replace_file_id
from another product or role is rejected"). That naturally forbids
swapping a RING file for a LID one via replace — the request would be
rejected as a cross-role replace. **No change needed.**

If the engineer wants to convert a product from RING to LID, they MUST
detach every RING file first (via a future "detach" flow or by deleting
the product). This change does NOT add a "convert ring to lid"
shortcut; explicit detach keeps the slot's identity unambiguous.

### Dashboard 4th slot: split-half `RING | LID` cell

The grid stays at 4 columns. The 4th column is a **single cell split
visually into two halves** — left half is RING, right half is LID.
The two halves always render side-by-side; the engineer sees both
options up-front. Each half independently behaves like the existing
single-role slots (drop-zone when empty, file row(s) when populated)
but with the additional rule that **whichever half is empty becomes
disabled the moment the other half holds ≥1 file**, enforcing the
RING-XOR-LID exclusion visually.

Per-product rendering of the 4th cell:

| Product state                | Left half (RING)            | Right half (LID)            |
|------------------------------|-----------------------------|-----------------------------|
| 0 RING, 0 LID                | empty drop-zone (enabled)   | empty drop-zone (enabled)   |
| ≥1 RING, 0 LID               | file row(s) (current)       | **disabled** placeholder    |
| 0 RING, ≥1 LID               | **disabled** placeholder    | file row(s) (current)       |
| ≥1 RING AND ≥1 LID           | **unreachable** (rejected at upload — see `product-files`) |

The disabled-half placeholder SHALL:
- carry a dimmed appearance distinct from the standard `slot.empty`
  dashed-border style (e.g., a `slot.disabled` class with `cursor:
  not-allowed`),
- show a `title` attribute naming the file id that locked the
  product into the other configuration (so the engineer knows what
  to delete to re-open the choice),
- not respond to clicks, drag-over highlight, or drop events — the
  drag/drop handlers SHALL no-op on disabled halves.

Each half's existing populated behaviour (file row, status badge,
Replace button, Add-file button for the multi-DXF case) is unchanged
from the current single-role slot. The split is purely a layout
refinement of the empty/disabled states.

### Viewer role switcher: split 4th slot mirrors dashboard

The viewer header's 4th position also splits into left (RING) and
right (LID) sub-slots, applying the same enable/disable rules. The
hardcoded list `["SBT", "BD", "POD", "RING"]` in `canvas.js:156`
becomes 3 single-role slots followed by a `renderRingLidPair(product,
file)` helper that emits two side-by-side role buttons.

When the viewer is loaded on a file under the active half (e.g., the
current file's `dxf_role === "LID"`), that half carries `.current`;
the other half is disabled with the same `title` explanation as the
dashboard.

The visual split keeps the toolbar at 4 conceptual positions (so the
existing layout / spacing rules in the spec still hold), but each
half is an independent role-btn for hit-testing.

### Spec enum widening is additive

Both the DRC manifest `role` enum and the RuleChecking sub-rule `part`
enum gain `"LID"`. This is an additive change to existing handoff
consumers: producers (us) may now emit `"LID"`; consumers that don't
recognize it should refuse the bundle per the existing
`bundle_version` major-version rule. We bump `bundle_version` to
`"1.1.0"` (minor) since adding an enum value is backward-compatible at
the JSON level even if not all consumers understand it semantically.

## Risks / Trade-offs

- **Risk — engineers upload to the wrong slot on a brand-new product.**
  → Mitigation: the upload modal's role picker labels each option with
  a one-line clarification ("RING = open-frame stiffener", "LID =
  closed-top cover"). The choice is reversible only by deleting the
  uploaded file (no implicit conversion path).

- **Risk — rule-check pipeline still references the literal `"RING"`
  in three places (docstrings + tuple).**
  → Mitigation: the merge path already treats role as an opaque
  dictionary key (`dxfs_by_role.get(role)`), so widening the enum at
  the boundaries (validate_role, manifest schema, RuleChecking enum)
  is enough. The `rule_check.py` Rule1/2/3 code paths name `BD`,
  `SBT`, `POD` directly and never touch RING/LID. New LID-specific
  rules are out of scope for this change.

- **Risk — `bundle_version` consumers pinned to `1.0.x` reject the
  manifest after the bump to `1.1.0`.**
  → Mitigation: per the existing rule, consumers MUST refuse only on
  **major** version they don't understand. A minor bump is the
  contract-correct signal. If the external team's parser is stricter
  in practice, we coordinate the upgrade before the first LID
  product ships.

## Migration Plan

1. Land the code + spec changes (additive enum widening).
2. No data migration: existing rows with `dxf_role = 'RING'` remain
   valid RING rows.
3. The first LID upload to any product proves the new path end-to-end.

Rollback: if the upload-side exclusion proves disruptive, revert
`app/files.py` to accept either; the manifest and UI changes are
backward-compatible on their own.

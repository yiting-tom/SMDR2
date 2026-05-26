## Context

The `auto-normalize-unit-suspect-dxf` change introduced
`detect_scale_factor(insunits, bbox_diagonal)` and persisted the result
on `files.applied_scale`. Downstream code reads `applied_scale` as
ground truth — viewer, matcher, rule-check, and the dashboard pill all
trust it. The detector is a heuristic over packaging-domain priors (10
mm – 5000 mm bbox, prefer smallest in-range factor, guard at
`|log10(M)| > 1`) and is intentionally conservative; the spec's
"Marginal-factor unitless DXF stays at 1.0" scenario exists precisely
because the safe choice was to leave such files alone. That means
there is a known residual class of files where the detector chooses
`1.0` but the geometry is clearly in the wrong units, and another
class where a DXF's declared `INSUNITS` is itself wrong.

Operators have already hit both. Today their only recourse is to leave
the project, fix the DXF in CAD, and re-upload. We want them to fix it
in place from the viewer the moment they spot it.

## Goals / Non-Goals

**Goals:**
- One-click way for the engineer to override the unit interpretation
  of a single file from the viewer.
- The override is durable (survives reload / re-preprocess).
- All downstream consumers (matcher, rule-check, dashboard pill,
  thumbnails) honour the override automatically — no consumer needs
  to learn a second unit-resolution path.
- Override is reversible: setting the picker back to the detector's
  natural choice clears the override row and restores detector
  authority.
- The cost of overriding (cache invalidation, re-matching) is made
  explicit before the operator commits.

**Non-Goals:**
- No bulk override across many files. One file at a time, from the
  viewer for that file.
- No override at upload time. Operator has no signal then.
- No silent auto-correct of declared `INSUNITS`. We do not edit the
  DXF — we layer interpretation on top.
- No per-layer or per-region overrides. Scale is a whole-file concept.
- No retroactive re-application of override after detector logic
  changes. If the detector improves in the future and the override
  ends up agreeing with it, the override is still recorded — that is
  fine; clearing is a manual act.

## Decisions

### D1. Where the override is applied

`flatten_for_render` is the single chokepoint that produces both the
flattened primitives and the recorded `applied_scale`. The override is
plumbed into this function. When an override is supplied, we **skip**
`detect_scale_factor` entirely and derive `M` from the override:

| `user_unit_override` | `M` (override path) |
|---|---|
| `"mm"`   | `1.0`    |
| `"cm"`   | `10.0`   |
| `"m"`    | `1000.0` |
| `"inch"` | `25.4`   |
| `"μm"`   | `0.001`  |

`applied_scale` is then persisted as usual. No downstream code learns
a second code path — `applied_scale` remains the only field they
read.

**Alternative considered:** keep `detect_scale_factor` running and let
the override post-multiply on top. Rejected: it introduces a second
source of truth for "what factor was applied," and the audit trail on
the dashboard pill becomes ambiguous (was the ×100 from detector or
from operator?). Single chokepoint is simpler.

### D2. Storage shape

Add `files.user_unit_override TEXT NULL`, constrained to the set
`{NULL, "mm", "cm", "m", "inch", "μm"}` via app-level validation
(SQLite has no enum type; we trust the writer).

`applied_scale REAL NOT NULL DEFAULT 1.0` stays as-is. It is now
written by either the detector or the override resolver, depending on
which path fired. The pair `(user_unit_override, applied_scale)`
together describe both the **what** (current effective scale) and the
**why** (auto vs. manual) — and that pair is what the dashboard pill
and the picker both read.

**Alternative considered:** store the override as a numeric multiplier
directly, with no string column. Rejected because the picker UI is
unit-named (operators think "this file is in inches", not "this file
needs ×25.4"), and round-tripping a float through the UI loses the
mental model. Keep the unit name; derive the multiplier.

### D3. The "set picker back to detector's pick" path clears the override

When the picker fires with the same unit that the detector would have
chosen on its own, the backend SHALL write `user_unit_override = NULL`
rather than store the redundant override. Two reasons:

1. Future detector improvements should keep applying automatically to
   files the operator never actually disagreed with.
2. The dashboard pill suffix `(user override)` should only ever
   indicate active operator intent.

To know "what the detector would have chosen," preprocess runs the
detector regardless of override (cheap), compares the two factors,
and writes `user_unit_override = NULL` when they agree.

### D4. Recompute is a background job with an explicit confirm step

Setting an override is destructive in the same sense that
auto-rescale is: it can change `applied_scale`, which invalidates
Match JSON for every product the file belongs to. The existing
"Auto-rescale invalidates saved Match JSON" requirement already
covers the cache-drop side. What we add on top is the **operator
contract**:

- `POST /api/files/{file_id}/unit-override` accepts the override and
  enqueues a re-preprocess job; returns `202` with the job id.
- Before the POST fires, the viewer SHALL show a confirm modal
  enumerating: (a) preprocess will re-run, (b) cached connectivity
  and pre-match will be rebuilt, (c) Match JSON for every product
  containing this file will be cleared and need re-running, (d) the
  override can be undone by picking the detector's choice again.
- The picker control is disabled while the recompute job is in
  flight, with the in-flight job id displayed for cross-session
  recovery (same UX pattern as rule-check).

**Alternative considered:** apply the override synchronously on the
POST request. Rejected: preprocess for a busy file (many primitives
+ side-region rebuild + thumbnail regen) is multi-second and would
block the request thread. We already have the background-job
machinery; reuse it.

### D5. INSUNITS-mismatch soft hint, never block

When the operator picks a unit that disagrees with the DXF's source
`INSUNITS` (e.g. file says inch, operator picks mm), the viewer
SHALL display an inline hint adjacent to the picker:

> `⚠ Differs from file declaration (inch)`

The hint is visual only — it does not change the confirm modal, the
POST payload, or the recompute job. The operator's judgement
overrides the DXF's self-declaration; the hint exists so they cannot
make this choice by accident.

**Alternative considered:** a second confirmation when overriding a
declared `INSUNITS`. Rejected: the operator already passes through
one confirm modal for the recompute cost. A second nag for a
declared-unit mismatch trains them to dismiss without reading.

### D6. Where the picker lives in the viewer

In the viewer header, adjacent to the existing `library-switcher`
dropdown. Reasons:

- The viewer has no persistent left/right column today; the layer
  list is a hidden floating panel toggled from the header. Putting
  the picker in the header keeps it always visible without
  introducing new layout.
- The `library-switcher` is the other file-level interpretation
  control already there (it selects which template library this
  file matches against). Co-locating unit-override with it matches
  the mental model of "things that change how this file is
  interpreted before matching."
- Does not steal canvas space.

Layout: a compact `<select>` labelled `Unit:` with the five options.
The detector-derived default is selected on load. A trailing badge
shows `set by you` when `user_unit_override IS NOT NULL`; an inline
hint shows the INSUNITS-mismatch warning when present.

## Risks / Trade-offs

- **[Risk] Operator picks the wrong unit and silently clobbers a
  correctly-detected file.**
  → Mitigation: confirm modal spells out the downstream invalidations;
  the dashboard pill suffix `(user override)` flags the file as
  operator-modified for any later reviewer.

- **[Risk] Cache invalidation cascades — a single override on a file
  shared by N products forces N match-rerun jobs.**
  → Mitigation: the existing invalidation requirement already handles
  this and the dashboard banner already exists ("Match JSON cleared
  after auto-rescale"). The confirm modal must surface the count of
  affected products before the operator commits.

- **[Risk] Background job fails mid-recompute, leaving
  `user_unit_override` set but `applied_scale` stale.**
  → Mitigation: the override row write and the recompute SHALL be a
  single transactional unit at the job level — on failure, the job
  is retried; the override row is never written until the recompute
  completes successfully. (Implementation note: write the override
  *as part of* the preprocess job, not before enqueuing it.)

- **[Trade-off] We surface five units in the picker, not the full
  AutoCAD `$INSUNITS` enumeration (≈20 entries).** The packaging
  workflow only ever sees these five. Adding more would dilute the
  picker; the operator can still hit an unrecognised-INSUNITS file
  with the detector + manual override path.

## Migration Plan

1. **Schema migration**: `ALTER TABLE files ADD COLUMN
   user_unit_override TEXT NULL`. Runs at startup like existing
   migrations; idempotent.
2. **No data backfill**: every existing row gets `NULL`, meaning
   detector authority — same behaviour as today.
3. **Rollback**: drop the column and the new endpoint. Files that
   were overridden retain their last-recomputed `applied_scale`
   (frozen at the override value) until they are re-preprocessed by
   any other mechanism. Acceptable, because the new value is still a
   valid `applied_scale` — downstream code never knew it came from
   an override.

## Open Questions

- **What is the icon / colour of the dashboard pill suffix?** Initial
  proposal: same neutral `ℹ` pill with `(user override)` appended in
  the same colour. Up for review during UI implementation.
- **Confirm modal copy: list each affected product by name, or just
  show a count?** For a file shared across many products the modal
  becomes a wall of text. Resolution: show count + first three
  product names + "and N more"; full list is in the modal's expandable
  detail.

## Context

`commitCurrentTemplate` in `app/static/canvas.js` is the viewer-side
handler that runs when the user presses ✓ in add-mode. It POSTs the
selected handles to `/api/files/{id}/commit`, receives back the new
template ID + class, and (today) clears the live-preview state and
re-renders. Scan All overlay state lives in two module-level vars:

- `scanAllByHandle: Map<handle, className> | null` — the per-handle
  class map painted by the overlay; `null` when Scan All is inactive.
- `scanAllSummary: { byClass: {name: count}, total } | null` — the
  per-class count strip the toolbar reads.

The live-preview state (S key) populates `matchSet: Set<handle>` via
`POST /api/files/{id}/match` with the user's current selection as the
template pattern. That endpoint returns every handle in the file that
matches the same pattern, with the same `(match_strategy, bbox_ratio)`
the upcoming Scan All would use (commit-time `add_template`'s
"hint" semantics).

Server-side Scan All (`/api/files/{id}/scan-all`, `app/main.py`)
iterates all 17 default classes' templates, runs the matcher per
class, applies `split_matches_by_side`, then runs `arbitrate(...)`
against `CLASS_ARBITRATION_GROUPS` to resolve cross-fire. Even with
the recent `add-circle-scan-fast-path` change this is ~7s for a
full-template library — way too slow to fire on every commit.

The recent `add-fiducial-square-class` and
`split-class-scope-library-vs-product` changes brought the default
class count to 17, with 8 product-scoped classes that are isolated
per product (Substrate, Lid family, DieArea, Ball family, Protrusion).
Scan All is the only place where committed templates surface in the
viewer's class colours; until the overlay refreshes, the user has no
visual confirmation their template is part of the library.

## Goals / Non-Goals

**Goals:**
- After a successful commit with Scan All active, the new template's
  matches SHALL appear in the overlay using their class colour,
  without a server round-trip in the common case.
- The post-commit overlay SHALL match what a full Scan All would
  have produced, *for classes outside arbitration groups*.
- For classes inside arbitration groups, fall back to the full
  re-run rather than producing an incorrect overlay.
- Keep the front-end / back-end constants in sync via the existing
  sentinel + drift-guard pattern already used for
  `CLASS_VIEW_CONSTRAINTS`.

**Non-Goals:**
- Refreshing the overlay on delete-template or move-template
  operations. They have the same need but require a different data
  flow (negative delta vs. positive delta) and are filed as
  follow-ups.
- Porting arbitration's neighbour-density math to JavaScript. The
  Python implementation lives in `app/class_arbitration.py` and
  involves pitch detection + MinNeighbors/MaxNeighbors rules per
  group; duplicating it on the front-end is a drift-risk magnet for
  a feature that only affects two classes today.
- Replacing the full Scan All re-run for arbitration-member commits
  with a partial server-side endpoint (e.g.,
  `/api/files/{id}/scan-arbitrate`). Worth considering if BGABall /
  FiducialCircle commit becomes the common path, but premature now.
- Reducing the hard-coded `CLASS_ARBITRATION_MEMBERS` JS constant to
  an HTTP fetch at load time. Current scale (2 entries, rarely
  changes) makes the sentinel + drift-guard pattern the lower-cost
  option.

## Decisions

### Reuse `selection ∪ matchSet` instead of calling `/api/match` after commit

The viewer's add-mode flow is:
1. Frame-select handles (cyan selection box) — populates `selection`
2. Press S → `runMatchPreview()` → `POST /api/files/{id}/match` with
   the selection — populates `matchSet` + `nearMissSet`
3. Press ✓ → `commitCurrentTemplate()` → `POST .../commit`

By the time ✓ runs, `selection ∪ matchSet` covers every handle the
just-committed template is responsible for in the current file. The
two halves split because the server-side `find_matches`
(`app/matching.py`) uses the seed pattern's handles as a skip-set
(`template_handle_set`) to keep the source pattern out of its
returned matches — that's correct for the live-preview count UX
("X matches besides what I selected") but means the seed pattern is
NOT in `matchSet`. The viewer renders both `selection` and
`matchSet` during the live preview (both show as cyan in the
existing code), so visually the seed is part of the preview, but
the data model splits them.

Post-commit, the union of the two is what the user expects to see
recoloured to the new class. Iterating just one of them would
leave the other invisible: if we only used `matchSet` the source
pattern (the user's just-selected handles) wouldn't get the class
colour; if we only used `selection` the rest of the matches
wouldn't.

Re-calling `/api/match` after commit would burn a redundant
round-trip for data the client already has.

**Alternatives considered:**
- *Re-fetch via `/api/match` after every commit.* Robust to the
  "user committed without pressing S" path (then `matchSet` is empty),
  but doubles the perceived latency for the common case and trades
  the no-server-round-trip benefit for a fallback that's already
  handled by the no-op branch ("matchSet empty → skip update").
- *Build a new endpoint that returns matches AND arbitration tags in
  one call.* Future option if arbitration commits become common;
  out of scope here.

### Fall back to full `runScanAll()` for arbitration-member classes

Today's `CLASS_ARBITRATION_GROUPS` has exactly one group: `{BGABall,
FiducialCircle}` — two classes whose templates are geometrically
identical (same-radius circles) and rely on neighbour-density
arbitration to be split correctly. A naive incremental merge would:

- For a new `BGABall` commit: tag every matched handle as `BGABall`,
  even isolated circles that arbitration would assign to
  `FiducialCircle` (MaxNeighbors(1)).
- For a new `FiducialCircle` commit: tag every matched handle as
  `FiducialCircle`, including dense grids that arbitration would
  assign to `BGABall` (MinNeighbors(2)).

Both produce overlays that misrepresent the post-arbitration state
the user will eventually see in Save Match's persisted JSON. Falling
back to the full Scan All re-run for these two classes (and any
future arbitration-group members) avoids the mis-attribution at the
cost of a ~7s wait — same as if the user had pressed Scan All
manually. The wait is bounded to a small subset of commits (two
classes out of 17), and arbitration-class commits already imply the
user wants the post-arbitration result.

**Alternatives considered:**
- *Build a partial scan + arbitration endpoint.* Reasonable
  longer-term option if these commits dominate. Adding a new endpoint
  for the v1 of this UX is overkill.
- *Skip the merge entirely for arbitration classes, status-line hint
  "press Scan All to refine".* Considered and offered to user;
  user picked the auto-fallback for one-click correctness.
- *Mirror arbitration math in JS.* Duplicate logic, drift risk,
  multi-week port. Hard no.

### Sentinel-wrapped JS mirror named `CLASS_ARBITRATION_MEMBERS`

The viewer needs to answer one question: "is this class an
arbitration member?" The Python `CLASS_ARBITRATION_GROUPS` registry
has additional fields (rules, default_class, pitch_multiplier,
min_population) that the viewer never uses. Mirroring the full
structure would over-couple front-end to server logic.

The chosen shape — `const CLASS_ARBITRATION_MEMBERS = ["BGABall",
"FiducialCircle"]` — is the flat union of every group's `members`
set. The drift guard at `tests/test_canvas_constants.py` builds the
same union from the Python source and asserts equality. The existing
`template-library` spec scenario "JS drift guard mirrors the Python
registry" already permits the JS literal to be a strict subset of
the Python fields, so this is within contract.

The sentinel name (`CLASS_ARBITRATION_MEMBERS_BEGIN/_END`) matches
the JS constant name rather than the Python registry name. The
template-library spec is updated to clarify that the sentinel name
SHALL match the JS literal's identifier (not the Python registry's),
since the JS may expose a derived view rather than a one-to-one
mirror.

**Alternatives considered:**
- *Mirror the full `CLASS_ARBITRATION_GROUPS` structure.* Pulls in
  fields the viewer doesn't use, increases drift surface area.
- *Fetch arbitration membership from a new
  `GET /api/arbitration-members` endpoint at viewer load.* Removes
  drift risk by definition but adds a network dependency for a
  ~2-entry list that changes at the same cadence as DEFAULT_CLASSES
  (rarely). The sentinel + drift-guard pattern already established
  for `CLASS_VIEW_CONSTRAINTS` is the lower-cost option.

### Override semantics: `scanAllByHandle.set(h, newClass)` wins

When merging `matchSet` into the existing overlay, a handle that was
previously tagged as class X (by a previous Scan All run) gets
overwritten to the new class. This matches the user's mental model:
"I just frame-selected these handles and labelled them as X, so X
they shall be." The same handle being reclassified by future Scan All
runs (which may, via arbitration or different per-class strategies,
prefer a different class) is acceptable — the user can always re-run
Scan All to reconcile.

This is intentionally NOT a "if class is currently set, leave it
alone" policy. That would leave the freshly-committed template
invisible in the overlay until Scan All, which is exactly the bug
this change fixes.

### No-op branch + minimal-data branch

One no-op path preserves the original behaviour:

- **Scan All not active** (`scanAllByHandle === null`): there's no
  overlay to update; just the class-count chip increment runs.

One minimal-data path produces a partial-but-useful overlay:

- **matchSet empty** (`matchSet.size === 0`): user committed without
  pressing S. We still merge `selection` (always non-empty —
  enforced by the commit handler's `!selection.size` early-return),
  so at least the source pattern lights up in the new class colour.
  Other instances of the new template stay invisible until the user
  presses Scan All. This is strictly better than the original
  "do nothing" path: the user always sees *some* visual confirmation
  of their commit.

The remaining decision point (arbitration member) is a fall-through
to `runScanAll()` — not a no-op, but it produces a correct overlay
asynchronously after the same status line.

## Risks / Trade-offs

- **Risk**: A handle that was correctly classified by a prior Scan All
  (with arbitration applied) gets overwritten by the incremental
  merge. → **Mitigation**: bounded to non-arbitration classes — the
  fall-back to full Scan All in arbitration cases prevents the
  worst-case mis-attribution. The override semantics for non-
  arbitration classes are intentional and match user intent.
- **Risk**: `matchSet` and the committed template can drift if the
  per-class strategy on the new class differs from what
  `runMatchPreview` used. → **Mitigation**: today the `class_name`
  hint plumbed into `/api/match` makes the preview use the same
  `(match_strategy, bbox_ratio)` the upcoming commit will. If this
  invariant is ever broken, the incremental merge becomes
  inconsistent with full Scan All; the post-commit `applyViewConstraintsToScanAll`
  still runs but can't compensate for strategy drift.
- **Risk**: Future commits of new arbitration-member classes
  (BGABall / FiducialCircle becoming a common path) inflict the
  ~7s wait. → **Mitigation**: out of scope for v1; documented as a
  follow-up option (partial server endpoint).
- **Trade-off**: Adding a JS-side constant introduces a small drift
  surface (`CLASS_ARBITRATION_MEMBERS`). → **Mitigation**: the same
  sentinel-comment + parsing test pattern that already keeps
  `CLASS_VIEW_CONSTRAINTS` in sync is reused verbatim; the new test
  adds one entry to the existing drift-guard suite.
- **Trade-off**: status-line gains a `· overlay +N <Class>` suffix
  that wasn't there before. → **Mitigation**: only appears on
  incremental merge (not on the no-op or fall-back paths) so the
  signal-to-noise ratio is preserved.

## Migration Plan

No migration required. Pure viewer-side change; no persisted state
touched; the previous behaviour (no overlay update) is identical to
the new no-op branches. Existing files / libraries / templates work
exactly as before.

## Open Questions

- *Should we also surface a small inline indicator in the toolbar
  chip ("just updated") for the first few seconds after commit?*
  Punted — the status line already announces the merge.
- *If a future arbitration group involves a non-circle class (e.g.,
  if SMD-2T variants start cross-firing), do we keep the full Scan
  All fallback?* The current decision is yes — the drift-guard test
  will surface the new member, the fallback kicks in automatically.
  Revisit if multiple new groups land in a single release.

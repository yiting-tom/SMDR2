## Context

Preprocessing today is a one-way function: `flatten_for_render` parses
the DXF, walks every entity through ezdxf's render path into a flat
list of primitives, computes a bbox, reads INSUNITS, and returns a
`RenderOutput` (`app/dxf.py:332`). Everything downstream — match-time
`EntityShape` construction, rule-check distances, viewer rendering,
layer thumbnails — consumes those primitives at face value in "drawing
units." The only acknowledgement that drawing units may not be mm is
the post-hoc `compute_unit_scale_warning` heuristic in
`app/files.py:150`, which paints a badge but mutates nothing.

The matcher's absolute tolerances (`TOLERANCE_ABS = 0.01`,
`SCALE_MIN/MAX = 0.9999/1.0001`) and the rule-check authoring
convention ("distance is in mm") both assume that drawing units **are**
mm. When a DXF is exported with `$INSUNITS = 0` and a 50-m bbox (the
classic "1000× scale" symptom), every one of those assumptions silently
breaks — and the user has no actionable lever besides "go fix it in
CAD and re-upload."

Doing the rescale once, at the pipeline boundary, lets every consumer
keep its existing absolute-unit semantics with no further changes.

## Goals / Non-Goals

**Goals:**
- When a DXF lives in the wrong units — either an authoritative
  declaration that does not match our mm consumers (inch, cm, m) or
  a unitless export whose bbox is grossly outside the expected
  packaging range — multiply every flattened coordinate by a factor
  `M` that brings the geometry into mm before persisting. Bbox,
  primitives, EntityShape points, layer-thumbnail SVGs all reflect
  mm output.
- Record the applied factor on the file row (as a float, not a
  single hardcoded constant) so the dashboard can state what
  happened ("auto-rescaled ×0.001", "auto-rescaled ×25.4 (inch →
  mm)") instead of leaving the user staring at a warning they
  cannot act on.
- Invalidate stale Match JSON for any file whose factor changes,
  since the saved per-handle point sets reference pre-rescale
  coordinates. The file returns to `ready_to_match`.
- Migrate already-uploaded files that match the new auto-rescale
  trigger on startup so the user doesn't have to manually re-upload.

**Non-Goals:**
- Rescaling files whose declared INSUNITS is already mm (`INSUNITS = 4`)
  regardless of bbox magnitude. A 1.2 m mm-declared panel is a
  legitimate design, not a unit bug.
- Marginal-factor inference. The unitless path only fires when the
  chosen factor differs from `1.0` by ≥ one order of magnitude (`M
  ≤ 0.1` or `M ≥ 10`); anything in `(0.1, 10)` keeps today's
  "informational badge only" behaviour.
- Re-running matchers automatically after re-preprocess. Match JSON
  invalidation drops the user back into the existing
  `ready_to_match` flow; they re-run match themselves.

## Decisions

### `applied_scale` semantics

`applied_scale` is a **multiplier**: `rescaled_coord = original_coord
* applied_scale`. So a 1000× too-big DXF gets `applied_scale = 0.001`
(divide-by-1000 effect), and an inch-declared DXF gets `applied_scale
= 25.4` (multiply-by-25.4 effect). `applied_scale = 1.0` means "no
rescale." Storing the multiplier (rather than the divisor) keeps the
math uniform across "too big" and "too small" cases.

### `detect_scale_factor`

A pure function on `(insunits, bbox_diagonal)`:

```
detect_scale_factor(insunits: int | None, bbox_diagonal: float) -> float
```

Behaviour (first match wins):

| Case | Condition | Factor |
|---|---|---|
| Declared inch | `insunits == 1` | `25.4` |
| Declared cm   | `insunits == 5` | `10` |
| Declared m    | `insunits == 6` | `1000` |
| Declared mm   | `insunits == 4` | `1.0` (always trust mm declaration) |
| Unitless / unknown | `insunits ∈ {0, None}` | best power-of-10 factor — see below |
| Otherwise     | unrecognised INSUNITS | `1.0` |

For the unitless path:

- Expected packaging diagonal range: `EXPECTED_RANGE_MM = (10.0,
  5000.0)`.
- Candidate factors: `[10**k for k in -4..+4]`.
- Pick the factor whose `bbox_diagonal * factor` falls inside
  `EXPECTED_RANGE_MM`. If multiple factors qualify, prefer the one
  with the smallest `|log10(factor)|` (closest to `1.0`).
- **Safety guard**: only return a non-`1.0` factor when
  `|log10(factor)| ≥ 1` (i.e., `factor ≤ 0.1` or `factor ≥ 10`).
  Marginal cases stay at `1.0` and keep the existing
  "informational badge" UX so a human decides.
- If no candidate brings the bbox into range, return `1.0`.

**Alternatives considered:**
- Always rescale anything tagged `suspect_scale` (single 1000×
  case). Rejected — leaves declared-inch / declared-cm / declared-m
  on the table, and leaves 10×-too-big unitless files as second-
  class citizens.
- Continuous factor inference (e.g. `factor = TARGET / diagonal`).
  Rejected — non-power-of-10 factors are almost always wrong in
  packaging CAD; they smear an actual modelling error into "looks
  about right" output that obscures the underlying bug.
- Drop the safety guard. Rejected — a legitimate 5×5 mm dice
  (diagonal ≈ 7 mm) is just outside the expected range, and we
  don't want a borderline auto-rescale that the user has no
  obvious way to undo.

### Where the rescale lives

Inside `app/dxf.py`, applied as a post-step on `RenderOutput` before
return. A new helper `_maybe_rescale(render: RenderOutput) ->
tuple[RenderOutput, float]` calls `detect_scale_factor`, applies the
multiplier to every primitive coordinate and to the bbox when the
factor is not `1.0`, and returns the (possibly rewritten) render
plus the applied factor. `flatten_for_render` calls it and exposes
the factor as a new field on `RenderOutput`.

This keeps **all** downstream consumers — preprocessing storage,
EntityShape construction in `app/matching.py`, layer-thumbnail SVG
rendering in `app/dxf.py:render_layer_svg`, rule-check distance
extraction — completely unaware that any rescale happened. They see
mm.

**Alternatives considered:**
- Rescale at consumer time (e.g. `EntityShape.from_points` divides
  by the file's stored factor). Rejected: would force every
  consumer to know about the factor and is the exact "absolute-unit
  assumption breaks silently" problem we're solving.
- Rescale via DXF-level transformation (insert a top-level scale
  block, re-write the file). Rejected: too invasive, breaks layer
  thumbnails that already cache, and the original DXF on disk is
  the user's source of truth — we should not touch it.

### Persistence

`files` table gains one column:

- `applied_scale REAL NOT NULL DEFAULT 1.0` — the factor applied
  during the most recent preprocess. `1.0` means "no rescale."

The migration adds the column with a default; legacy rows read
`1.0` until they are re-preprocessed.

`File.to_dict` extends the unit-warning payload with the persisted
factor:

- `unit_scale_warning` — unchanged semantics (`None` / `"unitless"`
  / `"suspect_scale"`)
- `unit_scale_warning_detail` — text reworded when `applied_scale
  != 1.0`. Examples:
  - INSUNITS=0, pre-rescale diagonal=42000 → `"INSUNITS=0, pre-rescale diagonal=42000 → auto-rescaled ×0.001 (mm)"`
  - INSUNITS=1 (inch), diagonal=10 → `"INSUNITS=1 (inch) → auto-rescaled ×25.4 (mm)"`
- `applied_scale` — numeric, `1.0` or the applied multiplier.

Dashboard JS rendering (`app/static/dashboard.js`):

- `applied_scale != 1.0` → render a neutral `ℹ rescaled <human>` pill,
  where `<human>` is `"÷1000"` for `M = 0.001`, `"×25.4 (inch)"` for
  `M = 25.4`, etc. The pill `title` carries the full detail text.
- Else if `unit_scale_warning` is non-null → render the existing
  yellow `⚠ unit` badge.
- Else render nothing.

### Match JSON invalidation

When `_maybe_rescale` returns a non-`1.0` factor AND the file row's
prior `applied_scale` differs, the preprocess step:

1. Deletes `data/match/<file_id>.json` if present.
2. Clears the `match_saved` flag on the file row.
3. Sets file status back to `ready_to_match`.
4. Records a one-shot toast/banner ID on the product card so the
   user sees "Match JSON cleared after auto-rescale — re-run match"
   on the next dashboard tick.

This is *exactly* the existing flow that fires when a file is
re-uploaded; we are reusing it.

**Alternative considered:** rescale the saved match JSON in place
(divide every stored centroid / point by the factor). Rejected — the
match JSON also encodes derived signature data (path lengths, radii)
that would need parallel rescaling, and any future strategy that
re-derives from EntityShape would diverge. Cleanest invariant is
"match JSON is always in the same units as the current preprocess
output." When the factor changes, invalidate.

### Migration for already-uploaded files

On app startup, a one-shot pass scans the `files` table for rows
where:

- `applied_scale == 1.0` (never auto-rescaled), AND
- `detect_scale_factor(insunits, bbox_diagonal)` would return a
  non-`1.0` factor under the new detector.

For each hit it submits a re-preprocess job through the existing
"Re-preprocess all files" job machinery (the same code path the
dev-params modal uses). This runs in the background, surfaces
progress via the existing `_jobs` reporting, and converges every
legacy file the detector now claims without user action.

Files that already have a `match_saved` artefact go through the
invalidation in the previous decision, dropping them back to
`ready_to_match`.

## Risks / Trade-offs

- **[Risk] False positive — a legitimate "geographic" CAD file with
  bbox in tens-of-metres that was *intentionally* unitless gets
  rescaled.** → Mitigation: only the unitless path is heuristic;
  it uses powers of 10 inside a packaging-shaped expected range
  (10–5000 mm) AND a one-order-of-magnitude safety guard so any
  marginal case stays at `M = 1.0`. Anyone who declares mm keeps
  their geometry untouched regardless of bbox. Users can inspect
  `applied_scale` on the file detail. If false positives still
  surface in practice, we add an opt-out per file (deferred until
  needed).
- **[Risk] Declared-inch / cm / m DXFs that *also* happen to have
  the "wrong" coordinates inside them (e.g. someone mislabelled the
  header) get the wrong factor.** → Mitigation: the declaration is
  authoritative by design. The dashboard pill spells out what we
  did so the user can spot the mismatch (e.g. an inch-declared
  file with a 0.1-unit bbox would get rescaled ×25.4 → 2.54 mm,
  which the user sees and can question). If this is wrong in
  practice we add a "force factor" override per file later.
- **[Risk] Saved Match JSONs disappear silently on first startup
  after deploy.** → Mitigation: the migration logs which file IDs
  were rescaled + invalidated; the dashboard banner spells out the
  reason per affected product card on next refresh. We do not
  silently re-run match.
- **[Trade-off] `flatten_for_render` is no longer a pure parse —
  it now mutates geometry conditional on heuristics.** → Mitigation:
  the rescale logic is one small helper with a public boolean and
  factor in its return value, so tests can exercise the heuristic
  independently. Existing tests for `flatten_for_render` keep using
  files that don't trigger the heuristic.
- **[Trade-off] Storing `applied_scale` per file ties our schema to
  one specific normalisation strategy.** → Acceptable — the column
  is a `REAL` and trivially generalises to future factors
  (`25.4` for inch, future smarter heuristics) without further
  migration.
- **[Risk] The viewer overlays raw DXF handles onto rescaled
  coordinates; if the user opens the file in AutoCAD side-by-side
  to verify, the coordinates differ by 1000×.** → Acceptable —
  surfacing `applied_scale` on the dashboard tells the user this is
  happening, and our internal "mm" framing is the source of truth
  for the matching/rule-check workflow.

## Migration Plan

1. **Code lands**: schema migration adds `applied_scale` column with
   default `1.0`. All existing rows read as `1.0`.
2. **First startup after deploy**: the one-shot scan submits
   re-preprocess jobs for every qualifying legacy file. Dashboard
   tick shows them transitioning `ready_to_match → preprocessing →
   ready_to_match` over a few seconds each.
3. **For files that had Match JSON saved**: the re-preprocess
   invalidates the match JSON; the affected product card shows the
   "re-run match" banner. The user re-runs match manually.
4. **Rollback**: drop the `applied_scale` column (or leave it,
   harmless). Revert `_maybe_rescale` to a no-op. Files that were
   already rescaled stay rescaled in their cached preprocess
   artefacts; re-preprocessing them with the old code will undo
   the rescale (because the DXF on disk is untouched).

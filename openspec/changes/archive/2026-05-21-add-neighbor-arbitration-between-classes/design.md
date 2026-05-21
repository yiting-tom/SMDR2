## Context

Pattern matching in SMDR2 is purely geometric. Two classes with
identical templates (e.g., `FiducialCircle` and `BGABall` when the
fiducial diameter equals the BGA ball diameter) produce the **same
handles** in both classes' match results — `find_matches_from_pointsets`
has no concept of class, and templates with identical entity-point sets
and signature parameters fire on the same candidates.

Today this leak surfaces as double-counting:

- `out["bottom_view.bga_ball.0"]` lists 100 handles (96 actual BGA balls
  + 4 fiducials misread as BGA).
- `out["top_view.fiducial_mark.0"]` lists the same 4 handles plus, if
  the fiducial is also visible in bottom_view, possibly more.
- Downstream rule checks (`app/rule_check.py`) inflate the BGA count by
  4 and produce contradictory per-class instance reports.

Two facts give us a clean disambiguator:

1. BGA balls are a regular grid → every BGA ball has ≥ 2 neighbours at
   the same pitch.
2. Fiducials are isolated (≤ 4 per substrate, far apart) → 0 neighbours
   at BGA pitch.

We do not have user-provided pitch information per file, but the BGA
population dominates the candidate count, so the median nearest-neighbour
distance over the **pooled** candidates equals the BGA pitch.

Existing patterns we will mirror:

- `library.CLASS_VIEW_CONSTRAINTS` (app/library.py): per-class registry
  + helper + JS drift-mirror + frozenset-typed defaults. We will follow
  the same shape.
- `split_matches_by_side` (app/side_regions.py:80): per-class filter step
  applied between matching and JSON write. Our arbitration plugs into
  the same hand-off point in `save_match_json`.

## Goals / Non-Goals

**Goals:**

- Eliminate double-counting between class pairs whose templates are
  geometrically indistinguishable, deterministically, without any
  user-configured pitch value.
- Make the rule data-driven and per-arbitration-group so future
  same-size collisions (e.g., two SMD pad sizes that coincidentally
  equal a ball diameter) can be added without code changes.
- Preserve byte-identical Match JSON output on inputs where no
  arbitration group's members overlap.
- Surface counts in the `save_match_json` response so tests and the UI
  can verify behaviour at a glance.

**Non-Goals:**

- Disambiguating *within* a single class (e.g., separating SMD-pad
  variants of different sizes). That is signature-gated upstream.
- Replacing or modifying `CLASS_VIEW_CONSTRAINTS`. View filtering is
  orthogonal and continues to run as today.
- Per-file or per-template-overrides of `pitch_multiplier` /
  `min_population`. We deliberately keep tuning to module-level
  defaults until field data says otherwise.
- Heuristics beyond neighbour count (e.g., DXF layer name, position in
  substrate). Those are good cheap signals but project-specific; we
  scope this change to a generic, geometry-only rule.

## Decisions

### Decision 1: Where the arbitration step runs

**Choice:** inside `save_match_json` in `app/main.py`, after the
per-class matching loop and the view-split done by
`split_matches_by_side`, before `json.dump`.

**Rationale:** at this point we already have:

- All matches grouped by `<view>.<class>.<idx>` keys.
- The full `shapes` map (centroids accessible via
  `shapes[handle].centroid`).
- All view-constraint filtering applied.

Running here means arbitration never sees instances that were never
view-allowed in the first place; that keeps the pool clean.

**Alternative considered:** push arbitration into the matching layer
itself (`find_matches_from_pointsets` returns class-tagged matches).
Rejected — it would conflate two responsibilities (find candidates vs.
break ties) and force every matching path to learn about the registry.

### Decision 2: Auto-derive pitch from median NN distance

**Choice:** for each arbitration group, pool all member-class instance
centroids and take `np.median(nearest_neighbour_distance(pool))` as the
pitch. Use `scipy.spatial.cKDTree.query(pool, k=2)[0][:, 1]` for the NN
distances (k=1 returns the point itself).

**Rationale:** BGA dominates the population by 1–2 orders of magnitude.
Median is bias-free against a small fraction of outliers (corner
fiducials) and needs no parameters. Computation is `O(N log N)`.

**Alternative considered:**

- Compute pitch from the BGA template's internal spacing. Rejected
  — single-ball templates carry no intra-pattern distance, and even
  multi-ball templates would not adapt to per-file scale (which is
  already auto-normalised but not always to 1:1).
- Ask the user. Rejected — defeats the "fix the bug, don't redesign
  the UX" scope, and the user has historically pushed back on adding
  parameters that the tool can infer.

### Decision 3: Neighbour-rule schema

**Choice:** sum-type rule with two variants — `MinNeighbors(n)` and
`MaxNeighbors(n)` — declared per member class inside an
`ArbitrationGroup` dataclass. Each instance is classified by the first
rule it satisfies in registry-declared order.

**Rationale:** keeps the data model trivially serialisable (JSON-able if
needed later), avoids a DSL, and is enough to express "ball ≥ 2,
fiducial ≤ 1" without resorting to ranges.

**Alternative considered:** a single `NeighborRange(min, max)` per
class. Rejected — semantically the BGA ball rule is "at least", not
"between" (a centre ball with 4 neighbours and a corner ball with 2
both qualify). Forcing an explicit max would invite tuning fragility.

### Decision 4: `pitch_multiplier = 1.5` default, group-overridable

**Choice:** the search radius for neighbour counting is
`pitch_multiplier × derived_pitch`. Default `1.5`; overridable in the
`ArbitrationGroup` definition.

**Rationale:** a strict `1.0×` would miss diagonal neighbours in a
square grid (`√2 ≈ 1.414×`). `1.5×` catches all 8 immediate neighbours
of an interior cell and the 3 of a corner cell, but excludes the next
ring (`2.0×` and beyond), so a sparse pattern doesn't accidentally
appear dense. Empirically robust across the test grids we have today.

**Alternative considered:** `pitch_multiplier = √2 + ε`. Rejected as
needlessly fragile under pitch jitter (real DXFs have minor centroid
drift); `1.5` gives a comfortable safety margin without admitting
second-ring neighbours.

### Decision 5: Population fallback (`min_population`)

**Choice:** if any **non-default** member class would receive <
`min_population` instances after classification, reassign the entire
pool to `default_class` (configured per group; `"FiducialCircle"` for
the BGA/Fiducial group). Default `min_population = 8`. The default
class itself has no floor — realistic substrates may have only 4
fiducials, and that must not trigger fallback.

**Rationale:** the dominant failure mode of median-NN auto-pitch is
"the file genuinely has no BGA grid, only 4 fiducials": the four
fiducials' pairwise distances become the median pitch, and they
classify as BGA because each has 2–3 neighbours at that pitch. With
`min_population = 8` applied to the non-default class (BGABall), a BGA
assignment count of 4 is impossible — it collapses to fiducials, which
is the safe direction (under-claiming BGA is better than ghost-counting
fiducials).

**Why 8?** smallest realistic BGA package this tool sees is roughly a
3×3 (9 balls). 8 is the round-down conservative floor; can be tuned per
group if a customer ships smaller BGA arrays.

**Alternative considered:** require a user-provided BGA-vs-fiducial
hint per file. Rejected — adds UX surface and contradicts the
auto-detect ethos.

### Decision 6: Reassignment respects view constraints, drops on conflict

**Choice:** when arbitration moves an instance from class A to class B,
the instance keeps its original view prefix. We then re-check
`is_allowed_view(B, view)`; if false, the instance is dropped (not
re-emitted under any key) and counted in `dropped_by_view`.

**Rationale:** view assignment is a property of *which rect the
centroid falls in*, which is invariant under class reassignment. If the
new class disallows that view, the instance is genuinely an artefact
(e.g., a real-world impossible "BGA in top view"), so dropping is
correct. Surfacing the count lets us catch registry-misconfiguration
loudly in tests.

**Alternative considered:** ignore view constraints during arbitration
re-check, trusting that the original class's view filter has done its
job. Rejected — once a handle's class label changes, the original
filter's invariant no longer holds; we must re-validate.

### Decision 7: Deterministic ordering

**Choice:** before pitch derivation and classification, sort the pooled
centroids by `(view_prefix, original_class_display_id,
original_instance_index)`. Use a stable sort; ties at the search-radius
boundary are broken by this same order.

**Rationale:** the matcher already emits matches in a deterministic
order; the arbitration step must not introduce non-determinism via
hash-iteration or set ordering. Byte-identical Match JSON across re-runs
is a property the test suite already relies on
(`test_match_json_invalidated_when_applied_scale_changes`).

## Risks / Trade-offs

- **Risk:** BGA pitch varies inside a single DXF (e.g., interposer
  with two BGA regions at different pitches). → **Mitigation:**
  median is still meaningful per population if one pitch dominates;
  for genuinely bimodal cases we'd need per-region grouping. Out of
  scope for v1 — flag in tests if it appears.
- **Risk:** `min_population = 8` excludes very small BGA packages
  (e.g., a 2×2 demo board). → **Mitigation:** the group's
  `min_population` is data-driven; add a per-customer override if it
  ever blocks shipping.
- **Risk:** registry typos (member listed in two groups, missing rule)
  silently misclassify. → **Mitigation:** validation at import time
  raises `ValueError`; covered by tests.
- **Risk:** the JS mirror drifts. → **Mitigation:** if any UI surface
  exposes the registry, gate it with the same sentinel-comment
  drift-guard used for `CLASS_VIEW_CONSTRAINTS`
  (`tests/test_canvas_constants.py`). For v1 we don't expect a UI
  surface — the registry is server-only.
- **Trade-off:** running arbitration on every `save_match_json` adds
  `O(N log N)` work (`N` ≈ pool size, typically `< 10000`). At project
  scale this is negligible (sub-millisecond), but we will not memoise
  unless profiling shows otherwise.

## Migration Plan

- **Forward compatibility:** purely additive. Both arbitration-group
  members (`BGABall`, `FiducialCircle`) are already in
  `DEFAULT_CLASSES`, so existing libraries already carry both classes
  via the existing seed-on-boot pass. No DB migration is needed.
- **Existing Match JSON files on disk:** unaffected. The new
  arbitration only runs at write time. Old files are read as-is; if
  the user re-runs `save_match_json`, the new shape kicks in and the
  file is rewritten in place. The `match_saved` flag stays correctly
  scoped to the file's most recent serialisation.
- **Rollback:** revert the change. No schema changes, no DB writes
  beyond the seeded class row that is already idempotent under the
  existing seeding logic.

## Open Questions

- Should `FiducialCircle` get a view constraint of its own
  (`{"top_view"}` is the IC-packaging norm)? The proposal leaves
  `FiducialCircle` unconstrained for v1 to avoid co-changing
  `CLASS_VIEW_CONSTRAINTS`; revisit once we have a sample DXF where
  fiducials genuinely appear in bottom_view (rare).
- Do we want to expose `arbitration_counts` in the UI's Save-Match
  result toast, or keep it API-only for now? Defer to the user; the
  spec only mandates the response field exists.

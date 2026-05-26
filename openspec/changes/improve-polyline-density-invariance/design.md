## Decision: Lazy resampling in the matching pipeline, not in EntityShape

The simplest place to put resampled clouds would be on `EntityShape`
itself — compute once at `build_entity_shapes` time, store alongside
the original points. That guarantees every consumer sees uniform
density.

We reject this for two reasons:

1. **Memory.** A 64-point cloud at float64 (x, y) is ~1 KB per
   `EntityShape`. A 400 k-entity BGA file allocates ~400 MB on top of
   the existing shapes dict, just for resampled mirrors that may
   never be needed.

2. **Wrong abstraction.** `EntityShape.points` is the file's actual
   geometry — it's used for picking, hit-test, OSNAP, rendering, and
   selection. Storing a "matcher-friendly" parallel cloud on the same
   dataclass invites future bugs where consumers grab the wrong field.

Instead the resampling lives **inside the matching pipeline** and
operates on the cloud lazily, only when a candidate has passed the
cheap pre-filter (path length) and is about to enter PCA / scale /
Chamfer. Template-side resampling happens once outside the loop.

## Decision: RESAMPLE_N = 64

Trade-off: too low loses fine shape detail; too high wastes Chamfer
cost. `collect_entity_points` already synthesizes circles at up to
64 points (capped from `2πr / 0.01`); reusing 64 keeps the matcher's
internal density consistent with the synthesized circles flowing in
from the library Templates.

For closed polygons with a small number of unique vertices (e.g., a
4-vertex rectangle), 64 arclength-spaced samples land on the edges:
PCA principal axes are unchanged (rectangle is its own
mirror-symmetric centroid) and Chamfer between the resampled
rectangle and any other equally-sized rectangle is essentially zero.

For open polylines (e.g., a single line segment, 2 vertices), 64
samples collinear along the segment: PCA reports one axis along the
line and one degenerate; Chamfer compares two line-shaped clouds —
works identically to today's path.

## Decision: Drop the vertex-count signature gate

With density-invariant Chamfer, `vertex_count_ratio` is no longer
discriminative for valid same-shape candidates. Keeping it would
*lose* real matches (the exact bug we're fixing) without compensating
benefit.

`path_length_ratio` stays. It's a genuine size invariant — two shapes
with very different perimeters cannot be matches under our `[0.95,
1.05]` scale band regardless of vertex count.

A `vertex_count < 2 → reject` guard remains, but only as a sanity
check against truly empty clouds; the previous `vertex_count == 0`
guard was equivalent.

## Decision: Apply to multi-entity matching too

`_match_multi` uses `align_score` for per-entity verification of a
hypothesised pose. The same density-bias bug exists there — a
multi-entity template containing a substrate outline would fail to
verify against a mirrored copy with different vertex count. The fix
is one-line (resample inputs at the start of `align_score`) so we
apply it consistently rather than carrying a per-path tuning knob.

## Non-goals

- No change to `_match_single_circle` (the fast path). Circles are
  already density-invariant by construction (only radius matters), so
  resampling adds cost without benefit.
- No change to persisted `Template.entity_point_sets`. Templates keep
  their original vertices; resampling happens at match time.
- No change to `EntityShape.points`, `centroid`, `radius`, or
  `path_length` — those are consumed by picking, rendering, and the
  signature filter respectively, and rely on file-original geometry.
- No exposure of `RESAMPLE_N` through the API or the spec. It's an
  internal performance knob; the spec only guarantees the *outcome*
  (density-invariant matching), not the mechanism.

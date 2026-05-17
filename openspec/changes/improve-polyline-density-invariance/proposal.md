## Why

Single-entity polyline matching today rejects genuine same-shape pairs
when the two entities are stored with very different vertex counts. The
canonical failure mode in IC packaging DXFs is the **mirrored
substrate outline**: one side authored as an 11-vertex polyline (arc
segments left as bulges, lightly flattened), the other authored or
imported as a 65-vertex polyline (heavily flattened to straight
segments). Same physical shape, same perimeter (~24 mm), but a
6× vertex-count difference.

Three downstream things fail together when this happens:

1. **Signature filter rejects.** `signatures_compatible` requires
   `vertex_count_ratio ∈ [0.75, 1.25]`. 11/65 = 0.169 — rejected
   before the matcher even attempts alignment.

2. **Even if signature filter passes**, `align_score`'s scale gate
   relies on `t_norm / c_norm`, where each `*_norm` is the mean
   distance from each cloud's centroid. Two clouds with the same
   shape but radically different point densities along that shape
   have meaningfully different `*_norm` values, so the scale can
   drift outside `[0.95, 1.05]` and produce a `NearMiss(reason="scale")`
   that should have been a match.

3. **Even if scale gate passes**, Chamfer distance is biased by point
   density. With 11 vs 65 points on a 24 mm perimeter, the sparser
   cloud's typical inter-vertex spacing is ~2.2 mm; chamfer distance
   from each sparse vertex to its nearest dense vertex is bounded by
   the dense cloud's spacing (~0.4 mm), so the symmetric mean is
   roughly `0.5 × (0.4 + 2.2)/2 ≈ 0.65 mm` — well above the 0.05 mm
   tolerance.

These three failures compound; loosening only the signature filter
(the only obvious knob) doesn't help, because the deeper PCA / scale /
Chamfer pipeline is fundamentally density-sensitive.

## What Changes

- Add a new module-level helper `_resample_arclength(points, n)` in
  `app/matching.py` that returns `n` points evenly spaced by cumulative
  arclength along the polyline. Handles both open and closed inputs
  (closed inputs identified by `first ≈ last`); for degenerate inputs
  (< 2 distinct points or zero total length) the original behaviour
  (no match) is preserved.
- Single-entity matching SHALL resample both the template cloud and
  each candidate cloud to a fixed `RESAMPLE_N = 64` points before
  computing centroid, PCA axes, scale, and Chamfer. The original
  `EntityShape.points` SHALL stay untouched — picking, hit-test,
  rendering, and other consumers continue to see the file's actual
  vertices.
- `signatures_compatible` SHALL drop the vertex-count gate. The
  path-length gate (±20%) stays. A degeneracy guard
  (`vertex_count < 2 → reject`) replaces the old non-zero gate.
- Multi-entity matching SHALL share the same resample helper for its
  per-entity verification (`align_score`), so substrate outlines that
  appear as part of a larger pattern also benefit.
- New tunable `RESAMPLE_N = 64`, matching the existing
  `collect_entity_points` circle-sampling density for consistency.

No persisted-data change. No API change. No frontend change.

## Capabilities

### Modified Capabilities
- `pattern-matching`: matching SHALL be invariant to per-entity
  vertex-count differences as long as both entities lie on the same
  curve (after translate / rotate / mirror / scale ∈ [0.95, 1.05]).

## Impact

- **Backend (`app/matching.py`)**: new `_resample_arclength` helper,
  `_match_single_serial` resamples template + candidate, `align_score`
  resamples both inputs, `signatures_compatible` drops vc gate.
- **Tests (`tests/test_matching.py`,
  `tests/test_matching_circle_fast_path.py`)**: existing scenarios
  remain green (resampling on similar-vc clouds is approximately a
  no-op). New tests cover the 11-vs-65-vertex substrate-mirror case
  end-to-end and the bare-minimum line-segment case.
- **No frontend touched.** Selection / hit-test continue to operate
  on `EntityShape.points` (the file's actual vertices) so the visible
  geometry is unchanged.
- **Perf:** resampling is `O(N + RESAMPLE_N)` per cloud (one
  cumulative-arclength pass + one `searchsorted`). Template is
  resampled once outside the loop; each candidate gets one resample
  before PCA/Chamfer. The vertex-count gate is dropped, so slightly
  more candidates reach the Chamfer phase — bounded by the path-length
  gate, which is the dominant filter for unrelated shapes anyway. On
  the 24k-entity diagnostic file, walltime stays comparable.

## ADDED Requirements

### Requirement: Transform-invariant matching

The matcher SHALL find candidates that match a template under any
combination of translation, rotation (any angle), mirroring (any axis),
and isotropic scaling within the closed interval [0.95, 1.05]. The
match acceptance threshold SHALL be a chamfer distance ε (default
0.05 in drawing units).

#### Scenario: Translated copy matches
- **WHEN** the candidate is the template translated by a non-zero vector
- **THEN** `align_score` returns a chamfer distance below 1e-6

#### Scenario: Rotated copy matches at arbitrary angles
- **WHEN** the candidate is the template rotated by 30°, 90°, 137°, or 270°
- **THEN** `align_score` returns a chamfer distance below 1e-3

#### Scenario: Mirrored copy matches
- **WHEN** the candidate is the template mirrored across the y-axis
- **THEN** `align_score` returns a chamfer distance below 1e-3

#### Scenario: Within-tolerance scale matches
- **WHEN** the candidate is the template scaled by a factor in (0.95, 1.05)
- **THEN** `align_score` returns a non-None result with chamfer below 1e-2
- **AND** the reported scale lies inside [0.95, 1.05]

#### Scenario: Out-of-tolerance scale is rejected
- **WHEN** the candidate is the template scaled by 1.5
- **THEN** `align_score` returns None (caller treats as a near-miss)


### Requirement: Single-entity template matching

When the template is exactly one entity, the matcher SHALL scan every
candidate entity in the drawing, pre-filter by `signatures_compatible`
(vertex count within ±25%, path length within ±20%), and verify
remaining candidates with `align_score`. Template-side state SHALL be
computed once outside the loop.

#### Scenario: Find translated copies of a single entity
- **WHEN** the template is one rectangle and the drawing contains 3 translated copies plus the template itself
- **THEN** `find_matches([template_handle], drawing)` returns exactly the 3 copies
- **AND** the template's own handle does not appear in the results

#### Scenario: Reject a different shape with similar size
- **WHEN** a candidate has the same vertex count but different aspect ratio
- **THEN** it does not appear in matches
- **AND** it MAY appear in near-misses with `reason: "shape"`


### Requirement: Multi-entity template matching (pose-based)

When the template consists of N≥2 entities, the matcher SHALL pick the
rarest template entity as a seed, encode every other template entity's
centroid in the seed's PCA-local frame, and for each candidate seed
predict where the other template entities should be in world
coordinates. A match SHALL be reported only when every predicted
position has a shape-compatible drawing entity within a small position
tolerance.

#### Scenario: Find a triangle copy
- **WHEN** the template is 3 lines forming a triangle
- **AND** the drawing contains the template plus a translated copy of the same triangle
- **THEN** `find_matches` returns exactly one match containing all 3 entities of the copy

#### Scenario: Reject unrelated nearby lines
- **WHEN** the drawing contains the template triangle plus isolated lines far away
- **THEN** no spurious match is produced

#### Scenario: Densely packed neighbours do not inflate the candidate cloud
- **WHEN** a multi-entity pattern is surrounded by similar patterns within bounding-box radius
- **THEN** each correct pattern instance is reported as exactly one match
- **AND** no neighbour entities are absorbed into the match


### Requirement: Closing-vertex normalisation

Before computing centroid and PCA the matcher SHALL drop the trailing
duplicate-of-first vertex that flattened closed polylines emit. The
path-length statistic SHALL still be computed on the full sequence so
closed shapes report their perimeter.

#### Scenario: Closing duplicate does not bias mirror alignment
- **WHEN** a rectangle template `[a, b, c, d, a]` is mirrored across an axis
- **THEN** `align_score` against the mirrored copy returns a chamfer distance below 1e-3


### Requirement: N_JOBS parallelism toggle

Single-entity matching SHALL support an `n_jobs` parameter that defaults
to the module-level `N_JOBS` constant (read from `SMDR2_N_JOBS` env
var, default 1). When `n_jobs == 1` matching SHALL run single-process
with no multiprocessing overhead. When `n_jobs > 1` and the candidate
count exceeds `n_jobs * 200`, candidates SHALL be split into chunks and
dispatched across a lazy module-level `ProcessPoolExecutor`. Results
SHALL be equivalent regardless of `n_jobs`.

#### Scenario: n_jobs > 1 yields the same matches as n_jobs == 1
- **WHEN** the same single-entity template is matched with `n_jobs=1` and `n_jobs=2`
- **THEN** the set of matched handles is identical


### Requirement: Near-misses for diagnostics

The matcher SHALL distinguish between confirmed matches and near-misses
(candidates that passed `signatures_compatible` but failed `align_score`
on scale or shape). Near-misses SHALL be returned in `MatchOutput.near_misses`
with a `reason` field of `"scale"` or `"shape"`.

#### Scenario: Wrong-sized but right-shape candidate is a near-miss
- **WHEN** a candidate's shape matches but its optimal scale falls outside [0.95, 1.05]
- **THEN** it appears in `near_misses` with `reason: "scale"`

# pattern-matching Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
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
candidate entity in the drawing, pre-filter by `signatures_compatible`,
and verify remaining candidates with `align_score`. Template-side state
SHALL be computed once outside the loop.

`signatures_compatible` SHALL apply the following rotation-, mirror- and
scale-invariant gates (a candidate must pass ALL gates to reach
alignment):

1. **Empty / degenerate**: both entities have ≥2 vertices.
2. **Path length**: when both path lengths are positive, their ratio
   lies in `[1 − 0.20, 1 + 0.20]`.
3. **Radius**: when both radii are positive, the ratio of `radius`
   (max distance from centroid) lies in `[1 − 0.20, 1 + 0.20]`.
4. **PCA σ-ratio**: the absolute difference between the two entities'
   `σ2 / σ1` (singular values of the centered point cloud, with
   `σ1 ≥ σ2`) is less than `0.15`. When `σ1 = 0` for an entity, its
   σ-ratio SHALL be treated as `0`.

`EntityShape` SHALL carry `pca_sigma1` and `pca_sigma2` as derived
fields populated by `from_points`, so `signatures_compatible` is O(1)
per candidate.

#### Scenario: Find translated copies of a single entity
- **WHEN** the template is one rectangle and the drawing contains 3 translated copies plus the template itself
- **THEN** `find_matches([template_handle], drawing)` returns exactly the 3 copies
- **AND** the template's own handle does not appear in the results

#### Scenario: Reject a different shape with similar size
- **WHEN** a candidate has the same vertex count but different aspect ratio
- **THEN** it does not appear in matches
- **AND** it MAY appear in near-misses with `reason: "shape"`

#### Scenario: Same-perimeter rectangle and square are rejected at signature
- **WHEN** the template is a 2×1 rectangle (perimeter 6) and a candidate is a 1.5×1.5 square (perimeter 6) translated elsewhere in the drawing
- **THEN** `signatures_compatible(template, candidate)` returns `False`
- **AND** no chamfer alignment is run for that candidate
- **AND** `find_matches` returns no match for it

#### Scenario: Thin line and similar-perimeter thick shape are rejected at signature
- **WHEN** the template is a long thin line (σ-ratio near 0) and a candidate is a near-square polyline of comparable path length (σ-ratio near 1)
- **THEN** `signatures_compatible` returns `False` because the σ-ratios differ by ≥0.15

#### Scenario: σ-ratio gate tolerates noise within ±0.15
- **WHEN** template and candidate have σ-ratios `0.50` and `0.60`
- **THEN** `signatures_compatible` does not reject on the σ-ratio gate alone

#### Scenario: Rotation does not change signature ratios
- **WHEN** a candidate is the template rotated by an arbitrary angle
- **THEN** the candidate's `radius` and σ-ratio match the template's within numerical noise
- **AND** `signatures_compatible(template, candidate)` returns `True`

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

### Requirement: Matcher reads tunables from live module attributes

The pattern matcher in `app/matching.py` SHALL resolve its tunable
constants (`SCALE_MIN`, `SCALE_MAX`, `TOLERANCE_ABS`,
`VERTEX_COUNT_RATIO`, `PATH_LENGTH_RATIO`, `RADIUS_RATIO`,
`SIGMA_RATIO_TOL`, `RESAMPLE_N`, `BRUTE_FORCE_CUTOFF`) through
module-attribute lookup at the time each helper is called, so a
runtime mutation to any of those attributes via the developer-override
store SHALL be picked up by the next match call without restart.

The change SHALL be a no-op at compiled default values: matching
results, near-misses, and scan-all output SHALL remain bit-identical
to the prior implementation when no overrides have been applied.

#### Scenario: Default matching unchanged
- **WHEN** the override store has not been touched since startup
- **THEN** matching produces the same matches and near-misses as before this change for the same template and candidates

#### Scenario: Override widens scale band for the next match
- **WHEN** the override store sets `SCALE_MIN = 0.99` and `SCALE_MAX = 1.01`, then a match call is issued
- **THEN** candidates whose optimal scale falls within the new band are returned as matches (previously near-misses with `reason: "scale"`), and reverting the overrides restores the original behaviour

#### Scenario: Override tightens shape tolerance for the next match
- **WHEN** the override store sets `TOLERANCE_ABS = 0.001`, then a match call is issued
- **THEN** at least one previously-matched candidate falls into `near_misses` with `reason: "shape"`


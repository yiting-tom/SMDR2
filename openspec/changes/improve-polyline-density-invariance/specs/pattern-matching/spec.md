## ADDED Requirements

### Requirement: Density-invariant single-entity matching

The matcher SHALL accept two entities as a match whenever they lie on
the same curve under the supported transforms (translation, rotation,
mirror, isotropic scale ∈ [0.95, 1.05]) regardless of how densely
each entity is sampled along that curve. Specifically, two polylines
describing the same closed outline — one with N vertices, one with M
vertices, where the `N / M` ratio is anywhere from `~1` up to `~10×`
— SHALL match each other when their path lengths and shapes agree.

This SHALL be achieved by resampling both the template cloud and each
candidate cloud to a fixed canonical density (arclength-uniform
spacing) before computing centroid, PCA axes, scale, or Chamfer
distance. The original `EntityShape.points` SHALL remain the
file-original geometry; resampling is internal to the matcher and is
NOT observable through any persisted artifact or API response.

#### Scenario: Mirror substrate with different vertex counts matches
- **WHEN** the template is a closed 11-vertex polyline describing a substrate outline of perimeter ≈ 24 mm
- **AND** the drawing contains a mirrored copy of the same substrate stored as a 65-vertex polyline of the same perimeter
- **THEN** `find_matches([template_handle], drawing)` returns the 65-vertex handle as a match

#### Scenario: Same path length but genuinely different shape does NOT match
- **WHEN** the template is a 24-mm-perimeter closed polygon
- **AND** the drawing contains a 24-mm-perimeter circle (path length matches but shape doesn't)
- **THEN** the circle is NOT returned as a match
- **AND** it MAY appear in `near_misses` with `reason: "shape"`

#### Scenario: Degenerate-vertex inputs are still handled
- **WHEN** the template is a 2-vertex line segment
- **AND** the drawing contains a translated copy of that segment
- **THEN** the translated copy is returned as a match
- **AND** no exception is raised for the low-vertex input

## MODIFIED Requirements

### Requirement: Transform-invariant matching

The matcher SHALL find candidates that match a template under any
combination of translation, rotation (any angle), mirroring (any axis),
and isotropic scaling within the closed interval [0.95, 1.05]. The
match acceptance threshold SHALL be a chamfer distance ε (default
0.05 in drawing units).

Internally the matcher resamples both clouds to a canonical
arclength-uniform density before scoring, so per-entity vertex-count
differences no longer bias Chamfer. As a side effect, the chamfer
score for transformed-but-otherwise-identical inputs is bounded by
about half the resample sample spacing rather than being numerically
zero. Scenario thresholds below reflect the new realistic floor (well
within ε) instead of the previous ULP-tight values, but the matcher's
acceptance threshold ε is unchanged.

#### Scenario: Translated copy matches
- **WHEN** the candidate is the template translated by a non-zero vector
- **THEN** `align_score` returns a chamfer distance below ε
- **AND** when the inputs are bit-identical the score is below 1e-6

#### Scenario: Rotated copy matches at arbitrary angles
- **WHEN** the candidate is the template rotated by 30°, 90°, 137°, or 270°
- **THEN** `align_score` returns a chamfer distance below ε

#### Scenario: Mirrored copy matches
- **WHEN** the candidate is the template mirrored across the y-axis
- **THEN** `align_score` returns a chamfer distance below ε

#### Scenario: Within-tolerance scale matches
- **WHEN** the candidate is the template scaled by a factor in (0.95, 1.05)
- **THEN** `align_score` returns a non-None result with chamfer below ε
- **AND** the reported scale lies inside [0.95, 1.05]

#### Scenario: Out-of-tolerance scale is rejected
- **WHEN** the candidate is the template scaled by 1.5
- **THEN** `align_score` returns None (caller treats as a near-miss)

### Requirement: Single-entity template matching

When the template is exactly one entity, the matcher SHALL scan every
candidate entity in the drawing, pre-filter by `signatures_compatible`
(path length within ±20%; vertex count is no longer a gate, only a
`vertex_count < 2` degeneracy guard remains), and verify remaining
candidates with `align_score`. Template-side state SHALL be computed
once outside the loop. Both template and candidate SHALL be resampled
to a canonical arclength-uniform density before centroid / PCA /
scale / Chamfer so per-entity vertex-count differences do not bias the
score. **EXCEPT** when the template entity has `kind == "circle"` and
`radius > 0`: in that case the matcher SHALL dispatch to the
radius-bucket fast path (see "Single-CIRCLE template fast path via
radius bucket"), which bypasses all of the above.

#### Scenario: Find translated copies of a single entity
- **WHEN** the template is one rectangle and the drawing contains 3 translated copies plus the template itself
- **THEN** `find_matches([template_handle], drawing)` returns exactly the 3 copies
- **AND** the template's own handle does not appear in the results

#### Scenario: Reject a different shape with similar size
- **WHEN** a candidate has the same path length but different aspect ratio / different shape
- **THEN** it does not appear in matches
- **AND** it MAY appear in near-misses with `reason: "shape"`

#### Scenario: Path-length-incompatible candidate rejected up front
- **WHEN** a candidate's path length differs from the template's by more than ±20%
- **THEN** `signatures_compatible` returns False
- **AND** that candidate is excluded before any resampling or Chamfer is computed

#### Scenario: CIRCLE template skips the resampling path
- **WHEN** the template is a single CIRCLE EntityShape (`kind == "circle"`, `radius > 0`)
- **THEN** the matcher dispatches to the radius-bucket fast path and does NOT resample anything

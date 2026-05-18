## MODIFIED Requirements

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

## MODIFIED Requirements

### Requirement: Multi-entity template matching (pose-based)

When the template consists of N≥2 entities, the matcher SHALL treat the
problem as rigid-transform congruence — the drawing is assumed to
contain instances that are translate / rotate / mirror copies of the
template, with no scale variation and no shape drift. The matcher
SHALL:

1. Pick the rarest template entity (smallest fingerprint-bucket
   intersection in the drawing) as the seed.
2. Enumerate candidate seeds **only from the seed's fingerprint bucket**
   — full-drawing scans gated by `signatures_compatible` SHALL NOT be
   used on this path.
3. For each candidate seed, recover the rigid transform `(R, t)`
   mapping the template seed's frame to the candidate seed's frame
   using one of two strategies, chosen by the seed's σ₂/σ₁ ratio:
   - **Anisotropic seed (`σ₂/σ₁ ≤ 0.95`):** use `_pca_axes` on the
     seed and the candidate, then try all four PCA sign variants
     `(±1, ±1)` to cover the mirror / 180° ambiguity. (Pre-existing
     behaviour.)
   - **Isotropic seed (`σ₂/σ₁ > 0.95`):** PCA orientation is
     numerically unstable for circles, squares, regular polygons,
     etc., so the matcher SHALL fall back to **2-point alignment**:
     iterate over the first other-template-entity's fingerprint
     bucket; for each `(cand_seed, cand_other_first)` pair whose
     drawing-side centroid distance matches the template-side
     centroid distance within `CENTROID_NOISE_TOL`, recover `R`
     from the angle between the two centroid-pair line directions
     and `t` from the seed translation; for N≥3 templates also try
     the mirrored variant of that rotation. No PCA orientation is
     used in this path.
4. For each other template entity, compute its expected world centroid
   `R · c_template + t` and look up the nearest drawing entity by
   centroid KDTree within a centroid-tolerance that absorbs numerical
   noise from the rigid transform.
5. Verify that the looked-up entity's fingerprint equals the template
   entity's fingerprint. A match SHALL be reported only when every
   other template entity is found at its predicted centroid with a
   fingerprint match.

The matcher SHALL NOT call `align_score` or any chamfer-distance
function on the multi-entity path.

#### Scenario: Find a triangle copy
- **WHEN** the template is 3 lines forming a triangle
- **AND** the drawing contains the template plus a translated copy of the same triangle
- **THEN** `find_matches` returns exactly one match containing all 3 entities of the copy
- **AND** the reported `scale` is exactly 1.0

#### Scenario: Reject unrelated nearby lines
- **WHEN** the drawing contains the template triangle plus isolated lines far away
- **THEN** no spurious match is produced

#### Scenario: Densely packed neighbours do not inflate the candidate cloud
- **WHEN** a multi-entity pattern is surrounded by similar patterns within bounding-box radius
- **THEN** each correct pattern instance is reported as exactly one match
- **AND** no neighbour entities are absorbed into the match

#### Scenario: Wrong-shape seed candidate is rejected at the fingerprint gate
- **WHEN** a drawing entity has the same path-length and similar centroid radius as the seed template but a different shape (different fingerprint)
- **THEN** the entity is not enumerated as a candidate seed and no match is produced for it — even when other template entities happen to align at predicted positions

#### Scenario: Mirrored copy matches (anisotropic seed path)
- **WHEN** the drawing contains a mirrored copy of a multi-entity template whose seed has `σ₂/σ₁ ≤ 0.95`
- **THEN** `find_matches` returns the mirrored copy as a match via one of the four PCA sign variants

#### Scenario: 2-entity isotropic template matches every row copy
- **WHEN** the template is two visually-identical isotropic shapes (e.g. two rounded squares with `σ₂/σ₁ > 0.95`) separated by a known offset
- **AND** the drawing contains a row of ≥5 copy-paste copies of that pair
- **THEN** `find_matches_from_pointsets` returns every copy as a match (full recall) — no copy is dropped due to seed-PCA orientation noise

#### Scenario: 3+ entity isotropic-seed template still covers mirror
- **WHEN** the template has N≥3 entities and the seed is isotropic
- **AND** the drawing contains a mirrored copy of the template
- **THEN** the matcher returns the mirrored copy as a match — the 2-point fallback path's mirror variant covers the case the four PCA sign variants would have covered on the anisotropic path

#### Scenario: Anisotropic-seed templates are unaffected
- **WHEN** the seed has `σ₂/σ₁ ≤ 0.95` (e.g. a narrow rectangle, an L-shape, an arc)
- **THEN** the matcher uses the PCA-based four-sign-variant path
- **AND** every existing multi-entity match behaviour (triangle copy, dense-neighbour suppression, wrong-shape rejection, frame-select stacked duplicates, close-packed neighbour symmetry) holds unchanged

#### Scenario: Distance gate rejects unrelated isotropic pairs
- **WHEN** the seed is isotropic and the drawing contains another isotropic shape with the same fingerprint at an unrelated distance from a seed candidate (not matching the template's seed→other distance)
- **THEN** the matcher SHALL NOT produce a spurious match — the candidate pair is rejected at the distance gate before the rigid transform is even computed

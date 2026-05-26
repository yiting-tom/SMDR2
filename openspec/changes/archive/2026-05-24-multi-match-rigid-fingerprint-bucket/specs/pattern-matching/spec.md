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
3. For each candidate seed, recover the rigid transform `(R, t)` mapping
   the template seed's PCA frame to the candidate seed's PCA frame,
   trying all four PCA sign variants to cover the mirror / 180°
   ambiguity.
4. For each other template entity, compute its expected world centroid
   `R · c_template + t` and look up the nearest drawing entity by
   centroid KDTree within a centroid-tolerance that absorbs numerical
   noise from the rigid transform (≤ 1e-6 for mm-unit DXFs).
5. Verify that the looked-up entity's fingerprint equals the template
   entity's fingerprint exactly. A match SHALL be reported only when
   every other template entity is found at its predicted centroid with
   a fingerprint match.

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

#### Scenario: Mirrored copy matches
- **WHEN** the drawing contains a mirrored copy of a multi-entity template
- **THEN** `find_matches` returns the mirrored copy as a match via one of the four PCA sign variants

## ADDED Requirements

### Requirement: Drawing-level fingerprint bucket cache

The matcher SHALL maintain a drawing-level cache mapping each entity's
quantised fingerprint to the list of handles that share it. The
fingerprint key MUST be derived from rotation-, translation-, and
mirror-invariant scalars (`path_length`, `radius`, `sigma_ratio`) each
rounded to a precision that matches the DXF coordinate resolution (see
design.md for the chosen digit count). The cache MUST be keyed by the
drawing dict's identity, mirroring the existing
`_radius_bucket_cache` contract — a fresh drawing dict (produced by a
library swap or re-preprocess) SHALL produce a fresh bucket dict.

#### Scenario: Same drawing dict reuses the cache
- **WHEN** `_match_multi` is called twice with the same `drawing` dict object
- **THEN** the fingerprint bucket is computed once on the first call and reused on the second

#### Scenario: New drawing dict produces fresh buckets
- **WHEN** the drawing's `EntityShape` dict is rebuilt (different object identity, e.g. after re-preprocess)
- **THEN** the fingerprint bucket cache key changes and the next `_match_multi` call rebuilds the bucket from the new dict

### Requirement: Multi-entity match reports `scale = 1.0`

Every `MatchResult` produced by the multi-entity path SHALL carry
`scale = 1.0` exactly (not approximately). The rigid-transform matcher
MUST NOT perform any scale search; under the rigid data contract scale
variation is impossible by construction.

#### Scenario: Triangle match
- **WHEN** the new matcher returns a triangle-copy match
- **THEN** the `MatchResult.scale` field equals `1.0` exactly

#### Scenario: Dense-neighbour match
- **WHEN** the new matcher returns matches from a dense-neighbour drawing
- **THEN** every returned `MatchResult.scale` equals `1.0` exactly

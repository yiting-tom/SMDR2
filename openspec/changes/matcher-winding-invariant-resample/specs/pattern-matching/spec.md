## ADDED Requirements

### Requirement: Resampling is winding- and vertex-order invariant

Single-entity chamfer matching SHALL be invariant to the stored vertex order
and winding direction of an outline. Before resampling a cloud to `RESAMPLE_N`
arclength-spaced points, the matcher SHALL anchor the sample start at a
geometry-determined vertex — the vertex furthest from the cloud's centroid —
so that two geometrically identical outlines stored with a different first
vertex or opposite winding (CW vs CCW) resample to the same sample grid and
score a chamfer distance of ~0.

This anchoring applies to the production single-entity matcher
(`_match_single_serial`, both template-side and per-candidate) and to
`align_score`. It does NOT apply to the multi-entity pose-based matcher (which
computes no chamfer) or to the signature-only mode.

Residual ambiguity when several vertices are equidistant from the centroid
(e.g. a near-square outline whose corners tie) SHALL be absorbed by the
existing 4 sign-variant orientation search, so the match still resolves.

#### Scenario: Reversed-winding identical copy matches

- **WHEN** a single-entity template is matched against an exact geometric copy of itself that is stored with the opposite winding direction AND a different first vertex AND translated elsewhere in the drawing
- **THEN** the candidate SHALL be reported as a match (chamfer ≤ `TOLERANCE_ABS`)
- **AND** the reported chamfer score SHALL be near zero (well below the tolerance), not a borderline pass

#### Scenario: Symmetric outline matches under every winding/start permutation

- **WHEN** the template is a rectangle-like outline whose corners are equidistant from the centroid (an anchor tie)
- **AND** the candidate is a copy of it under any combination of translation, rolled start vertex, reversed winding, 180° rotation, or mirror
- **THEN** the candidate SHALL be reported as a match
- **AND** the anchor-vertex tie SHALL be resolved by the downstream sign-variant orientation search rather than producing a near-miss

#### Scenario: Genuinely different shapes are still rejected

- **WHEN** a candidate differs from the template by a real geometric feature beyond tolerance (e.g. a notch in a different position, or a changed corner chamfer) even though its bounding box or perimeter is similar
- **THEN** the canonical resample anchor SHALL NOT cause a false match
- **AND** the candidate SHALL remain a near-miss (chamfer > `TOLERANCE_ABS`) or be rejected at the signature gate

## ADDED Requirements

### Requirement: Drawing-level signature index cache

The single-entity chamfer scan SHALL gate candidates against a drawing-level
signature index — parallel arrays of each shape's `vertex_count`,
`path_length`, `radius`, and σ-ratio — instead of calling
`signatures_compatible` once per shape per template. The index MUST be keyed by
the drawing dict's identity, mirroring the `_radius_bucket_cache` /
fingerprint-bucket contract: a fresh drawing dict (library swap or
re-preprocess) SHALL produce a fresh index, and repeated templates over the
same drawing dict SHALL reuse the one already built.

The vectorised gate's result SHALL be identical to evaluating
`signatures_compatible(template, shape)` for every shape: same path-length,
radius, and σ-ratio tolerances, same handling of zero-valued dimensions, and
the override-at-call-time behaviour of `PATH_LENGTH_RATIO` / `RADIUS_RATIO`.
The candidate set, the resulting matches, and the near-misses SHALL be
unchanged — this is a performance optimisation with no behavioural effect.

#### Scenario: Same drawing dict reuses the index across templates

- **WHEN** the single-entity chamfer path is invoked for several templates against the same `drawing` dict object (e.g. a scan-all)
- **THEN** the signature index is computed once on the first template and reused for the rest

#### Scenario: New drawing dict produces a fresh index

- **WHEN** the drawing's `EntityShape` dict is rebuilt (different object identity, e.g. after re-preprocess)
- **THEN** the index cache key changes and the next scan rebuilds the arrays from the new dict

#### Scenario: Vectorised gate matches the per-shape gate exactly

- **WHEN** a single-entity template is scanned against a drawing
- **THEN** the set of candidate handles passing the vectorised gate equals the set for which `signatures_compatible(template, shape)` returns `True`
- **AND** the confirmed matches and near-misses are identical to the per-shape implementation

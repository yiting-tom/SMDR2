## ADDED Requirements

### Requirement: Single-class pool short-circuits arbitration

When the input dictionary `out` contains keys for exactly one of the group's member classes (e.g. only `bga_ball.*` keys, no `fiducial_circle.*` keys), the system SHALL short-circuit `arbitrate()` for that group: classify, fallback, and re-emit are ALL skipped. The source keys remain untouched in the rewritten output. The single-class invariant guarantees no cross-class disambiguation is possible — `classify()` could only mis-label individual instances based on neighbour density, never resolve genuine ambiguity.

The gate check examines RAW input keys, not the deduplicated `original_class` field on each `_Instance`: when the matcher cross-fires both templates on the same handles, `pool_instances` dedupes by handle set and would surface only the lex-first source key's class on every instance, which would make a true cross-fire indistinguishable from a single-template library. Reading the raw key set distinguishes the two cases correctly.

The "Population fallback" requirement still applies when the input contains keys for two or more of the group's member classes.

#### Scenario: Single-class pool with low-density instances preserves original labels

- **WHEN** the library has only `BGABall` templates (no `FiducialCircle` template)
- **AND** scan-all produces N BGABall match instances where many have 0 or 1 neighbours within `1.5 × derived_pitch` (e.g. the BGABall template cross-fired onto isolated same-radius vias / drill holes / decorative dots in addition to the main BGA grid)
- **AND** every pool instance has `original_class == "BGABall"`
- **THEN** arbitration SHALL short-circuit
- **AND** every instance SHALL keep `original_class = "BGABall"` regardless of what `classify()` would have produced
- **AND** no `fiducial_circle.*` key SHALL appear in the rewritten output
- **AND** `GroupCounts.assigned == {"BGABall": N}`
- **AND** `GroupCounts.population_fallback_triggered == False`
- **AND** `GroupCounts.derived_pitch == None` (not computed)
- **AND** `GroupCounts.reassigned_from_match == 0`

#### Scenario: Single-class pool with FiducialCircle-only library preserves labels

- **WHEN** the library has only `FiducialCircle` templates (no `BGABall` template)
- **AND** scan-all produces 4 FiducialCircle match instances at the corners of the substrate
  (the original guard scenario for `apply_population_fallback`)
- **AND** every pool instance has `original_class == "FiducialCircle"`
- **THEN** arbitration SHALL short-circuit
- **AND** every instance SHALL stay `FiducialCircle`
- **AND** `GroupCounts.assigned == {"FiducialCircle": 4}`
- **AND** `GroupCounts.population_fallback_triggered == False`
  (the desired outcome — all 4 stay fiducials — is reached via the short-circuit
  instead of the historical classify-then-fallback path, but the end result is
  identical)

#### Scenario: Mixed-class input runs full arbitration

- **WHEN** the input dictionary `out` contains keys for both `BGABall` and
  `FiducialCircle` (e.g. both `bga_ball.0` and `fiducial_circle.0`)
- **AND** the library has templates for both classes
- **THEN** the single-class short-circuit SHALL NOT fire
- **AND** classify, the `default_in_pool` precondition, and the population
  fallback SHALL all run as defined by their respective requirements
- **AND** the matcher cross-fire is disambiguated by neighbour density
  (grid handles → BGABall, isolated handles → FiducialCircle)
  regardless of whether `pool_instances` dedup collapsed all handles
  onto a single `original_class`

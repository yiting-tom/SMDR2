## MODIFIED Requirements

### Requirement: Single-class pool short-circuits arbitration

When the input dictionary `out` contains keys for exactly one of the group's member classes, the system SHALL short-circuit `arbitrate()` for that group **only if that sole class is a non-default member** (`sole_class != group.default_class`): classify, fallback, and re-emit are skipped and the source keys remain untouched. A non-default member (e.g. `BGABall`) is the high-confidence claim — the matcher only fired its template on geometry that already passed that class's gate, so there is no cross-class competition and `classify()` could only mis-label by neighbour density (demoting isolated instances to phantom defaults).

When the sole class **is** the `default_class` (the safe fallback, e.g. `FiducialCircle`), the system SHALL NOT short-circuit. Density evidence can still disambiguate upward: a dense grid matched only by the default class's template must be promoted to the non-default member. The pool therefore falls through to the full classify + population-fallback pipeline — a genuine grid (≥ `min_population`) is promoted, while a handful of true default-class instances (< `min_population`) is collapsed back to the default by the floor.

The gate check examines RAW input keys, not the deduplicated `original_class` field on each `_Instance`: when the matcher cross-fires both templates on the same handles, `pool_instances` dedupes by handle set and would surface only the lex-first source key's class on every instance, which would make a true cross-fire indistinguishable from a single-template library. Reading the raw key set distinguishes the two cases correctly.

The "Population fallback" requirement still applies whenever the input contains keys for two or more member classes, or for the single default-class pool that falls through.

#### Scenario: Single-class non-default pool preserves original labels

- **WHEN** the library has only `BGABall` templates (no `FiducialCircle` template)
- **AND** scan-all produces N BGABall match instances where many have 0 or 1 neighbours within `1.5 × derived_pitch` (e.g. the BGABall template cross-fired onto isolated same-radius vias / drill holes / decorative dots in addition to the main BGA grid)
- **AND** every pool instance has `original_class == "BGABall"` (the sole class is non-default)
- **THEN** arbitration SHALL short-circuit
- **AND** every instance SHALL keep `original_class = "BGABall"` regardless of what `classify()` would have produced
- **AND** no `fiducial_circle.*` key SHALL appear in the rewritten output
- **AND** `GroupCounts.assigned == {"BGABall": N}`
- **AND** `GroupCounts.population_fallback_triggered == False`
- **AND** `GroupCounts.derived_pitch == None` (not computed)
- **AND** `GroupCounts.reassigned_from_match == 0`

#### Scenario: Single-class default pool that is a dense grid is promoted

- **WHEN** the input contains keys for only `fiducial_circle.*` (the default class)
- **AND** the instances form a dense grid where every point has ≥ 2 neighbours within `1.5 × derived_pitch`
- **AND** the grid size is ≥ `min_population`
- **THEN** the single-class short-circuit SHALL NOT fire (the sole class is the default)
- **AND** classify SHALL label every grid instance `BGABall`
- **AND** the population floor SHALL NOT trigger (non-default count ≥ `min_population`)
- **AND** every instance SHALL be re-emitted under a `bga_ball.*` key
- **AND** no `fiducial_circle.*` key SHALL remain in the rewritten output
- **AND** `GroupCounts.assigned == {"BGABall": N, "FiducialCircle": 0}`
- **AND** `GroupCounts.derived_pitch` SHALL be the computed median NN distance (not `None`)

#### Scenario: Single-class default pool below the floor collapses to the default

- **WHEN** the library has only `FiducialCircle` templates (no `BGABall` template)
- **AND** scan-all produces 4 `FiducialCircle` match instances at the corners of the substrate
- **AND** every pool instance has `original_class == "FiducialCircle"` (the sole class is the default)
- **THEN** the single-class short-circuit SHALL NOT fire
- **AND** classify MAY label the corners `BGABall` (each corner has ≥ 2 neighbours within `1.5 ×` the corner pitch)
- **AND** the population floor SHALL trigger (non-default count `4 < min_population`) and collapse all 4 back to `FiducialCircle`
- **AND** every instance SHALL stay `FiducialCircle`
- **AND** `GroupCounts.assigned == {"FiducialCircle": 4, "BGABall": 0}`
- **AND** `GroupCounts.population_fallback_triggered == True`
- **AND** `GroupCounts.derived_pitch` SHALL be the computed median NN distance (not `None`)

#### Scenario: Mixed-class input runs full arbitration

- **WHEN** the input dictionary `out` contains keys for both `BGABall` and `FiducialCircle` (e.g. both `bga_ball.0` and `fiducial_circle.0`)
- **AND** the library has templates for both classes
- **THEN** the single-class short-circuit SHALL NOT fire
- **AND** classify, the `default_in_pool` precondition, and the population fallback SHALL all run as defined by their respective requirements
- **AND** the matcher cross-fire is disambiguated by neighbour density (grid handles → BGABall, isolated handles → FiducialCircle) regardless of whether `pool_instances` dedup collapsed all handles onto a single `original_class`

## ADDED Requirements

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

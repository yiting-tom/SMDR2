## ADDED Requirements

### Requirement: Context-specific arbitration entry points

Production code SHALL invoke arbitration through stage-specific entry points
that bind the view-constraint enforcement mode to the pipeline stage, rather
than passing an enforcement flag directly at each call site:

- `arbitrate_for_prematch(out, shapes, groups)` — used by the
  preprocess/prematch stage, where view rectangles are not yet drawn and every
  instance has `view_prefix = None`. It SHALL resolve cross-fire between member
  classes WITHOUT dropping any instance for a view-constraint conflict; the
  returned `view_drops` SHALL be empty.
- `arbitrate_for_match(out, shapes, groups)` — used by the save-match and
  scan-all stages. It SHALL re-validate each reassigned instance's new class
  against its view prefix and drop view-conflicting instances into
  `dropped_by_view`, per the "Integration with Match JSON serialisation"
  requirement.

Both entry points SHALL delegate to the same underlying arbitration algorithm
(pooling, pitch derivation, neighbour-count classification, population
fallback, single-class short-circuit, and deterministic ordering); the ONLY
behavioural difference between them SHALL be view-conflict handling. The
low-level `arbitrate(..., *, enforce_view_constraints=...)` entry point MAY
remain available for tests and explicit control, but production call sites
SHALL use the stage-specific entry points.

#### Scenario: Prematch entry point preserves view-constrained instances
- **GIVEN** an arbitration pool in which a reassigned instance's new class is
  view-constrained and the instance has `view_prefix = None`
- **WHEN** `arbitrate_for_prematch` runs over the pool
- **THEN** the instance is retained under its reassigned class
- **AND** the returned `view_drops` is empty (no instance is dropped for a
  view conflict)

#### Scenario: Match entry point drops view-conflicting instances
- **GIVEN** a `top_view`-prefixed instance that arbitration reassigns from
  `FiducialCircle` to `BGABall`
- **AND** `CLASS_VIEW_CONSTRAINTS["BGABall"] == {"bottom_view", "side_view"}`
- **WHEN** `arbitrate_for_match` runs
- **THEN** the instance is dropped (not emitted under any key)
- **AND** counted in `dropped_by_view`

#### Scenario: Both entry points agree on class assignment absent view conflicts
- **GIVEN** an arbitration pool with no view-constraint conflicts
- **WHEN** the pool is arbitrated via `arbitrate_for_prematch` and,
  separately, via `arbitrate_for_match`
- **THEN** both produce identical class assignments (the entry points differ
  only in view-conflict handling)

#### Scenario: Production call sites use the stage-specific entry points
- **WHEN** the prematch, save-match, and scan-all stages invoke arbitration
- **THEN** prematch calls `arbitrate_for_prematch`
- **AND** save-match and scan-all call `arbitrate_for_match`
- **AND** no production call site passes `enforce_view_constraints` directly

## ADDED Requirements

### Requirement: Per-class neighbour-count rule registry

The system SHALL expose a data-driven registry
`library.CLASS_ARBITRATION_GROUPS` (see the `class-arbitration`
capability for the full schema) co-located with `CLASS_VIEW_CONSTRAINTS`
in `app/library.py`.

The library SHALL seed at least one default group whose members are
`{"BGABall", "FiducialCircle"}` with the rules and fallback configured by
the `class-arbitration` spec. Both members are already part of
`DEFAULT_CLASSES`, so no additional class-seeding migration is needed:
existing libraries on disk already carry both member classes via the
existing seed-on-boot logic (`Default class seeding` requirement above).

The library module SHALL expose a helper
`library.arbitration_group_for(class_name: str) -> ArbitrationGroup | None`
that returns the (unique) group containing `class_name`, or `None` when
the class is not part of any group. A class display ID SHALL belong to
at most one group; constructing the registry with a class in two groups
SHALL fail at import time with a clear `ValueError`.

If both `CLASS_VIEW_CONSTRAINTS` and `CLASS_ARBITRATION_GROUPS` apply to
a class, the view-constraint check SHALL remain the final gate: after
arbitration reassigns an instance, its new class's view rule is
re-checked, and the instance is dropped if disallowed
(see `class-arbitration` spec's "Integration with Match JSON
serialisation" requirement for the exact ordering).

#### Scenario: Default seed includes BGA/Fiducial group
- **WHEN** the application boots
- **THEN** `CLASS_ARBITRATION_GROUPS` contains a group whose `members`
  equals `frozenset({"BGABall", "FiducialCircle"})`

#### Scenario: Lookup helper returns the containing group
- **WHEN** `arbitration_group_for("BGABall")` is called
- **THEN** the returned group's `members` contains both `"BGABall"`
  and `"FiducialCircle"`
- **AND** `arbitration_group_for("Substrate")` returns `None`

#### Scenario: A class cannot appear in two groups
- **WHEN** the registry is constructed with `"BGABall"` listed in two
  separate `ArbitrationGroup` entries
- **THEN** import-time validation SHALL raise `ValueError`
  naming the conflicting class

#### Scenario: JS drift guard mirrors the Python registry
- **WHEN** any UI affordance under `app/static/canvas.js` is added
  that consumes the arbitration registry
- **THEN** the JS literal SHALL be wrapped in
  `// CLASS_ARBITRATION_GROUPS_BEGIN ... // CLASS_ARBITRATION_GROUPS_END`
  sentinel comments
- **AND** a test under `tests/test_canvas_constants.py` SHALL parse
  the JS literal and assert structural equality with the Python
  registry, failing the build on drift
- **AND** the JS may be a strict subset of the Python registry's
  fields (e.g., omit fields the UI does not need); the equality SHALL
  be checked over the fields the JS chooses to expose

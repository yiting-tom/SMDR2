## MODIFIED Requirements

### Requirement: Arbitration group declaration

The system SHALL expose a data-driven registry
`library.CLASS_ARBITRATION_GROUPS: tuple[ArbitrationGroup, ...]` where each
group declares a set of two or more class **display IDs** whose templates
can be geometrically indistinguishable, together with the neighbour-count
rule that disambiguates them.

The default registry SHALL be **empty** `()`. The historical
`BGABall`/`FiducialCircle` density group is retired: that pair is now
disambiguated by mutually exclusive view constraints (`BGABall` bottom-only,
`FiducialCircle` top-only — see `template-library`), which is deterministic
and avoids the density heuristic's `derive_pitch` / population-floor edge
cases. The `arbitrate()` machinery and the `ArbitrationGroup` contract are
retained so a future *same-view* same-geometry collision can be handled by
adding a group; with an empty registry `arbitrate()` is a no-op pass-through.

Each `ArbitrationGroup` SHALL specify:

| Field             | Type                              | Meaning |
|-------------------|-----------------------------------|---------|
| `members`         | `frozenset[str]`                  | Class display IDs participating in this group. |
| `rules`           | `dict[str, NeighborRule]`         | Per-member neighbour-count rule, keyed by display ID. Every member in `members` SHALL have a rule. |
| `default_class`   | `str`                             | The class to assign instances to when the population-fallback fires. SHALL be one of `members`. |
| `min_population`  | `int`                             | If, after arbitration, any **non-default** member class would receive fewer than this many instances, every instance in the group SHALL be reassigned to `default_class`. The default class itself has no floor. |
| `pitch_multiplier`| `float`                           | Neighbour search radius is `pitch_multiplier × derived_pitch`. Defaults to `1.5`. |

`NeighborRule` SHALL be one of:

- `MinNeighbors(n)` — the instance's neighbour count SHALL be `≥ n`.
- `MaxNeighbors(n)` — the instance's neighbour count SHALL be `≤ n`.

#### Scenario: Default registry is empty
- **WHEN** the application boots with default configuration
- **THEN** `CLASS_ARBITRATION_GROUPS` SHALL equal `()`
- **AND** `arbitration_group_for("BGABall")` SHALL return `None`
- **AND** `arbitration_group_for("FiducialCircle")` SHALL return `None`
- **AND** `arbitrate()` over the empty registry SHALL return its input unchanged

#### Scenario: A member must have a rule
- **WHEN** an `ArbitrationGroup` is constructed
- **AND** some class in `members` is not a key of `rules`
- **THEN** construction SHALL fail with a clear error
  (`ValueError` naming the missing member)

#### Scenario: default_class must be a member
- **WHEN** an `ArbitrationGroup` is constructed with
  `default_class` not in `members`
- **THEN** construction SHALL fail with a clear error

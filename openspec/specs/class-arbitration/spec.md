# class-arbitration

## Purpose

Post-match resolution of class membership when multiple classes have
geometrically indistinguishable templates (e.g., `BGABall` and
`FiducialCircle` when their circle diameters match). The pattern matcher
itself is class-agnostic, so identical-shape templates fire on the same
handles; this capability assigns each handle to exactly one class using
the spatial density of its neighbours.
## Requirements
### Requirement: Arbitration group declaration

The system SHALL expose a data-driven registry
`library.CLASS_ARBITRATION_GROUPS: tuple[ArbitrationGroup, ...]` where each
group declares a set of two or more class **display IDs** whose templates
can be geometrically indistinguishable, together with the neighbour-count
rule that disambiguates them.

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

The registry SHALL include at minimum one group covering `BGABall` and
`FiducialCircle`:

```python
ArbitrationGroup(
    members=frozenset({"BGABall", "FiducialCircle"}),
    rules={
        "BGABall":        MinNeighbors(2),
        "FiducialCircle": MaxNeighbors(1),
    },
    default_class="FiducialCircle",
    min_population=8,
    pitch_multiplier=1.5,
)
```

#### Scenario: Default registry contains the BGA/Fiducial group
- **WHEN** the application boots with default configuration
- **THEN** `CLASS_ARBITRATION_GROUPS` contains exactly one group
- **AND** that group's `members` equals `frozenset({"BGABall", "FiducialCircle"})`
- **AND** `rules["BGABall"]` is `MinNeighbors(2)`
- **AND** `rules["FiducialCircle"]` is `MaxNeighbors(1)`
- **AND** `default_class == "FiducialCircle"`

#### Scenario: A member must have a rule
- **WHEN** an `ArbitrationGroup` is constructed
- **AND** some class in `members` is not a key of `rules`
- **THEN** construction SHALL fail with a clear error
  (`ValueError` naming the missing member)

#### Scenario: default_class must be a member
- **WHEN** an `ArbitrationGroup` is constructed with
  `default_class` not in `members`
- **THEN** construction SHALL fail with a clear error

### Requirement: Auto-derived grid pitch

For each arbitration group, the system SHALL derive the grid pitch
empirically from the **pooled centroids** of all matched instances of
every member class in that group.

The derived pitch SHALL be the **median of the per-centroid
nearest-neighbour distances** over the pooled set, computed in the file's
working coordinate space.

If the pooled set has fewer than 2 centroids, the group SHALL be skipped
(no arbitration runs; matches are emitted unchanged).

#### Scenario: Pitch is the median NN distance
- **WHEN** matched instances of an arbitration group's members produce
  centroids at `(0,0), (1,0), (2,0), (3,0), (10,10)`
- **THEN** the per-centroid NN distances are `1, 1, 1, 1, ~11.4`
- **AND** the derived pitch is `1.0`

#### Scenario: Pitch is robust to a minority of outliers
- **WHEN** a 10×10 BGA grid at pitch `0.4` mm is matched
- **AND** 4 isolated fiducials at the substrate corners are also matched
- **THEN** the derived pitch SHALL be within `±5%` of `0.4` mm

#### Scenario: Empty or single-element pool skips arbitration
- **WHEN** the pooled centroid set for a group has fewer than 2 entries
- **THEN** arbitration is skipped
- **AND** the Match JSON is emitted with the matches produced by the
  matching step unchanged

### Requirement: Neighbour-count classification

For each instance in an arbitration group's pooled set, the system SHALL
count the number of **other** instances in the pool whose centroid lies
within `pitch_multiplier × derived_pitch` of the instance's centroid (a
strict inequality, the instance itself excluded).

The instance SHALL then be assigned to the **unique** member class whose
`NeighborRule` is satisfied by that count.

If zero member rules match, the instance SHALL be assigned to
`default_class`.

If multiple member rules match (registry misconfiguration), construction
of the group SHALL have already failed; at runtime this case SHALL raise
an assertion error rather than emit silently wrong matches.

#### Scenario: Centre BGA ball has many neighbours
- **WHEN** an instance has 4 neighbours within the search radius
- **AND** the group's rules are
  `{"BGABall": MinNeighbors(2), "FiducialCircle": MaxNeighbors(1)}`
- **THEN** the instance is assigned to `"BGABall"`

#### Scenario: Isolated fiducial has zero neighbours
- **WHEN** an instance has 0 neighbours within the search radius
- **THEN** the instance is assigned to `"FiducialCircle"`

#### Scenario: Edge BGA ball with exactly 2 neighbours stays BGA
- **WHEN** an instance has exactly 2 neighbours within the search radius
- **THEN** the instance is assigned to `"BGABall"`
  (because `MinNeighbors(2)` is satisfied first)

### Requirement: Population fallback

The system SHALL reassign every instance in the group's pooled set to `default_class` when both of the following hold: (a) at least one **non-default** member class would receive **fewer than `min_population` instances** after per-instance classification, AND (b) `default_class` itself has at least one instance in the pool (i.e. at least one match-result was emitted from a `default_class` template via the pre-arbitration matching pass). The default-class count is NOT subject to the floor — realistic substrates may legitimately have only 4 fiducials, and that should not force fallback.

The `default_class`-in-pool precondition (b) prevents the fallback from inventing labels for which the library has no template: when the default class produced zero matches (e.g. the library has no `default_class` template, or its template did not match anything in this DXF), there is no evidence the safe direction is in play, and collapsing to it would create handle assignments under a class key backed by no template.

This guards against the degenerate case where a DXF contains, say, only fiducials (no BGA pattern at all): without this rule, the four corner circles would form their own pseudo-grid and be misclassified as BGA. The precondition does NOT weaken this guard — in that scenario the default-class (FiducialCircle) templates DID produce the original matches, so `default_in_pool=True` and the fallback still fires.

#### Scenario: BGA candidates below floor collapse to fiducials
- **WHEN** the BGA/Fiducial group has `min_population = 8`
- **AND** classification produced 4 instances labelled `"BGABall"`
  and 0 labelled `"FiducialCircle"`
- **AND** at least one of the pool's instances has `original_class == "FiducialCircle"`
  (e.g. the library has a FiducialCircle template that contributed matches)
- **THEN** all 4 instances SHALL be reassigned to `"FiducialCircle"`
- **AND** no instance remains labelled `"BGABall"`

#### Scenario: Non-default population above floor with thin default is preserved
- **WHEN** the group has `min_population = 8` and `default_class == "FiducialCircle"`
- **AND** classification produced 96 `"BGABall"` and 4 `"FiducialCircle"`
- **THEN** assignments are preserved as-is
  (96 BGA, 4 fiducials in the output)
- **AND** the fiducial count of 4 does NOT trigger fallback because
  `FiducialCircle` is the default class (it has no floor)

#### Scenario: Default class absent from the pool suppresses fallback
- **WHEN** the BGA/Fiducial group has `min_population = 8`
- **AND** the library contains only `"BGABall"` templates (no `"FiducialCircle"` template)
- **AND** scan-all / save-match produces 4 BGABall matches and 0 FiducialCircle matches
- **AND** every pool instance has `original_class == "BGABall"`
- **THEN** the fallback SHALL NOT trigger
- **AND** every instance SHALL keep the class assigned by `classify()` (BGABall)
- **AND** no `fiducial_circle.*` key SHALL appear in the rewritten output
- **AND** `GroupCounts.population_fallback_triggered` SHALL be `False`

#### Scenario: Degenerate fiducial-only DXF still triggers fallback
- **WHEN** a DXF has only FiducialCircle template matches (the original guard scenario)
- **AND** classification mis-labels the 4 corner fiducials as BGABall via
  the tight-grid heuristic
- **AND** `per_class_pre[BGABall] = 4 < 8`
- **AND** every pool instance has `original_class == "FiducialCircle"`
  (`default_in_pool == True`)
- **THEN** fallback SHALL trigger and reassign all 4 to FiducialCircle
- **AND** the precondition introduced by this change does NOT weaken
  this safety net

### Requirement: Integration with Match JSON serialisation

The arbitration step SHALL run inside the Match JSON serialiser
(`app/main.py:save_match_json`) **after** all per-class matching loops
and the view-prefix split (`split_matches_by_side`) have produced the
initial `out` dictionary, and **before** the dictionary is written to
disk.

For each arbitration group, the step SHALL:

1. Pool every instance keyed under any member class — across **all view
   prefixes** (`top_view.<member>.*`, `bottom_view.<member>.*`,
   `side_view.<member>.*`, and the unprefixed `<member>.*` fallback).
   Instances that share the same set of handles across multiple member
   keys (matcher cross-fire — the bug this capability exists to resolve)
   SHALL be deduplicated to one pool entry per physical instance.
2. Run pitch derivation + neighbour-count classification +
   population fallback over that pooled set.
3. Re-key each instance so it appears under **exactly one** member
   class in the output, preserving its original view prefix.

When an instance changes class, its view prefix SHALL NOT change. Per
existing template-library rules, the view-constraint registry SHALL
re-validate the new class against the prefix; instances whose new class
disallows that view SHALL be **dropped** (not reassigned to a third
class) and counted in `arbitration_counts.dropped_by_view`.

The `save_match_json` response payload SHALL include an
`arbitration_counts` field with the per-group breakdown:

```json
{
  "arbitration_counts": {
    "BGABall|FiducialCircle": {
      "pool_size": 100,
      "derived_pitch": 0.402,
      "assigned": {"BGABall": 96, "FiducialCircle": 4},
      "reassigned_from_match": 4,
      "population_fallback_triggered": false,
      "dropped_by_view": 0
    }
  }
}
```

The group key SHALL be the sorted member names joined by `|`.

#### Scenario: Arbitration runs once after per-class matching completes
- **WHEN** `save_match_json` is invoked on a file whose library has
  `BGABall` and `FiducialCircle` configured
- **AND** the per-class matching loop has populated `out` with raw
  matches for both classes
- **THEN** the arbitration step runs exactly once for the
  `{"BGABall", "FiducialCircle"}` group
- **AND** the resulting `out` dictionary has each handle keyed under at
  most one member class

#### Scenario: Identical-size circles split into BGA grid + fiducials
- **GIVEN** a DXF containing a 10×10 grid of circles at pitch 0.4 mm
  in `bottom_view` and 4 isolated circles of the same diameter in
  `top_view` (substrate corners)
- **WHEN** `save_match_json` runs
- **THEN** `bottom_view.bga_ball.*` contains 100 instances
- **AND** `top_view.fiducial_circle.*` contains 4 instances
- **AND** no handle appears under both classes
- **AND** the response's `arbitration_counts` reports
  `assigned == {"BGABall": 100, "FiducialCircle": 4}`

#### Scenario: Reassignment respects view constraints
- **GIVEN** a `BGABall` candidate that arbitration reassigns to
  `FiducialCircle`
- **AND** `FiducialCircle` has no view constraint registered
- **THEN** the instance retains its original view prefix
  (e.g., `bottom_view.fiducial_circle.0`)

- **AND WHEN** instead arbitration reassigns a `top_view`-prefixed
  instance from `FiducialCircle` to `BGABall`
- **AND** `CLASS_VIEW_CONSTRAINTS["BGABall"] == {"bottom_view", "side_view"}`
- **THEN** the instance SHALL be dropped (not emitted under any key)
- **AND** counted in `arbitration_counts...dropped_by_view`

#### Scenario: Arbitration is a no-op when no group's members are matched
- **WHEN** a library has only `Substrate` and `SMD` classes configured
  (neither is in any arbitration group)
- **THEN** `save_match_json` produces the same `out` dictionary it
  would have produced without the arbitration step
- **AND** `arbitration_counts` is an empty dict

### Requirement: Deterministic ordering

Arbitration SHALL produce byte-identical Match JSON across repeated
runs over the same input. To achieve this, the pooled centroid set
SHALL be ordered by `(view_prefix, original_class, original_instance_index,
sorted_handles_tuple)` before pitch derivation and classification, and
tie-breaks in neighbour counting (instances exactly at the search radius)
SHALL be handled by the same total order.

#### Scenario: Repeated invocations are byte-identical
- **WHEN** `save_match_json` runs twice over the same file with
  unchanged input
- **THEN** the two written Match JSON files are byte-identical
- **AND** the two `arbitration_counts` payloads are equal


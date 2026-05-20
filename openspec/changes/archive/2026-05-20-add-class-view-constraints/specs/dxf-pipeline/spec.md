## MODIFIED Requirements

### Requirement: Side-prefixed match JSON keys

The system SHALL emit each match instance into
`data/match/{file_id}.json` (written via `POST
/api/files/{file_id}/match-json`) under a key derived from its
bbox-center position relative to the file's three side rectangles,
using the following deterministic priority:

- center inside `top_view_rect` → key `top_view.<class>.<index>`
- else center inside `bottom_view_rect` → key `bottom_view.<class>.<index>`
- else center inside `side_view_rect` → key `side_view.<class>.<index>`
- else (center inside none, or all three rectangles unset) → key
  `<class>.<index>` (no prefix)

The `<class>` token SHALL be the snake_case match-JSON key form
defined by `library.CLASS_JSON_KEY` (see "Per-file Match JSON
export" and the `template-library` capability). The view-prefix
segments (`top_view`, `bottom_view`, `side_view`) are themselves
already snake_case and are unaffected by this change.

The bbox center SHALL be computed from the combined bounding box of
all entity point arrays in the match instance.

Instances of the same `<class>.<index>` template that fall in
different rectangles SHALL be split into the corresponding view-
prefixed keys in the same output file.

**Class-view constraint filter.** Before a key is emitted, the
serialiser SHALL consult `library.is_allowed_view(class_name,
view)` (see `template-library` capability) where `class_name` is
the **display ID** and `view` is one of `"top_view"`,
`"bottom_view"`, `"side_view"`, or `None` (the unassigned position
above). When the helper returns `False`, the instance SHALL be
dropped: not emitted under any key. Surviving counts SHALL be
exposed in the endpoint response as
`side_counts = {"top_view", "bottom_view", "side_view",
"unassigned", "dropped"}` where `"dropped"` is the aggregate count
of class-view-constraint violations.

**Skip-when-impossible optimisation.** For any class with an entry
in `library.CLASS_VIEW_CONSTRAINTS`, when *every* allowed view
rectangle for that class is `None` on the file
(e.g., `C4Ball` on a file whose `top_view_rect is None`), the
serialiser MAY skip the call to `find_matches_from_pointsets` for
that class's templates entirely, because every produced instance
would be dropped. This SHALL be a pure performance optimisation:
the response SHALL be byte-identical to a run that did not skip,
and the test surface SHALL verify both paths agree.

#### Scenario: Top-view-only file with all three regions drawn
- **WHEN** the file has three valid rectangles and all `smd_2t.0` match instances' bbox centers lie inside `top_view_rect`
- **THEN** the saved JSON contains key `top_view.smd_2t.0` with every instance
- **AND** the JSON does not contain `bottom_view.smd_2t.0`, `side_view.smd_2t.0`, or unprefixed `smd_2t.0`

#### Scenario: Instances split across all three views
- **WHEN** a class `smd_2t.0` has 15 instances: 7 in `top_view_rect`, 5 in `bottom_view_rect`, 3 in `side_view_rect`
- **THEN** the saved JSON contains `top_view.smd_2t.0` (7 instances), `bottom_view.smd_2t.0` (5), and `side_view.smd_2t.0` (3)
- **AND** the unprefixed key `smd_2t.0` does not appear

#### Scenario: Overlap priority resolves to top_view
- **WHEN** `top_view_rect` and `side_view_rect` overlap and a match instance's bbox center lies inside both
- **THEN** that instance is emitted under `top_view.<class>.<index>`

#### Scenario: Overlap priority resolves to bottom_view when top is absent
- **WHEN** `top_view_rect` is null, `bottom_view_rect` and `side_view_rect` overlap, and a match instance's bbox center lies inside both
- **THEN** that instance is emitted under `bottom_view.<class>.<index>`

#### Scenario: No regions set leaves unconstrained keys unprefixed
- **WHEN** all three of `top_view_rect`, `bottom_view_rect`, and `side_view_rect` are null
- **THEN** the saved JSON keys for **unconstrained classes** (e.g., `smd_2t`, `substrate`) are `<class>.<index>` exactly as before any side regions were introduced
- **AND** the saved JSON does NOT contain any key for **constrained classes** (`c4_ball`, `bga_ball`), because their unassigned matches are dropped

#### Scenario: Instance outside all three rectangles is unprefixed for unconstrained class
- **WHEN** the file has at least one rectangle drawn but one match instance of an **unconstrained** class has its bbox center outside all three
- **THEN** that instance is emitted under the unprefixed key `<class>.<index>`

#### Scenario: Only side_view set, instance inside it
- **WHEN** only `side_view_rect` is set and a match instance's bbox center lies inside it
- **THEN** that instance is emitted under `side_view.<class>.<index>`

#### Scenario: C4Ball in top_view is kept
- **WHEN** the file has `top_view_rect` set and a `C4Ball` match's bbox center lies inside it
- **THEN** the saved JSON contains `top_view.c4_ball.<index>` with that instance
- **AND** `side_counts["top_view"]` includes this match

#### Scenario: C4Ball in bottom_view is dropped
- **WHEN** the file has `bottom_view_rect` set and a `C4Ball` match's bbox center lies inside it
- **THEN** the saved JSON does NOT contain `bottom_view.c4_ball.<index>` for that instance
- **AND** the saved JSON does NOT contain `c4_ball.<index>` unprefixed for that instance
- **AND** `side_counts["dropped"]` includes this match

#### Scenario: C4Ball with no top_view_rect set is dropped (skip-when-impossible)
- **WHEN** the file has `top_view_rect is None` and the library contains a `C4Ball` template
- **THEN** the saved JSON contains no `c4_ball` key (prefixed or unprefixed)
- **AND** the implementation MAY skip the matcher call for that template
- **AND** the result is byte-identical to a run that did not skip

#### Scenario: BGABall in bottom_view is kept
- **WHEN** the file has `bottom_view_rect` set and a `BGABall` match's bbox center lies inside it
- **THEN** the saved JSON contains `bottom_view.bga_ball.<index>` with that instance

#### Scenario: BGABall in side_view is kept
- **WHEN** the file has `side_view_rect` set and a `BGABall` match's bbox center lies inside it
- **THEN** the saved JSON contains `side_view.bga_ball.<index>` with that instance

#### Scenario: BGABall in top_view is dropped
- **WHEN** the file has `top_view_rect` set and a `BGABall` match's bbox center lies inside it
- **THEN** the saved JSON does NOT contain `top_view.bga_ball.<index>` for that instance
- **AND** `side_counts["dropped"]` includes this match

#### Scenario: BGABall with no bottom_view_rect and no side_view_rect is dropped
- **WHEN** the file has `bottom_view_rect is None` and `side_view_rect is None` (only `top_view_rect` is set, or none of the three)
- **AND** the library contains a `BGABall` template
- **THEN** the saved JSON contains no `bga_ball` key (prefixed or unprefixed)

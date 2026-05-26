## MODIFIED Requirements

### Requirement: Per-file Match JSON export

`POST /api/files/{file_id}/match-json` SHALL produce a Match JSON of
the form `{"<class>.<template-index>": [[handle, ...], ...]}` over the
file's library and SHALL persist it to `data/match/{file_id}.json`.

The `<class>` token in every key SHALL be the **match-JSON key** form
defined by `library.CLASS_JSON_KEY` (see the `template-library`
capability), i.e. the snake_case / identifier-safe form derived from
the class's display ID. The viewer's per-class display label (which
uses the CamelCase display ID) SHALL be unaffected — only the
persisted JSON key changes.

For a class without an entry in `CLASS_JSON_KEY` (custom classes
added by the user), the `<class>` token SHALL be the display ID
verbatim.

#### Scenario: Single-entity template export
- **WHEN** a file's library has a `BGABall` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `bga_ball.0`
- **AND** every match in `bga_ball.0` is a single-handle list

#### Scenario: Multi-entity template export
- **WHEN** a file's library has a `SMD-2T` template composed of 3 entities at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `smd_2t.0`
- **AND** every match in `smd_2t.0` is a 3-handle list

#### Scenario: Substrate export uses snake_case key
- **WHEN** a file's library has a `Substrate` template at index 0
  and the file has no side regions drawn
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `substrate.0`
- **AND** the response does NOT include the key `Substrate.0`

#### Scenario: Custom class key passes through verbatim
- **WHEN** a library has a user-added class `MyMarker` with one template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `MyMarker.0` (no case-folding)

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

#### Scenario: No regions set leaves keys unprefixed
- **WHEN** all three of `top_view_rect`, `bottom_view_rect`, and `side_view_rect` are null
- **THEN** the saved JSON keys are `<class>.<index>` exactly as before any side regions were introduced

#### Scenario: Instance outside all three rectangles is unprefixed
- **WHEN** the file has at least one rectangle drawn but one match instance's bbox center is outside all three
- **THEN** that instance is emitted under the unprefixed key `<class>.<index>`

#### Scenario: Only side_view set, instance inside it
- **WHEN** only `side_view_rect` is set and a match instance's bbox center lies inside it
- **THEN** that instance is emitted under `side_view.<class>.<index>`

## ADDED Requirements

### Requirement: Per-file side regions persistence

The system SHALL persist, per uploaded file, two optional
axis-aligned world-space rectangles: `frontside_rect` and
`bottomside_rect`. Each rectangle SHALL be stored as JSON
`{"x0":..,"y0":..,"x1":..,"y1":..}` with `x0<=x1` and `y0<=y1` after
normalisation. Either or both SHALL be allowed to be unset. The
rectangles SHALL be reachable via `GET /api/files/{file_id}` (included
in the file record JSON) and writable via `PATCH
/api/files/{file_id}/side-regions` with body
`{"frontside_rect": <rect|null>, "bottomside_rect": <rect|null>}`.

Switching the file's library, re-running preprocess, or editing the
selected layers SHALL NOT clear the side rectangles.

#### Scenario: PATCH stores both rectangles
- **WHEN** the user PATCHes `/api/files/{id}/side-regions` with both rectangles
- **THEN** the file record returns both rectangles on subsequent GETs
- **AND** the values are normalised so `x0<=x1` and `y0<=y1`

#### Scenario: PATCH clears one side independently
- **WHEN** the user PATCHes with `frontside_rect: null` and leaves bottomside as-is
- **THEN** the frontside rectangle is unset
- **AND** the bottomside rectangle is unchanged

#### Scenario: Library swap preserves regions
- **WHEN** the user changes the file's library
- **THEN** the file's `frontside_rect` and `bottomside_rect` are unchanged

### Requirement: Side-prefixed match JSON keys

The system SHALL emit each match instance into
`data/match/{file_id}.json` (written via `POST
/api/files/{file_id}/match-json`) under a key derived from its
bbox-center position relative to the file's side rectangles:

- center inside `frontside_rect` → key `frontside.<class>.<index>`
- center inside `bottomside_rect` → key `bottomside.<class>.<index>`
- center inside both (rectangles overlap) → key
  `frontside.<class>.<index>` (deterministic tiebreak)
- center inside neither, or both rectangles unset → key
  `<class>.<index>` (no prefix)

The bbox center SHALL be computed from the combined bounding box of
all entity point arrays in the match instance.

Instances of the same `<class>.<index>` template that fall in
different sides SHALL be split into the corresponding side-prefixed
keys in the same output file.

#### Scenario: Frontside-only file with both regions drawn
- **WHEN** the file has two valid rectangles, and all `smd.0` match instances' bbox centers lie inside `frontside_rect`
- **THEN** the saved JSON contains key `frontside.smd.0` with every instance
- **AND** the JSON does not contain the unprefixed key `smd.0`

#### Scenario: Instances split across sides
- **WHEN** a class `smd.0` has 12 instances, 7 with bbox centers in `frontside_rect` and 5 in `bottomside_rect`
- **THEN** the saved JSON contains `frontside.smd.0` (7 instances) and `bottomside.smd.0` (5 instances)
- **AND** the unprefixed key `smd.0` does not appear

#### Scenario: No regions set leaves keys unprefixed
- **WHEN** both `frontside_rect` and `bottomside_rect` are null
- **THEN** the saved JSON keys are `<class>.<index>` exactly as before this change

#### Scenario: Instance outside both rectangles is unprefixed
- **WHEN** the file has rectangles drawn but one match instance's bbox center is outside both
- **THEN** that instance is emitted under the unprefixed key `<class>.<index>`

### Requirement: Side-region edits invalidate saved match

The server SHALL delete the cached `data/match/{file_id}.json` (if
present) and reset the file's `match_saved` flag to `0` whenever
`PATCH /api/files/{file_id}/side-regions` changes either rectangle,
so the engineer re-runs Save Match. The response SHALL include the
updated `match_saved` value so the dashboard can refresh.
`data/prematch/{file_id}.json` is not side-aware (it's a flat
per-class handle list used for the viewer's colored overlay) and is
left untouched.

#### Scenario: Editing regions clears the saved match
- **WHEN** the user PATCHes the side regions and the file previously had `match_saved = 1`
- **THEN** `match_saved` becomes `0`
- **AND** `data/match/{file_id}.json` no longer exists on disk

## MODIFIED Requirements

### Requirement: Per-file side regions persistence

The system SHALL persist, per uploaded file, three optional
axis-aligned world-space rectangles: `top_view_rect`,
`bottom_view_rect`, and `side_view_rect`. Each rectangle SHALL be
stored as JSON `{"x0":..,"y0":..,"x1":..,"y1":..}` with `x0<=x1` and
`y0<=y1` after normalisation. Any subset (including all three, any
two, any one, or none) SHALL be allowed. The rectangles SHALL be
reachable via `GET /api/files/{file_id}` (included in the file record
JSON) and writable via `PATCH /api/files/{file_id}/side-regions` with
body `{"top_view_rect": <rect|null>, "bottom_view_rect": <rect|null>, "side_view_rect": <rect|null>}`.

Switching the file's library, re-running preprocess, or editing the
selected layers SHALL NOT clear any of the side rectangles.

On first server start after this change, the `files` table SHALL be
migrated by renaming `frontside_rect` to `top_view_rect`, renaming
`bottomside_rect` to `bottom_view_rect`, and adding the new
`side_view_rect` column. The migration SHALL be idempotent.

#### Scenario: PATCH stores all three rectangles
- **WHEN** the user PATCHes `/api/files/{id}/side-regions` with all three rectangles
- **THEN** the file record returns all three rectangles on subsequent GETs
- **AND** the values are normalised so `x0<=x1` and `y0<=y1`

#### Scenario: PATCH clears one side independently
- **WHEN** the user PATCHes with `top_view_rect: null` and leaves the other two as-is
- **THEN** the `top_view` rectangle is unset
- **AND** the `bottom_view_rect` and `side_view_rect` are unchanged

#### Scenario: PATCH sets only side_view
- **WHEN** the user PATCHes with only `side_view_rect` populated and the other two as `null`
- **THEN** the file record returns `side_view_rect` populated and the other two as `null`

#### Scenario: Library swap preserves regions
- **WHEN** the user changes the file's library
- **THEN** the file's three rectangles are unchanged

#### Scenario: Migration renames pre-existing columns
- **WHEN** the server starts against a DB whose `files` table has the old `frontside_rect` and `bottomside_rect` columns and no `side_view_rect`
- **THEN** after migration the table has `top_view_rect`, `bottom_view_rect`, and `side_view_rect`
- **AND** any existing values in `frontside_rect` are now under `top_view_rect`
- **AND** any existing values in `bottomside_rect` are now under `bottom_view_rect`

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

The bbox center SHALL be computed from the combined bounding box of
all entity point arrays in the match instance.

Instances of the same `<class>.<index>` template that fall in
different rectangles SHALL be split into the corresponding view-
prefixed keys in the same output file.

#### Scenario: Top-view-only file with all three regions drawn
- **WHEN** the file has three valid rectangles and all `smd.0` match instances' bbox centers lie inside `top_view_rect`
- **THEN** the saved JSON contains key `top_view.smd.0` with every instance
- **AND** the JSON does not contain `bottom_view.smd.0`, `side_view.smd.0`, or unprefixed `smd.0`

#### Scenario: Instances split across all three views
- **WHEN** a class `smd.0` has 15 instances: 7 in `top_view_rect`, 5 in `bottom_view_rect`, 3 in `side_view_rect`
- **THEN** the saved JSON contains `top_view.smd.0` (7 instances), `bottom_view.smd.0` (5), and `side_view.smd.0` (3)
- **AND** the unprefixed key `smd.0` does not appear

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

### Requirement: Side-region edits invalidate saved match

The server SHALL delete the cached `data/match/{file_id}.json` (if
present) and reset the file's `match_saved` flag to `0` whenever
`PATCH /api/files/{file_id}/side-regions` changes any of the three
rectangles, so the engineer re-runs Save Match. The response SHALL
include the updated `match_saved` value so the dashboard can refresh.
`data/prematch/{file_id}.json` is not side-aware (it's a flat
per-class handle list used for the viewer's colored overlay) and
SHALL be left untouched.

#### Scenario: Editing any region clears the saved match
- **WHEN** the user PATCHes the side regions (changing any of the three rectangles, including setting one to null) and the file previously had `match_saved = 1`
- **THEN** `match_saved` becomes `0`
- **AND** `data/match/{file_id}.json` no longer exists on disk

#### Scenario: Editing only side_view clears the saved match
- **WHEN** the user PATCHes with only `side_view_rect` changing (the other two are unchanged) and the file previously had `match_saved = 1`
- **THEN** `match_saved` becomes `0`
- **AND** `data/match/{file_id}.json` no longer exists on disk

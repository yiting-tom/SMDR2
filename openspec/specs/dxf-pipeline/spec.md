# dxf-pipeline Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
### Requirement: Server-side DXF flatten

The system SHALL parse uploaded DXF files server-side using ezdxf and
emit JSON-serialisable drawing primitives. The allowed `type` values
SHALL be `line`, `polyline`, `filled_polygon`, `point`, and `circle`.
Non-circular curves SHALL be flattened to polylines with a
per-file flatten tolerance derived from the file's modelspace bbox
diagonal so vertex count stays bounded across pathological unit
scales. The tolerance SHALL be `max(BASE_TOLERANCE, diagonal *
SCALE_FACTOR)` with `BASE_TOLERANCE = 0.01` drawing units and
`SCALE_FACTOR = 1e-5`. Files whose extents cannot be determined SHALL
fall back to `BASE_TOLERANCE`. Circular sub-paths produced by
`Frontend.draw_path` (typically CIRCLE entities and 360° CIRCULAR-ARC
entities) SHALL be emitted as a `circle` primitive carrying
`center: [x, y]` and `r: float` instead of being flattened to a
closed polyline.

Filled circular regions reaching `Frontend.draw_filled_paths` (for
example, a HATCH entity whose only boundary is a circular edge) SHALL
likewise be emitted as a single `circle` primitive — `center` + `r`
plus an additional `filled: true` flag — instead of as a
`filled_polygon` carrying the flattened boundary ring, **provided
the call satisfies all of**: exactly one input path, exactly one
sub-path, `sub.is_closed`, `getattr(sub, "has_curves", False)`, and a
positive detection from the same circle predicate used by
`draw_path`. When any one of those conditions fails (multi-path
HATCH, multi-sub-path HATCH with holes, polyline-only sub-path that
happens to be near-circular, etc.), the system SHALL fall back to
the existing `filled_polygon` emit.

Stroke-only circles emitted from `draw_path` SHALL continue to omit
the `filled` field (equivalent to `filled: false`); the field is
strictly additive and OPTIONAL on the `circle` primitive shape.

The detection predicate SHALL require at least 8 candidate vertices
and a radial variance `(rmax - rmin) / rmean ≤ 0.02`, identical for
the `draw_path` and `draw_filled_paths` code paths. Each primitive
SHALL carry the source DXF entity handle so the matching engine and
frontend can resolve back to the original entity. When the chosen
tolerance differs from `BASE_TOLERANCE`, the system SHALL emit one
info-level log line recording the diagonal and the chosen tolerance.

#### Scenario: Flatten the bundled sample
- **WHEN** `flatten_for_render("data/test.dxf")` is called
- **THEN** the result contains at least one primitive
- **AND** every primitive's `type` is one of `line / polyline / filled_polygon / point / circle`
- **AND** every primitive carries a non-empty `handle`
- **AND** the result's `bbox` and `background` fields are populated

#### Scenario: Normal-scale file uses the base tolerance
- **WHEN** a DXF whose modelspace bbox diagonal is below 1000 drawing units is flattened
- **THEN** the effective flatten tolerance equals `BASE_TOLERANCE` (0.01)
- **AND** no tolerance-adjustment log line is emitted

#### Scenario: Oversized-scale file relaxes the tolerance
- **WHEN** a DXF whose modelspace bbox diagonal is 100_000 drawing units is flattened
- **THEN** the effective flatten tolerance equals `1.0` (= 100_000 × 1e-5)
- **AND** the number of primitives produced for an ELLIPSE entity in the file is comparable to the count the same entity would produce at unit-scale (within 2×)
- **AND** an info-level log line records the diagonal and the chosen tolerance

#### Scenario: File with no determinable extents falls back to base tolerance
- **WHEN** a DXF whose extents cannot be determined (empty modelspace, all entities outside ezdxf's fast-bbox support) is flattened
- **THEN** the effective flatten tolerance equals `BASE_TOLERANCE`
- **AND** flatten proceeds without raising

#### Scenario: A CIRCLE entity becomes a circle primitive
- **WHEN** a DXF containing a single CIRCLE entity (radius 0.15 mm) is flattened
- **THEN** the result contains a primitive with `type == "circle"`
- **AND** that primitive carries numeric `center` (length 2) and `r` matching the source CIRCLE within 1 % radial tolerance
- **AND** the result contains no closed polyline primitive for that handle
- **AND** the primitive's `filled` field is absent or falsey

#### Scenario: A HATCH bounded by a circle becomes a filled circle primitive
- **WHEN** a DXF containing a HATCH whose only boundary is a single circular edge (radius 0.30 mm) is flattened
- **THEN** the result contains exactly one primitive for the HATCH's handle with `type == "circle"` and `filled == true`
- **AND** that primitive's `center` and `r` match the source HATCH boundary within 1 % radial tolerance
- **AND** the result contains no `filled_polygon` primitive for that handle
- **AND** the primitive's `decorative` flag is `true` (HATCH is in `DECORATIVE_DXFTYPES`)

#### Scenario: A HATCH with a non-circular polyline boundary stays a filled_polygon
- **WHEN** a DXF containing a HATCH whose boundary is a polyline-only path (`has_curves == False`) is flattened
- **THEN** the result for that handle contains a `filled_polygon` primitive
- **AND** does NOT contain a `circle` primitive, even if the polyline vertices happen to lie on a circle

#### Scenario: A multi-sub-path HATCH (e.g., annulus) stays a filled_polygon
- **WHEN** a DXF containing a HATCH with an outer circular boundary and an inner circular hole is flattened
- **THEN** the result for that handle contains one `filled_polygon` primitive with two rings
- **AND** does NOT contain a `circle` primitive (the fast path requires exactly one sub-path)

#### Scenario: A true polyline stays a polyline
- **WHEN** a DXF containing an 8-vertex closed POLYLINE that is NOT a circular approximation is flattened
- **THEN** the result contains a `polyline` primitive (not a `circle`) for that handle
- **AND** the polyline's `points` list preserves the source vertices

#### Scenario: Index primitives by source DXF handle
- **WHEN** `build_handle_index(primitives)` is called over a flattened DXF
- **THEN** every entry maps a handle to the list of primitive indices for that entity
- **AND** the relation `primitives[idx]["handle"] == handle` holds for every (handle, idx)

### Requirement: Matcher consumes circle primitives via synthetic vertex sampling

`collect_entity_points` SHALL, for primitives of `type == "circle"`,
synthesize a deterministic, evenly-spaced sample of points around the
circle so the matching engine sees a point cloud equivalent to the
pre-change flattened-polyline representation. The number of samples N
SHALL be chosen as `max(8, min(64, round(2π·r / 0.01)))` so the
sampling density tracks the previous flattening tolerance and the
same input DXF always yields the same fingerprint.

#### Scenario: Circle primitive contributes points to the matcher
- **WHEN** a parsed file contains a `circle` primitive with `r = 0.15`
- **AND** `collect_entity_points` is invoked for that handle
- **THEN** the returned list has between 8 and 64 points
- **AND** each point lies within 1 % of radial distance `r` from `center`
- **AND** invoking the function again on the same primitive returns an identical list

### Requirement: Multi-file upload with deterministic file IDs

Users SHALL be able to upload one or more DXF files at the same time via
`POST /api/files`. Each accepted file SHALL receive a deterministic
`file_id` derived from the SHA-256 of its bytes (first 16 hex chars).
Re-uploading the same content SHALL deduplicate to the existing
`file_id` and skip re-processing if already ready.

#### Scenario: New DXF upload kicks off background processing
- **WHEN** a user uploads a previously-unseen `.dxf` file
- **THEN** the response contains a `file_id`, `status: "preprocessing"`, and a `job_id`
- **AND** a preprocess job is submitted to the worker pool

#### Scenario: Duplicate upload is deduplicated
- **WHEN** a user uploads bytes-identical content to a file already processed
- **THEN** the response carries `deduped: true` and `status: "ready_to_match"`
- **AND** no new preprocess job is submitted

#### Scenario: Non-DXF file is rejected
- **WHEN** a user uploads a file without a `.dxf` extension
- **THEN** the per-file response carries a `skipped` field with the reason
- **AND** no record is registered

### Requirement: File lifecycle status

Each uploaded file SHALL track exactly one status value at any time
from: `preprocessing`, `ready_to_match`, `checking_rules`, `report`,
`error`. Initial state SHALL be `preprocessing`; successful preprocess
SHALL transition to `ready_to_match`; preprocess failure SHALL
transition to `error` with the captured exception in `error`.

#### Scenario: Successful preprocess
- **WHEN** the preprocess worker returns successfully for a file
- **THEN** the file's status becomes `ready_to_match`
- **AND** `parsed_at`, `primitive_count`, `bbox`, and `background` are populated

#### Scenario: Preprocess failure
- **WHEN** the preprocess worker raises an exception
- **THEN** the file's status becomes `error`
- **AND** the `error` field captures the exception message and traceback

### Requirement: Background pre-processing with pre-match

For every uploaded file the system SHALL run a background pipeline that
parses the DXF, builds the entity shape index, runs scan-all against
the file's library snapshot, and persists the parsed primitives and the
pre-match handle-by-class to disk under `data/parsed/{file_id}.json`
and `data/prematch/{file_id}.json`.

#### Scenario: Pre-match against an empty library
- **WHEN** preprocessing completes for a file whose library has no templates
- **THEN** `data/prematch/{file_id}.json` exists with `{by_class: {}, total: 0}`

#### Scenario: Pre-match against a populated library
- **WHEN** preprocessing completes for a file whose library has at least one template
- **THEN** `data/prematch/{file_id}.json` contains handles grouped by class
- **AND** the totals match the sum of unique handles across classes

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

### Requirement: Process-pool worker isolation

DXF parsing SHALL run in a child process via `ProcessPoolExecutor` so
the FastAPI event loop is never blocked. Worker count SHALL default to
2 and be controlled via `MAX_WORKERS` in `app/jobs.py`. Worker pool
SHALL be shut down cleanly on application shutdown.

#### Scenario: Concurrent uploads don't block the API
- **WHEN** two large DXFs are uploaded back-to-back
- **THEN** both `POST /api/files` responses return promptly
- **AND** the preprocess jobs execute in parallel across the worker pool

### Requirement: RenderOutput carries source DXF $INSUNITS

`flatten_for_render` SHALL extract the source DXF's `$INSUNITS` header
value (`doc.header.get("$INSUNITS")`) and expose it on `RenderOutput`
as a nullable integer. The value SHALL be returned verbatim with no
remapping; consumers downstream are responsible for interpreting the
DXF spec enum (0 = unitless, 1 = inch, 2 = foot, 4 = mm, 5 = cm,
6 = m, …). When the header is missing or unparseable, the field SHALL
be `None`.

#### Scenario: A DXF with INSUNITS=4 (mm) is flattened
- **WHEN** a DXF whose header declares `$INSUNITS = 4` is flattened
- **THEN** `RenderOutput.insunits == 4`

#### Scenario: A DXF with no INSUNITS header is flattened
- **WHEN** a DXF whose header does not set `$INSUNITS` (or sets it to 0) is flattened
- **THEN** `RenderOutput.insunits` is `0` if explicitly set, else `None`

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


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
closed polyline. The detection predicate SHALL require at least 8
candidate vertices and a radial variance
`(rmax - rmin) / rmean ≤ 0.02`. Each primitive SHALL carry the source
DXF entity handle so the matching engine and frontend can resolve
back to the original entity. When the chosen tolerance differs from
`BASE_TOLERANCE`, the system SHALL emit one info-level log line
recording the diagonal and the chosen tolerance.

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

#### Scenario: Single-entity template export
- **WHEN** a file's library has a `bga_ball` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `bga_ball.0`
- **AND** every match in `bga_ball.0` is a single-handle list

#### Scenario: Multi-entity template export
- **WHEN** a file's library has a `smd` template composed of 3 entities at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `smd.0`
- **AND** every match in `smd.0` is a 3-handle list

### Requirement: Process-pool worker isolation

DXF parsing SHALL run in a child process via `ProcessPoolExecutor` so
the FastAPI event loop is never blocked. Worker count SHALL default to
2 and be controlled via `MAX_WORKERS` in `app/jobs.py`. Worker pool
SHALL be shut down cleanly on application shutdown.

#### Scenario: Concurrent uploads don't block the API
- **WHEN** two large DXFs are uploaded back-to-back
- **THEN** both `POST /api/files` responses return promptly
- **AND** the preprocess jobs execute in parallel across the worker pool


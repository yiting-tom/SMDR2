# dxf-pipeline Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
### Requirement: Server-side DXF flatten

The system SHALL parse uploaded DXF files server-side using ezdxf and
emit JSON-serialisable drawing primitives (`line`, `polyline`,
`filled_polygon`, `point`). Curves SHALL be flattened to polylines with
a configurable deviation tolerance (default 0.01 in drawing units).
Each primitive SHALL carry the source DXF entity handle so the
matching engine and frontend can resolve back to the original entity.

#### Scenario: Flatten the bundled sample
- **WHEN** `flatten_for_render("data/test.dxf")` is called
- **THEN** the result contains at least one primitive
- **AND** every primitive's `type` is one of `line / polyline / filled_polygon / point`
- **AND** every primitive carries a non-empty `handle`
- **AND** the result's `bbox` and `background` fields are populated

#### Scenario: Index primitives by source DXF handle
- **WHEN** `build_handle_index(primitives)` is called over a flattened DXF
- **THEN** every entry maps a handle to the list of primitive indices for that entity
- **AND** the relation `primitives[idx]["handle"] == handle` holds for every (handle, idx)

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


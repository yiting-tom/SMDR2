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

Closed sub-paths produced by `Frontend.draw_path` SHALL be probed by
the circle detector **regardless of whether the sub-path carries
curve segments**. When `sub.has_curves == True` the detector SHALL
require at least `CIRCLE_MIN_VERTS = 8` candidate vertices; when
`sub.has_curves == False` (pure line segments — typically a closed
LWPOLYLINE / POLYLINE authored as an N-gon approximation of a
circle, as commonly seen for BGA balls in some packaging DXFs) the
detector SHALL require at least `CIRCLE_MIN_VERTS_NOCURVE = 11`
candidate vertices. The higher no-curves threshold protects
legitimate low-N polygon pads (squares, hexagons, octagons,
dodecagons) whose radial variance can coincidentally fall under
`CIRCLE_RADIAL_TOL`; the threshold is set to 11 because regular
polygons used as deliberate pad shapes in IC packaging
substantively use N ∈ {3, 4, 6, 8, 12} and never N=11.

Filled circular regions reaching `Frontend.draw_filled_paths` (for
example, a HATCH entity whose only boundary is a circular edge) SHALL
likewise be emitted as a single `circle` primitive — `center` + `r`
plus an additional `filled: true` flag — instead of as a
`filled_polygon` carrying the flattened boundary ring, **provided
the call satisfies all of**: exactly one input path, exactly one
sub-path, `sub.is_closed`, and a positive detection from the same
circle predicate used by `draw_path` (with the same dual-threshold:
`CIRCLE_MIN_VERTS = 8` for `has_curves == True`,
`CIRCLE_MIN_VERTS_NOCURVE = 11` for `has_curves == False`). When any
one of those conditions fails (multi-path HATCH, multi-sub-path
HATCH with holes, sub-path whose vertex count is below the
applicable threshold, sub-path whose radial variance exceeds
tolerance, etc.), the system SHALL fall back to the existing
`filled_polygon` emit.

Stroke-only circles emitted from `draw_path` SHALL continue to omit
the `filled` field (equivalent to `filled: false`); the field is
strictly additive and OPTIONAL on the `circle` primitive shape.

The detection predicate SHALL require a radial variance
`(rmax - rmin) / rmean ≤ CIRCLE_RADIAL_TOL = 0.02`, identical for
the `draw_path` and `draw_filled_paths` code paths and identical
for the curves and no-curves cases. Each primitive SHALL carry the
source DXF entity handle so the matching engine and frontend can
resolve back to the original entity. When the chosen tolerance
differs from `BASE_TOLERANCE`, the system SHALL emit one info-level
log line recording the diagonal and the chosen tolerance.

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

#### Scenario: A pure-line LWPOLYLINE approximating a circle becomes a circle primitive
- **WHEN** a DXF containing a closed LWPOLYLINE with 24 vertices spaced uniformly on a circle (radius 0.15 mm, centre (3.0, 4.0)) is flattened
- **THEN** the result contains exactly one primitive for the LWPOLYLINE's handle with `type == "circle"` and `filled` absent or falsey
- **AND** the primitive's `center` and `r` match the source vertices within 1 % radial tolerance
- **AND** the result contains no `polyline` primitive for that handle

#### Scenario: A pure-line LWPOLYLINE at the boundary vertex count is promoted
- **WHEN** a DXF containing a closed LWPOLYLINE with exactly 11 vertices uniformly on a circle is flattened
- **THEN** the result contains a `circle` primitive for that handle
- **AND** does NOT contain a `polyline` primitive for that handle

#### Scenario: A pure-line LWPOLYLINE below the no-curves threshold stays a polyline
- **WHEN** a DXF containing a closed LWPOLYLINE with 10 vertices uniformly on a circle (i.e., a decagon) is flattened
- **THEN** the result contains a `polyline` primitive for that handle
- **AND** does NOT contain a `circle` primitive (vertex count is below `CIRCLE_MIN_VERTS_NOCURVE`)

#### Scenario: A HATCH bounded by a pure-line LWPOLYLINE circle becomes a filled circle primitive
- **WHEN** a DXF containing a HATCH whose only boundary is a closed LWPOLYLINE with 24 vertices uniformly on a circle (radius 0.30 mm) is flattened
- **THEN** the result contains exactly one primitive for the HATCH's handle with `type == "circle"` and `filled == true`
- **AND** the primitive's `decorative` flag is `true`
- **AND** the result contains no `filled_polygon` primitive for that handle

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

Per-product upload (`POST /api/products/{product_id}/files`) SHALL
accept an optional form field `skip_layer_pick: bool` (default
`false`). When set to `true`:

- The handler SHALL NOT submit `_discover_layers_worker` (Phase 1).
- The handler SHALL submit `_preprocess_worker` (Phase 2) directly
  with `selected_layers=None`, which the worker already treats as
  "no layer filter — keep every primitive".
- The handler SHALL register the new file row with
  `initial_status = PREPROCESSING`.
- The file SHALL never enter the `discovering_layers` or
  `awaiting_layers` lifecycle states for this upload — those
  states are skipped entirely.
- Layer-manifest JSON (`data/layer_preview/{file_id}/layers.json`)
  and per-layer SVG thumbnails SHALL NOT be written for this
  upload. (If a prior non-skip upload of the same `file_id`
  already wrote them, they are left on disk untouched but unused.)

When `skip_layer_pick` is absent or `false`, the upload behaves
exactly as before: Phase 1 is submitted, the file lands at
`awaiting_layers`, and the operator picks layers via the existing
`POST /api/files/{file_id}/layers` endpoint.

For the dedup-rebind branch (re-upload of bytes-identical content
to a different product slot), the `skip_layer_pick` flag SHALL be
honoured the same way: the existing row's `status` is set to
`PREPROCESSING`, `selected_layers` is reset to `NULL`, and Phase 2
is submitted directly. The dedup case without the flag continues
to set `status = DISCOVERING_LAYERS` and re-run Phase 1.

The server SHALL NOT validate dev-mode origin of the flag. The
flag is honoured unconditionally on any incoming request; gating
the affordance is a UI responsibility (see the `viewer-ui`
capability's `Skip layer picker dev affordance` requirement).

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

#### Scenario: skip_layer_pick=true bypasses Phase 1 entirely
- **WHEN** a user uploads a previously-unseen `.dxf` to
  `POST /api/products/{pid}/files` with form field
  `skip_layer_pick=true`
- **THEN** the response carries `status: "preprocessing"` and a `job_id`
- **AND** the job submitted is `_preprocess_worker`, not
  `_discover_layers_worker`
- **AND** `selected_layers` on the registered row is `NULL`
- **AND** the file never transitions through `discovering_layers`
  or `awaiting_layers`
- **AND** `data/layer_preview/{file_id}/layers.json` is not written

#### Scenario: skip_layer_pick=false (or absent) keeps the existing Phase 1 path
- **WHEN** a user uploads a `.dxf` to
  `POST /api/products/{pid}/files` with `skip_layer_pick=false`
  or with the field omitted
- **THEN** the response carries `status: "discovering_layers"`
- **AND** the job submitted is `_discover_layers_worker`
- **AND** the layer manifest is rendered as today

#### Scenario: skip_layer_pick=true on dedup-rebind reuses the row through Phase 2
- **WHEN** a user re-uploads bytes-identical content to a different
  product slot with `skip_layer_pick=true`
- **AND** the existing row is in `awaiting_layers` or any other
  pre-`ready_to_match` state
- **THEN** the row's `status` is set to `preprocessing` and
  `selected_layers` is reset to `NULL`
- **AND** `_preprocess_worker` is submitted with
  `selected_layers=None`
- **AND** Phase 1 is not re-run

### Requirement: File lifecycle status

Each uploaded file SHALL track exactly one status value at any time
from: `discovering_layers`, `awaiting_layers`, `preprocessing`,
`ready_to_match`, `checking_rules`, `report`, `error`.

The default upload path takes a file through
`discovering_layers` (during Phase 1) → `awaiting_layers` (after
Phase 1, waiting for the operator's layer pick) → `preprocessing`
(Phase 2) → `ready_to_match` on success, or `error` on any
worker failure.

The dev-mode skip path (see the `Multi-file upload with
deterministic file IDs` requirement's `skip_layer_pick` field)
takes a file through `preprocessing` → `ready_to_match`
directly, skipping `discovering_layers` and `awaiting_layers`
entirely.

In both paths, preprocess failure SHALL transition the file to
`error` with the captured exception in `error`.

The `error` field SHALL capture either:
- a single exception message and traceback (the historical case,
  e.g. an OS error or a downstream pipeline failure), **or**
- a combined strict + recover exception string when both DXF parse
  paths failed (see `DXF parsing uses strict-first with recover
  fallback`).

The `dxf_recover_notes` field SHALL be populated independently of
the lifecycle status: a file may reach `ready_to_match` with
non-null `dxf_recover_notes` (the recover path succeeded), or
`error` with null `dxf_recover_notes` (recover did not save it, or
the failure was not DXF-parse related).

#### Scenario: Successful preprocess
- **WHEN** the preprocess worker returns successfully for a file
- **THEN** the file's status becomes `ready_to_match`
- **AND** `parsed_at`, `primitive_count`, `bbox`, and `background` are populated

#### Scenario: Preprocess failure
- **WHEN** the preprocess worker raises an exception
- **THEN** the file's status becomes `error`
- **AND** the `error` field captures the exception message and traceback

#### Scenario: Ready file may carry recover notes
- **WHEN** a file's preprocess succeeds via the recover fallback
- **THEN** the file's status is `ready_to_match`
- **AND** `FileRecord.dxf_recover_notes` is a non-null dict carrying
  the audit summary

#### Scenario: Skip-layer-pick path bypasses layer-related statuses
- **WHEN** a file is uploaded with `skip_layer_pick=true`
- **THEN** the file's status transitions are
  `preprocessing` → `ready_to_match` (or `error`)
- **AND** the status never reads `discovering_layers` or
  `awaiting_layers` for this file's upload

### Requirement: DXF parsing uses strict-first with recover fallback

The system SHALL open user-uploaded DXF files by first calling
`ezdxf.readfile` (strict). When that call succeeds the parser
SHALL proceed exactly as before — there is no change to the
downstream flatten / circle-detection / bbox path. When the strict
call raises any of ezdxf's parser exception classes
(`ezdxf.DXFStructureError`, `ezdxf.DXFTagError`, or any subclass of
`ezdxf.DXFError` raised inside `readfile`), the parser SHALL fall
back to `ezdxf.recover.readfile` and continue with the recovered
`(doc, auditor)`. Non-parser exceptions (`FileNotFoundError`,
`PermissionError`, OS-level IO errors) SHALL NOT trigger the
fallback and SHALL propagate unchanged.

When the recover path is taken, the parser SHALL:
- Emit a `WARNING`-level server log carrying the file id (or path
  when no id is available yet), the strict-mode exception class
  name and message, and an Auditor summary
  (`n_fixed`, `n_unrecoverable`, and the first ≤ 5 audit
  messages).
- Persist that summary as a JSON-serialisable dict on the
  `FileRecord.dxf_recover_notes` field. The dict's shape SHALL be:
  `{"strict_error": "<ExceptionClassName>: <msg>",
    "n_fixed": <int>, "n_unrecoverable": <int>,
    "audit_messages": ["<msg>", …]}`.
  Files that succeed via strict SHALL leave the field `null`.

When both strict and recover raise, the parser SHALL re-raise an
exception whose message includes the strict exception (class +
message) and the recover exception (class + message) separated by
a marker (`" | recover: "` or equivalent), and the worker's
exception handler SHALL log it at `ERROR` level. The file SHALL
transition to the `error` lifecycle status with that combined
message captured in `FileRecord.error`.

Numerical output for files that succeed via strict SHALL be
byte-identical to the prior behaviour. Files that succeed via
recover SHALL produce the geometric output ezdxf's recover yields;
the system makes no claim that recovered geometry matches what a
hypothetical strict parse would have produced — by definition the
strict parse did not produce one.

#### Scenario: Strict-OK file leaves recover notes null
- **WHEN** an uploaded DXF parses successfully via `ezdxf.readfile`
- **THEN** the resulting `FileRecord.dxf_recover_notes` is `null`
- **AND** no `WARNING` log line is emitted for the upload

#### Scenario: Recover-OK file populates recover notes and logs WARNING
- **WHEN** an uploaded DXF raises `DXFStructureError` from
  `ezdxf.readfile` and is then parsed successfully via
  `ezdxf.recover.readfile`
- **THEN** the file's status reaches `ready_to_match` as normal
- **AND** `FileRecord.dxf_recover_notes` is a dict containing
  `strict_error`, `n_fixed`, `n_unrecoverable`, and
  `audit_messages` (≤ 5 entries)
- **AND** the server log contains a single `WARNING` line
  identifying the file and quoting the strict exception + audit
  counts

#### Scenario: Both-fail file reaches error status with combined detail
- **WHEN** an uploaded DXF raises `DXFStructureError` from
  `ezdxf.readfile` and `ezdxf.recover.readfile` also raises
- **THEN** the file's status becomes `error`
- **AND** `FileRecord.error` contains both exception class names
  and messages (strict and recover) in a single string
- **AND** the server log contains an `ERROR` line covering both
  exceptions
- **AND** `FileRecord.dxf_recover_notes` is `null`

#### Scenario: Non-parser exception is not recovered
- **WHEN** opening an uploaded DXF raises `FileNotFoundError` (the
  file was deleted between upload registration and worker start)
- **THEN** the parser SHALL NOT call `ezdxf.recover.readfile`
- **AND** the file's status becomes `error` with the original
  exception captured in `FileRecord.error`

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

`POST /api/files/{file_id}/match-json` SHALL submit a Match JSON
build job to the shared `ProcessPoolExecutor` and return **HTTP 202
with body `{"job_id": "<uuid>", "file_id": "<file_id>"}`** as soon
as the job is queued. Fast pre-flight checks (file record exists,
status is past `awaiting_layers`, `data/parsed/{file_id}.json`
exists on disk, the file's library is registered) SHALL run inside
the request handler so unrecoverable inputs still return a
synchronous 4xx/5xx without producing a job. The handler SHALL NOT
mutate `file.match_saved` itself.

The worker SHALL produce a Match JSON of the form
`{"<class>.<template-index>": [[handle, ...], ...]}` over the
file's library and SHALL persist it to `data/match/{file_id}.json`.
The on-disk shape and key form are unchanged from the previous
synchronous behaviour.

The `<class>` token in every key SHALL be the **match-JSON key**
form defined by `library.CLASS_JSON_KEY` (see the `template-library`
capability), i.e. the snake_case / identifier-safe form derived
from the class's display ID. The viewer's per-class display label
(which uses the CamelCase display ID) SHALL be unaffected — only
the persisted JSON key changes. For a class without an entry in
`CLASS_JSON_KEY` (custom classes added by the user), the `<class>`
token SHALL be the display ID verbatim.

When the worker completes successfully, the job's done callback
SHALL set `FILE_STORE.set_match_saved(file_id, True)` and SHALL
store the summary payload — `template_keys`, `total_matches`,
`side_counts`, `arbitration_counts`, `saved_to`, `file_id`,
`library_id`, `match_saved` — under `job.result`. This payload's
field set SHALL match the previous synchronous response body 1:1
so callers that already consume those fields work after they
switch from reading the POST body to reading `GET /api/jobs/{job_id}`.

When the worker raises, the job's done callback SHALL record
`job.status = "error"` and `job.error` SHALL be a non-empty
diagnostic string. `file.match_saved` SHALL NOT flip and
`data/match/{file_id}.json` SHALL NOT be considered valid; any
partial file written during the failed run is treated as absent
by the rule-check submit gate (which checks `match_saved`).

The existing read endpoint `GET /api/files/{file_id}/match-json`
SHALL continue to serve the persisted JSON from disk (unchanged).

#### Scenario: POST returns 202 with a job id
- **WHEN** a file's library is non-empty and the user invokes
  `POST /api/files/{id}/match-json`
- **THEN** the response status is `202`
- **AND** the response body is `{"job_id": "<uuid>", "file_id": "<id>"}`
- **AND** the in-memory job dict carries an entry with status
  `queued` or `running`
- **AND** no `data/match/{id}.json` has been written yet
- **AND** the file's `match_saved` flag remains its prior value

#### Scenario: Job result mirrors the prior synchronous body
- **WHEN** the submitted job for a file reaches status `done`
- **THEN** `GET /api/jobs/{job_id}` returns a body where
  `result.template_keys`, `result.total_matches`,
  `result.side_counts`, `result.arbitration_counts`,
  `result.saved_to`, `result.match_saved` are present
- **AND** the field shapes match the prior synchronous response
  body
- **AND** `data/match/{file_id}.json` exists on disk
- **AND** the file's `match_saved` flag is `true`

#### Scenario: Worker error keeps match_saved false
- **WHEN** the submitted job for a file reaches status `error`
- **THEN** `GET /api/jobs/{job_id}` returns a body where `error`
  is a non-empty string
- **AND** the file's `match_saved` flag remains `false`
- **AND** the rule-check submit gate
  (`POST /api/products/{pid}/rule-check`) for any product binding
  this file as a role still rejects with the
  `"these roles still need Save Match"` 400 error

#### Scenario: Pre-flight failure short-circuits without a job
- **WHEN** the user invokes `POST /api/files/{id}/match-json` for
  a file whose `parsed/{file_id}.json` is missing on disk
- **THEN** the response status is `4xx`/`5xx` (as today)
- **AND** no entry is added to the in-memory job dict
- **AND** the user receives the failure synchronously, without
  needing to poll

#### Scenario: Single-entity template export
- **WHEN** a file's library has a `BGABall` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json` and
  the resulting job completes successfully
- **THEN** `result.template_keys` includes the key `bga_ball.0`
- **AND** every match in `bga_ball.0` is a single-handle list

#### Scenario: Multi-entity template export
- **WHEN** a file's library has a `SMD-2T` template composed of 3
  entities at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json` and
  the resulting job completes successfully
- **THEN** `result.template_keys` includes the key `smd_2t.0`
- **AND** every match in `smd_2t.0` is a 3-handle list

#### Scenario: Substrate export uses snake_case key
- **WHEN** a file's library has a `Substrate` template at index 0
  and the file has no side regions drawn
- **AND** the user invokes `POST /api/files/{id}/match-json` and
  the resulting job completes successfully
- **THEN** `result.template_keys` includes the key `substrate.0`
- **AND** `result.template_keys` does NOT include the key
  `Substrate.0`

#### Scenario: Custom class key passes through verbatim
- **WHEN** a library has a user-added class `MyMarker` with one
  template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json` and
  the resulting job completes successfully
- **THEN** `result.template_keys` includes the key `MyMarker.0`
  (no case-folding)

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

### Requirement: DXF preprocessing reads tunables from live module attributes

The DXF preprocessing pipeline in `app/dxf.py` SHALL resolve its
tunable thresholds (`BASE_TOLERANCE`, `CURVE_FLATTENING_DISTANCE`,
`CIRCLE_MIN_VERTS`, `CIRCLE_RADIAL_TOL`, `MAX_PRIMS_PER_THUMB`,
`MAX_VERTICES_PER_POLYLINE`) through module-attribute lookup at the
time each helper is called, not via values captured at import time.
This SHALL enable the developer-parameter override store to take
effect on subsequent preprocess calls without restart.

The change SHALL be a no-op at compiled default values: the rendering,
flattening, and circle-detection behaviour of an unmodified server
SHALL remain bit-identical to the prior implementation.

#### Scenario: Default behaviour unchanged
- **WHEN** the override store has not been touched since startup
- **THEN** preprocessing produces the same primitive payload as before this change for the same input DXF

#### Scenario: Override changes flatten tolerance for the next call
- **WHEN** the override store sets `BASE_TOLERANCE = 0.05`, then preprocess runs on a fresh DXF
- **THEN** flattened polylines use the new tolerance, and reverting the override restores the original

### Requirement: Re-preprocess all files job rebuilds primitives in-place

The pipeline SHALL expose an entry point invoked by
`POST /api/dev/reprocess-all` that re-runs preprocessing for every
file currently in storage using whatever tunables are live in the
module attribute table. For each file the job SHALL: read the
original DXF source from disk, run the same preprocess steps that
upload uses, overwrite the stored primitives and pre-match cache,
and update the file's lifecycle status the same way an upload would.
Saved Match JSONs SHALL NOT be deleted, even if their referenced
handles no longer appear in the re-extracted primitives.

For each file the job SHALL preserve that file's persisted scope and
unit decision exactly as a normal preprocess would: it SHALL re-apply
the file's `user_unit_override` (deriving the multiplier from the
override and skipping `detect_scale_factor`) when one is set, and SHALL
load the file's product-scoped templates for the pre-match step using
the file's `product_id`. A re-preprocess SHALL NOT re-run the
auto-detector on a file that carries an explicit `user_unit_override`.

#### Scenario: Re-preprocess walks every file
- **WHEN** the dev endpoint enqueues a reprocess-all job over 12 files
- **THEN** every file's stored primitives are rewritten exactly once and the job's progress counter reaches 12

#### Scenario: Saved Match JSONs survive re-preprocess
- **WHEN** a file with a saved Match JSON is re-preprocessed under a different `CIRCLE_MIN_VERTS`
- **THEN** the Match JSON file remains on disk (even if individual entries become orphaned in primitives)

#### Scenario: Re-preprocess re-applies a set unit override
- **WHEN** a unit-suspect file (its detector factor is non-`1.0`) has `user_unit_override == "mm"` (so `applied_scale == 1.0`) and is re-preprocessed by a reprocess-all job
- **THEN** the file's `applied_scale` remains `1.0` (the override's multiplier is re-applied, the detector is not consulted) and `user_unit_override` stays `"mm"`

### Requirement: Auto-rescale unit-suspect DXFs during preprocess

`flatten_for_render` SHALL multiply every flattened primitive
coordinate (and the recorded bbox) by a scale multiplier `M`
derived from a pure helper `detect_scale_factor(insunits,
bbox_diagonal) -> float`. `applied_scale` semantics: `rescaled_coord
= original_coord * M`. `M == 1.0` means no rescale.

`detect_scale_factor` SHALL return the first matching factor below:

| Case | Condition | Factor |
|---|---|---|
| Declared inch | `insunits == 1` | `25.4` |
| Declared cm   | `insunits == 5` | `10.0` |
| Declared m    | `insunits == 6` | `1000.0` |
| Declared mm   | `insunits == 4` | `1.0` |
| Unitless / unknown | `insunits ∈ {0, None}` | best power-of-10 (see below) |
| Otherwise     | unrecognised INSUNITS | `1.0` |

For the unitless / unknown path the function SHALL:

1. Consider candidate factors `[10**k for k in -3..+3]`. ±3 orders of
   magnitude covers every real packaging unit-misread case (μm → mm,
   mm → m, etc.) while keeping extreme misreads at `M = 1.0` for a
   human to inspect.
2. Pick the factor `M` for which `bbox_diagonal * M` falls inside
   the closed range `[10.0, 5000.0]`. When multiple factors qualify,
   pick the one giving the smallest in-range output — packaging
   designs cluster at the chip / small-package end (1–50 mm), and
   the aggressive choice is almost always right for the unit-misread
   cases this detector targets. When `M = 1.0` qualifies, always
   prefer it (no rescale).
3. Return `M` only when `|log10(M)| > 1` (i.e. `M ≤ 0.1` or `M ≥
   10` is **not** enough — must be ≤ 0.01 or ≥ 100). Marginal
   factors in `[0.1, 10]` return `1.0` so a real 5×5 mm dice
   (diagonal ≈ 7 mm) is not mistakenly rescaled ×10 to 70 mm.
4. Return `1.0` when no candidate brings the bbox into range.

When rescale fires (`M != 1.0`), all of the following SHALL reflect
the rescaled geometry:

- `RenderOutput.bbox`
- Every coordinate on every primitive in `RenderOutput.primitives`
- The per-layer thumbnail SVG produced by `render_layer_svg` for
  this file
- Anything derived downstream, including `EntityShape.points` used
  by the matcher and rule-check

`RenderOutput` SHALL gain an `applied_scale: float` field
(defaulting to `1.0`) carrying the factor. `flatten_for_render`
SHALL set it from the result of the rescale step. The DXF's source
`insunits` SHALL be recorded unmodified — it documents the input,
not the post-rescale state.

The `files` table SHALL gain an `applied_scale REAL NOT NULL
DEFAULT 1.0` column. Preprocessing SHALL persist the factor
returned by `flatten_for_render` into this column.

#### Scenario: A 1000×-too-big unitless DXF gets rescaled to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 42 000-unit bbox diagonal
- **THEN** `detect_scale_factor(0, 42000)` returns `0.001`
- **AND** `RenderOutput.applied_scale == 0.001`
- **AND** `RenderOutput.bbox` diagonal is 42 mm
- **AND** the persisted `files.applied_scale` row equals `0.001`

#### Scenario: A 1000×-too-small unitless DXF gets rescaled to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 0.05-unit bbox diagonal
- **THEN** `detect_scale_factor(0, 0.05)` returns `1000.0`
- **AND** `RenderOutput.applied_scale == 1000.0`
- **AND** `RenderOutput.bbox` diagonal is 50 mm

#### Scenario: A 100×-too-big unitless DXF gets rescaled to chip scale
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 6 000-unit bbox diagonal
- **THEN** `detect_scale_factor(0, 6000)` returns `0.01` (smallest in-range output: 60 mm rather than 600 mm)
- **AND** `RenderOutput.applied_scale == 0.01`
- **AND** `RenderOutput.bbox` diagonal is 60 mm

#### Scenario: A 100×-too-small unitless DXF gets rescaled
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 0.5-unit bbox diagonal
- **THEN** `detect_scale_factor(0, 0.5)` returns `100.0`
- **AND** `RenderOutput.applied_scale == 100.0`
- **AND** `RenderOutput.bbox` diagonal is 50 mm

#### Scenario: Declared-inch DXF is converted to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 1` (inch) and a 10-unit bbox diagonal
- **THEN** `detect_scale_factor(1, 10)` returns `25.4`
- **AND** `RenderOutput.applied_scale == 25.4`
- **AND** `RenderOutput.bbox` diagonal is 254 mm

#### Scenario: Declared-cm DXF is converted to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 5` (cm) and a 30-unit bbox diagonal
- **THEN** `detect_scale_factor(5, 30)` returns `10.0`
- **AND** `RenderOutput.applied_scale == 10.0`

#### Scenario: Declared-m DXF is converted to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 6` (m) and a 0.3-unit bbox diagonal
- **THEN** `detect_scale_factor(6, 0.3)` returns `1000.0`
- **AND** `RenderOutput.applied_scale == 1000.0`

#### Scenario: Declared-mm DXF is always left alone
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 4` (mm) regardless of bbox magnitude
- **THEN** `detect_scale_factor(4, ...)` returns `1.0`
- **AND** `RenderOutput.applied_scale == 1.0`

#### Scenario: Marginal-factor unitless DXF stays at 1.0
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 7-unit bbox diagonal (a real 5 mm × 5 mm dice would only need ×10 to reach the expected range)
- **THEN** `detect_scale_factor(0, 7)` returns `1.0` (×10 is rejected by the safety guard `|log10(M)| > 1`)

#### Scenario: In-range unitless DXF stays at 1.0
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 100-unit bbox diagonal (already inside `[10, 5000]`)
- **THEN** `detect_scale_factor(0, 100)` returns `1.0`

#### Scenario: Out-of-range unitless DXF stays at 1.0
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 0.00005-unit bbox diagonal that no power-of-10 in `[-3, +3]` can bring into `[10, 5000]`
- **THEN** `detect_scale_factor(0, 0.00005)` returns `1.0`

#### Scenario: NULL insunits is treated like 0
- **WHEN** preprocess runs on a DXF with no recoverable INSUNITS header and a 42 000-unit bbox diagonal
- **THEN** `detect_scale_factor(None, 42000)` returns `0.001`

#### Scenario: Layer thumbnails reflect rescaled geometry
- **WHEN** preprocess runs and `applied_scale != 1.0`
- **THEN** the per-layer thumbnail SVGs for that file use coordinates multiplied by `applied_scale`
- **AND** the SVG viewBox dimensions match the rescaled bbox

### Requirement: Auto-rescale invalidates saved Match JSON

The server SHALL invalidate any saved Match JSON when preprocessing
produces an `applied_scale` that differs from the file row's
previously persisted `applied_scale`. Concretely, the server SHALL:

1. Delete `data/match/{file_id}.json` if present.
2. Reset the file row's `match_saved` flag to `0`.
3. Move the file's status back to `ready_to_match`.

The dashboard SHALL surface a one-line banner on the affected
product card (next dashboard tick) explaining that Match JSON was
cleared after auto-rescale so the user knows to re-run match.

`data/prematch/{file_id}.json` SHALL be rebuilt as part of the
re-preprocess pipeline (it is always derived); no separate
invalidation step is needed.

#### Scenario: Match JSON is dropped when factor changes
- **WHEN** a file's previous `applied_scale` was `1.0` and a new preprocess returns `applied_scale == 0.001`
- **AND** `data/match/{file_id}.json` existed on disk before the preprocess
- **THEN** `data/match/{file_id}.json` no longer exists
- **AND** the file row's `match_saved == 0`
- **AND** the file row's `status == "ready_to_match"`

#### Scenario: No invalidation when factor is unchanged
- **WHEN** a file is re-preprocessed and `applied_scale` stays at the same value as before (whether `1.0` or non-`1.0`)
- **AND** the file previously had `match_saved == 1`
- **THEN** `data/match/{file_id}.json` is left alone
- **AND** `match_saved` remains `1`

#### Scenario: Side-region invalidation still wins on its own
- **WHEN** a preprocess that did not change `applied_scale` also did not change side regions
- **THEN** no Match JSON invalidation fires from this requirement

### Requirement: One-shot legacy migration on startup

On app startup, the server SHALL submit a re-preprocess job for
every file row matching **all** of:

- `applied_scale == 1.0` (never rescaled before)
- `detect_scale_factor(insunits, bbox_diagonal)` evaluated against
  the persisted `insunits` and bbox diagonal returns a non-`1.0`
  factor under the current detector.
- `user_unit_override` is `NULL` (no explicit operator decision). A
  file carrying an override SHALL be excluded — the operator has
  authority over its unit and the auto-rescale migration MUST NOT
  re-evaluate or overwrite it.

The migration SHALL reuse the existing re-preprocess job machinery
(the same code path that backs `POST /api/dev/reprocess-all`),
including its progress reporting through `_jobs`. Each matched file
SHALL go through the standard rescale + Match JSON invalidation flow
defined by the previous requirements.

The migration SHALL run exactly once per startup and SHALL be safe
to re-run (idempotent) — files that already have `applied_scale !=
1.0` are skipped because the detector returns the *current* unit
state, and a previously-rescaled file's persisted bbox is already in
mm.

#### Scenario: Legacy unit-suspect file gets rescaled on first startup
- **WHEN** the server starts and a file row has `applied_scale == 1.0`, `insunits == 0`, and persisted bbox diagonal of 42 000
- **THEN** a re-preprocess job is submitted for that file
- **AND** after the job completes, the file row has `applied_scale == 0.001`

#### Scenario: Legacy declared-inch file gets converted to mm on first startup
- **WHEN** the server starts and a file row has `applied_scale == 1.0`, `insunits == 1`, and persisted bbox diagonal of 10
- **THEN** a re-preprocess job is submitted for that file
- **AND** after the job completes, the file row has `applied_scale == 25.4`

#### Scenario: Migration is idempotent
- **WHEN** the server restarts after the migration ran once
- **THEN** the now-rescaled files are not re-submitted
- **AND** any newly-uploaded legacy-style files that haven't been preprocessed yet are still picked up

#### Scenario: Overridden file is excluded from the migration
- **WHEN** the server starts and a file row has `applied_scale == 1.0`, `insunits == 0`, a bbox diagonal whose detector factor is non-`1.0`, and `user_unit_override == "mm"`
- **THEN** no re-preprocess job is submitted for that file and its `applied_scale` stays `1.0` across the restart

### Requirement: User unit override overrides the auto-rescale detector

The `files` table SHALL gain a `user_unit_override TEXT NULL` column
whose value (when not `NULL`) is one of the literal strings
`"mm" | "cm" | "m" | "inch" | "μm"`. The column defaults to `NULL`
on every existing and newly inserted row. App-layer code SHALL be
the sole validator of the enumerated set; the database column itself
is unconstrained `TEXT`.

When `flatten_for_render` runs on a file row whose
`user_unit_override` is **not** `NULL`, the function SHALL derive the
scale multiplier `M` from the override using this table and SHALL
**skip** the `detect_scale_factor` heuristic entirely for that
invocation:

| `user_unit_override` | `M` |
|---|---|
| `"mm"`   | `1.0`    |
| `"cm"`   | `10.0`   |
| `"m"`    | `1000.0` |
| `"inch"` | `25.4`   |
| `"μm"`   | `0.001`  |

All downstream contracts established by the existing
"Auto-rescale unit-suspect DXFs during preprocess" requirement SHALL
hold unchanged when `M` is derived from an override:
`RenderOutput.applied_scale` carries the resulting multiplier; all
primitive coordinates, the bbox, layer thumbnails, and derived
`EntityShape.points` reflect the rescaled geometry; `files.applied_scale`
persists the multiplier.

The source `insunits` SHALL still be recorded unmodified. An override
SHALL be allowed even when the file declares a recognised `INSUNITS`
(e.g. `insunits == 1` for inch with `user_unit_override == "mm"`);
the override wins. This case is informational only — no warning
gating, no rejection.

The existing "Auto-rescale invalidates saved Match JSON" requirement
SHALL fire on override-driven `applied_scale` changes the same way it
fires on detector-driven changes — its trigger condition ("`applied_scale`
that differs from the file row's previously persisted `applied_scale`")
already covers both.

#### Scenario: Override to inch on a unitless DXF yields ×25.4
- **WHEN** a file row has `user_unit_override == "inch"` and a stored `insunits == 0`
- **AND** `flatten_for_render` runs for that file
- **THEN** the function does not call `detect_scale_factor`
- **AND** `RenderOutput.applied_scale == 25.4`
- **AND** the bbox and every primitive coordinate are multiplied by `25.4`
- **AND** `files.applied_scale` is persisted as `25.4`

#### Scenario: Override to mm on a declared-inch DXF wins over the declaration
- **WHEN** a file row has `user_unit_override == "mm"` and `insunits == 1` (inch)
- **AND** `flatten_for_render` runs for that file
- **THEN** `RenderOutput.applied_scale == 1.0` (no rescale)
- **AND** the stored `insunits` row value remains `1`
- **AND** the per-file dashboard payload still reports `insunits == 1` for transparency

#### Scenario: Override to μm rescales by 0.001
- **WHEN** a file row has `user_unit_override == "μm"`
- **AND** `flatten_for_render` runs for that file
- **THEN** `RenderOutput.applied_scale == 0.001`

#### Scenario: NULL override falls through to the detector
- **WHEN** a file row has `user_unit_override IS NULL`
- **AND** `flatten_for_render` runs for that file
- **THEN** `M` is derived from `detect_scale_factor(insunits, bbox_diagonal)` per the existing requirement
- **AND** every existing scenario for the detector continues to hold

### Requirement: Setting the picker to the detector's natural choice clears the override

The server SHALL write `user_unit_override = NULL` rather than store the
redundant string when the operator-driven override-set flow receives a
unit whose implied multiplier equals the multiplier `detect_scale_factor`
would return for the same file's `(insunits, pre_rescale_bbox_diagonal)`.
The operator MAY still trigger this code path to force a recompute; that
is acceptable, but the persistent state SHALL reflect "no override" so
future detector improvements continue to apply to this file.

#### Scenario: Operator picks "mm" when the detector also picks 1.0
- **WHEN** a file with `insunits == 4` (mm) and `applied_scale == 1.0` has its override set to `"mm"` via the override endpoint
- **THEN** the persisted `user_unit_override` is `NULL`
- **AND** the persisted `applied_scale` remains `1.0`

#### Scenario: Operator picks "inch" when the detector also picks 25.4
- **WHEN** a file with `insunits == 1` (inch) has its override set to `"inch"`
- **THEN** the persisted `user_unit_override` is `NULL`
- **AND** the persisted `applied_scale` is `25.4` (unchanged from detector path)

#### Scenario: Operator picks "mm" when the detector would pick 0.001 — override is recorded
- **WHEN** a file with `insunits == 0` and pre-rescale bbox diagonal 42 000 has its override set to `"mm"`
- **AND** the detector would have returned `0.001` for this file
- **THEN** the persisted `user_unit_override` is `"mm"`
- **AND** the persisted `applied_scale` is `1.0`

### Requirement: Unit-override endpoint and recompute

The server SHALL expose `POST /api/files/{file_id}/unit-override`
accepting a JSON body `{"unit": <one of "mm"|"cm"|"m"|"inch"|"μm">}`.
The endpoint SHALL:

1. Validate `unit` against the enumerated set and return `400` for any
   other value (including `null`, missing field, or unknown string).
2. Enqueue a re-preprocess job for `file_id` that, as its first step,
   writes the override (or `NULL` per the clear-on-match requirement)
   into the file row, then runs the standard preprocess pipeline.
3. Return `202 Accepted` with `{"job_id": <id>}` so the viewer can
   poll for completion using the existing job-status endpoint.

The endpoint SHALL be idempotent at the override-value level: a
second POST with the same `unit` value on a file row that already
holds that override (or that maps to the same effective `applied_scale`)
SHALL still enqueue the job and recompute, because the operator may
legitimately use the picker to force a recompute even when nothing
about the override value changed. The persisted override row state
after the job completes is governed by the
"Setting the picker to the detector's natural choice clears the
override" requirement.

While a recompute job is in flight for a given `file_id`, subsequent
POSTs to the same endpoint for the same `file_id` SHALL return `409
Conflict` with the in-flight job id. The viewer is responsible for
displaying this state.

When the recompute completes and the resulting `applied_scale`
differs from the file row's prior `applied_scale`, the
"Auto-rescale invalidates saved Match JSON" requirement governs the
cache-drop and product-banner behaviour — no second invalidation
mechanism is introduced.

#### Scenario: POST with a valid unit returns 202 with a job id
- **WHEN** a client POSTs `{"unit": "inch"}` to `/api/files/{file_id}/unit-override` for a file currently in `ready_to_match`
- **THEN** the response is `202 Accepted` with a JSON body containing `"job_id"`
- **AND** a preprocess job for that file is enqueued

#### Scenario: POST with an unknown unit returns 400
- **WHEN** a client POSTs `{"unit": "feet"}`
- **THEN** the response is `400 Bad Request`
- **AND** no job is enqueued
- **AND** the file row is unchanged

#### Scenario: POST while a recompute is already running returns 409
- **WHEN** a preprocess job triggered by an earlier override POST is still in flight for `file_id`
- **AND** a second POST arrives for the same `file_id`
- **THEN** the response is `409 Conflict` with the in-flight `job_id` in the body
- **AND** no new job is enqueued

#### Scenario: Recompute persists the override before preprocess runs
- **WHEN** a recompute job for `file_id` with target unit `"inch"` starts running
- **THEN** the job writes `user_unit_override = "inch"` to the file row before invoking `flatten_for_render`
- **AND** `flatten_for_render` reads the persisted override and skips the detector


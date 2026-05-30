## MODIFIED Requirements

### Requirement: Multi-file upload with deterministic file IDs

Users SHALL be able to upload one or more DXF files at the same time via
`POST /api/files`. Each accepted file SHALL receive a deterministic
`file_id` derived from the SHA-256 of its bytes (first 16 hex chars).
Re-uploading the same content SHALL deduplicate to the existing
`file_id` and skip re-processing if already ready.

The server SHALL enforce a maximum upload size on the product-scoped upload
handler `POST /api/products/{product_id}/files` (the only upload endpoint; the
legacy `POST /api/files` no longer exists). The limit SHALL be a configurable
byte ceiling (default 300 MB), overridable via the `SMDR2_MAX_UPLOAD_MB`
environment variable to stay consistent with the other `SMDR2_*` tuning knobs.
An uploaded file whose buffered size exceeds the limit SHALL be rejected with
HTTP 413 and SHALL NOT register a file record or submit a preprocess job. The
server MAY additionally reject early with 413 when the request-level
`Content-Length` exceeds the limit, but because in `multipart/form-data` that
header measures the whole request body (not the individual file part), the
authoritative per-file guard is the buffered-body length check.

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

#### Scenario: Oversized upload is rejected with 413
- **WHEN** a user uploads to `POST /api/products/{product_id}/files` a file
  whose buffered size exceeds the configured limit
- **THEN** the request is rejected with HTTP 413
- **AND** no file record is registered and no preprocess job is submitted

#### Scenario: Upload limit is environment-configurable
- **WHEN** `SMDR2_MAX_UPLOAD_MB` is set to a value smaller than a given file
- **THEN** that file is rejected with HTTP 413 under the lowered limit

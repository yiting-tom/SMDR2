## MODIFIED Requirements

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

#### Scenario: Successful preprocess
- **WHEN** the preprocess worker returns successfully for a file
- **THEN** the file's status becomes `ready_to_match`
- **AND** `parsed_at`, `primitive_count`, `bbox`, and `background` are populated

#### Scenario: Preprocess failure
- **WHEN** the preprocess worker raises an exception
- **THEN** the file's status becomes `error`
- **AND** the `error` field captures the exception message and traceback

#### Scenario: Skip-layer-pick path bypasses layer-related statuses
- **WHEN** a file is uploaded with `skip_layer_pick=true`
- **THEN** the file's status transitions are
  `preprocessing` → `ready_to_match` (or `error`)
- **AND** the status never reads `discovering_layers` or
  `awaiting_layers` for this file's upload

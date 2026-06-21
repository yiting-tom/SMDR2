# dxf-pipeline Specification (delta)

## MODIFIED Requirements

### Requirement: Multi-file upload with deterministic file IDs

Users SHALL be able to upload one or more DXF files at the same time
via `POST /api/versions/{version_id}/files`. Each accepted file SHALL
receive a deterministic `file_id` derived from the SHA-256 of its
bytes (first 16 hex chars). Re-uploading the same content SHALL
deduplicate to the existing `file_id`: the bytes are stored once, a
new `version_files` binding is created for the target
`(version_id, role)`, and per-version processing runs for the new
binding (parsed/match artifacts are keyed by `(version_id, file_id)`,
so a file already processed under another version still needs this
version's pass — unless this version already has the artifact, in
which case processing is skipped). Upload SHALL be rejected with
HTTP 409 when the version is signed off.

Upload SHALL accept an optional form field `skip_layer_pick: bool`
(default `false`). When set to `true`:

- The handler SHALL NOT submit `_discover_layers_worker` (Phase 1).
- The handler SHALL submit `_preprocess_worker` (Phase 2) directly
  with `selected_layers=None`, which the worker already treats as
  "no layer filter — keep every primitive".
- The handler SHALL register the new binding with
  `initial_status = PREPROCESSING`.
- The binding SHALL never enter the `discovering_layers` or
  `awaiting_layers` lifecycle states for this upload — those
  states are skipped entirely.
- Layer-manifest JSON
  (`data/layer_preview/{version_id}/{file_id}/layers.json`) and
  per-layer SVG thumbnails SHALL NOT be written for this upload.

When `skip_layer_pick` is absent or `false`, the upload behaves
as before: Phase 1 is submitted, the binding lands at
`awaiting_layers`, and the operator picks layers via the existing
layers endpoint (scoped by `version_id`).

The server SHALL NOT validate dev-mode origin of the flag. The
flag is honoured unconditionally on any incoming request; gating
the affordance is a UI responsibility.

#### Scenario: New DXF upload kicks off background processing
- **WHEN** a user uploads a previously-unseen `.dxf` file to
  `POST /api/versions/{vid}/files`
- **THEN** the response contains a `file_id`, a lifecycle status, and a `job_id`
- **AND** a job is submitted to the worker pool carrying `version_id`

#### Scenario: Duplicate content reuses bytes but processes per version
- **WHEN** a user uploads bytes-identical content already bound and
  processed under version `v1` to version `v2`
- **THEN** the response carries `deduped: true` (no new `uploads/` write)
- **AND** a binding `(v2, role, file_id)` is created
- **AND** processing for `(v2, file_id)` runs (artifacts keyed per version)

#### Scenario: Upload to a signed-off version is rejected
- **WHEN** version `v1` is signed off
- **AND** a user posts a DXF to `POST /api/versions/{v1}/files`
- **THEN** the response is HTTP 409 and no binding is created

#### Scenario: Non-DXF file is rejected
- **WHEN** a user uploads a file without a `.dxf` extension
- **THEN** the per-file response carries a `skipped` field with the reason
- **AND** no binding is registered

#### Scenario: skip_layer_pick=true bypasses Phase 1 entirely
- **WHEN** a user uploads a previously-unseen `.dxf` to
  `POST /api/versions/{vid}/files` with form field `skip_layer_pick=true`
- **THEN** the response carries `status: "preprocessing"` and a `job_id`
- **AND** the job submitted is `_preprocess_worker`, not
  `_discover_layers_worker`
- **AND** `selected_layers` on the binding is `NULL`
- **AND** the binding never transitions through `discovering_layers`
  or `awaiting_layers`
- **AND** `data/layer_preview/{vid}/{file_id}/layers.json` is not written

#### Scenario: skip_layer_pick=false (or absent) keeps the existing Phase 1 path
- **WHEN** a user uploads a `.dxf` with `skip_layer_pick=false`
  or with the field omitted
- **THEN** the response carries `status: "discovering_layers"`
- **AND** the job submitted is `_discover_layers_worker`
- **AND** the layer manifest is rendered as today under the version-scoped path

### Requirement: Per-file side regions persistence

The system SHALL persist, per `(version, file)` binding, three
optional axis-aligned world-space rectangles: `top_view_rect`,
`bottom_view_rect`, and `side_view_rect`, stored on the
`version_files` row. Each rectangle SHALL be stored as JSON
`{"x0":..,"y0":..,"x1":..,"y1":..}` with `x0<=x1` and `y0<=y1` after
normalisation. Any subset (including all three, any two, any one, or
none) SHALL be allowed. The rectangles SHALL be reachable via the
file record JSON (resolved with a `version_id` context) and writable
via `PATCH /api/files/{file_id}/side-regions` with a required
`version_id` and body `{"top_view_rect": <rect|null>,
"bottom_view_rect": <rect|null>, "side_view_rect": <rect|null>}`.
Writes SHALL be rejected with HTTP 409 when the version is signed
off.

Re-running preprocess or editing the selected layers SHALL NOT clear
any of the side rectangles.

#### Scenario: PATCH stores all three rectangles
- **WHEN** the user PATCHes side-regions with all three rectangles and a `version_id`
- **THEN** the binding returns all three rectangles on subsequent GETs
- **AND** the values are normalised so `x0<=x1` and `y0<=y1`

#### Scenario: PATCH clears one side independently
- **WHEN** the user PATCHes with `top_view_rect: null` and leaves the other two as-is
- **THEN** the `top_view` rectangle is unset
- **AND** the `bottom_view_rect` and `side_view_rect` are unchanged

#### Scenario: Rects are per version
- **WHEN** versions `v1` and `v2` both bind file `F`
- **AND** the user PATCHes `F`'s side-regions under `v2`
- **THEN** `(v1, F)`'s rectangles are unchanged

#### Scenario: Preprocess re-run preserves regions
- **WHEN** the binding's preprocess is re-run
- **THEN** the binding's three rectangles are unchanged

### Requirement: Per-file Match JSON export

The system SHALL persist match output per `(version, file)` at
`data/match/{version_id}/{file_id}.json` (the existing on-disk JSON
shape is unchanged). The save endpoint SHALL require a `version_id`
and SHALL be rejected with HTTP 409 when that version is signed off.
Saving match JSON for one version SHALL NOT modify any other
version's match artifacts. All other behaviours of the existing
requirement (shape, side-prefixed keys, invalidation semantics)
SHALL apply unchanged within the version scope.

#### Scenario: Save writes the version-scoped path
- **WHEN** the client saves match JSON for file `F` under version `v1`
- **THEN** `data/match/{v1}/{F}.json` exists
- **AND** no file outside `data/match/{v1}/` changes

#### Scenario: Sibling version artifacts are untouched
- **WHEN** `data/match/{v1}/{F}.json` exists
- **AND** the client re-runs and saves match for `(v2, F)`
- **THEN** `data/match/{v1}/{F}.json` is byte-for-byte unchanged

## REMOVED Requirements

### Requirement: One-shot legacy migration on startup
**Reason**: C9 定案不遷移——schema 偵測到舊版直接重建,不存在「帶著舊資料升級」的啟動路徑;auto-rescale 偵測對新上傳照常生效,無需開機掃描存量檔。
**Migration**: 刪除 startup 的 reprocess 掃描;`POST /api/dev/reprocess-all` 保留供手動全量重跑。

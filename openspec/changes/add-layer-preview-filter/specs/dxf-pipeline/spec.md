## ADDED Requirements

### Requirement: Two-phase preprocess with layer discovery

The DXF preprocess pipeline SHALL split into two phases: a cheap
**layer-discovery** phase that enumerates DXF layers and produces a
per-layer preview, and a **full-preprocess** phase that performs the
existing flatten / index / scan-all work but filtered to a user-chosen
layer subset. Each new upload SHALL start in `discovering_layers`,
transition to `awaiting_layers` once layer discovery completes,
transition to `preprocessing` once the user confirms a layer selection,
and finally reach `ready_to_match` when the full preprocess completes.

#### Scenario: Fresh upload runs layer discovery first
- **WHEN** a user uploads a previously-unseen `.dxf` file
- **THEN** the file's status becomes `discovering_layers`
- **AND** no `parsed/{file_id}.json` or `prematch/{file_id}.json` is written yet
- **AND** the file transitions to `awaiting_layers` when discovery succeeds

#### Scenario: Phase 2 is gated on user confirmation
- **WHEN** a file is in `awaiting_layers`
- **THEN** no full-preprocess job has been submitted
- **AND** the file does NOT auto-advance to `ready_to_match` without explicit user input

#### Scenario: Discovery failure surfaces as error
- **WHEN** the layer-discovery worker raises an exception
- **THEN** the file's status becomes `error`
- **AND** the `error` field captures the exception message

### Requirement: Per-layer SVG preview output

Layer discovery SHALL write, for each uploaded file, a manifest at
`data/layer_preview/{file_id}/layers.json` and one SVG thumbnail per
layer at `data/layer_preview/{file_id}/{layer}.svg`. Every SVG SHALL
use the file-wide world bbox as its `viewBox` so that all layer
thumbnails share the same coordinate frame and align visually. Layer
names containing characters unsafe for filesystem paths SHALL be
escaped (URL-encoded or sanitised) in the SVG filename, with the
mapping recorded in the manifest.

#### Scenario: Manifest lists every layer with its entity count
- **WHEN** layer discovery completes for a DXF with 12 distinct layers
- **THEN** `layers.json` contains 12 entries
- **AND** each entry carries `name`, `entity_count`, and `svg_filename`

#### Scenario: All thumbnails share the same viewBox
- **WHEN** two thumbnails are loaded for the same file
- **THEN** their SVG `viewBox` attributes are identical
- **AND** both equal the file's world bbox in `xmin ymin width height` form

#### Scenario: Decorative entities skipped in thumbnails
- **WHEN** a layer contains a `DIMENSION` annotation flagged as decorative
- **THEN** the layer is still listed in the manifest with its entity count
- **AND** the rendered thumbnail does NOT include the decorative geometry

### Requirement: Per-file persisted layer selection

The `files` table SHALL gain a `selected_layers` column (JSON-encoded
array of layer names) recording the user's chosen layer subset.
`NULL` SHALL mean "user has not chosen yet" — the file is still in
`awaiting_layers`. The full-preprocess job SHALL refuse to run when
`selected_layers` is `NULL` or `[]`. The selection SHALL persist
across library / role swaps so the user is not re-prompted on
re-preprocessing.

#### Scenario: Confirmation stores selection and kicks off Phase 2
- **WHEN** the user POSTs `{layers: ["BD", "SMD"]}` to `/api/files/{file_id}/layers`
- **THEN** `selected_layers` is persisted as `["BD","SMD"]`
- **AND** a `preprocessing` job is submitted with that layer filter
- **AND** the file's status flips to `preprocessing`

#### Scenario: Library swap reuses prior selection
- **WHEN** a file with `selected_layers=["BD","SMD"]` is reassigned to a different library
- **THEN** re-preprocessing runs against the new library
- **AND** the layer filter `["BD","SMD"]` is reused without re-prompting the user

#### Scenario: Empty selection rejected
- **WHEN** the user POSTs `{layers: []}` to `/api/files/{file_id}/layers`
- **THEN** the API responds with a 400 error
- **AND** no preprocess job is submitted

#### Scenario: Selection validated against the manifest
- **WHEN** the user POSTs a layer name that is not in the file's `layers.json`
- **THEN** the API responds with a 400 error
- **AND** the file's status is unchanged

### Requirement: Downstream artifacts honor the selected layers

Once a layer selection is in effect, the full-preprocess worker SHALL
drop every primitive whose `layer` is not in the selected set before
building the handle index, shape index, scan-all pre-match, and any
subsequent match-JSON export. `parsed/{file_id}.json` SHALL embed a
`selected_layers` field reflecting the active filter so downstream
debugging tools can identify the active subset.

#### Scenario: Filtered primitives never reach the handle index
- **WHEN** a file is preprocessed with `selected_layers=["BD"]`
- **AND** the DXF contains entities on both `BD` and `SILK` layers
- **THEN** `parsed/{file_id}.json` contains no primitives with `layer = "SILK"`
- **AND** `prematch/{file_id}.json` references no handles from `SILK` entities

#### Scenario: Match-JSON export filters the same way
- **WHEN** `POST /api/files/{file_id}/match-json` runs against a file with
  `selected_layers=["BD"]`
- **THEN** the exported matches contain no handles from layers outside
  `["BD"]`

#### Scenario: parsed.json embeds the active selection
- **WHEN** preprocessing completes with `selected_layers=["BD","SMD"]`
- **THEN** `parsed/{file_id}.json` includes the top-level field
  `selected_layers: ["BD","SMD"]`

### Requirement: Backwards-compatible handling of pre-existing files

The system SHALL leave files already in `ready_to_match` (or later) at
deploy time untouched — their parsed caches stay valid, their
`selected_layers` stays `NULL`, and `NULL` SHALL be treated as "all
layers" when reading the parsed cache. When the user clicks "Edit
layers" on such a legacy file, layer discovery SHALL be re-run on
demand and the file SHALL transition into the new two-phase flow from
that point forward.

#### Scenario: Existing ready file stays ready after deploy
- **WHEN** a file in `ready_to_match` with `selected_layers=NULL` is loaded
  after deploy
- **THEN** its status remains `ready_to_match`
- **AND** its parsed cache is reused as-is

#### Scenario: Edit-layers on a legacy file re-runs discovery
- **WHEN** the user invokes the "Edit layers" affordance on a legacy file
  with no `layers.json`
- **THEN** layer discovery is re-run for that file
- **AND** the file transitions to `awaiting_layers` until the user confirms

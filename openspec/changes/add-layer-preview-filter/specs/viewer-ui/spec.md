## ADDED Requirements

### Requirement: Layer-selection modal on dashboard

When a file's status is `awaiting_layers`, the dashboard SHALL open a
layer-selection modal for that file. The modal SHALL render one card
per layer, each containing the per-layer SVG thumbnail (fetched via
`GET /api/files/{file_id}/layer-preview/{layer}.svg`), the layer name,
the entity count, and a checkbox. The footer SHALL show
"Use selected (K of M)" as the primary action — disabled when K = 0 —
plus "Select all" and "Select none" helpers. Initial state SHALL be
"all layers checked". Confirming SHALL POST
`/api/files/{file_id}/layers` with the chosen layer names and then
poll until the file reaches `ready_to_match`.

#### Scenario: Modal auto-opens on awaiting_layers
- **WHEN** dashboard polling reports a file's status as `awaiting_layers`
- **THEN** the modal opens with that file's layer manifest pre-rendered

#### Scenario: Confirm with all layers behaves like the legacy preprocess
- **WHEN** the user opens the modal, leaves all layers checked, and clicks
  "Use selected (M of M)"
- **THEN** `selected_layers` is persisted as the full layer set
- **AND** Phase 2 preprocessing runs against every primitive in the file

#### Scenario: Confirm disabled when no layers checked
- **WHEN** the user unchecks every layer in the modal
- **THEN** the primary action button is disabled

#### Scenario: Cancel leaves file pending
- **WHEN** the user closes the modal without confirming
- **THEN** the file's status remains `awaiting_layers`
- **AND** no Phase 2 job is submitted

### Requirement: Dashboard row reflects layer-discovery status

Each file row on the dashboard SHALL display the `discovering_layers`
and `awaiting_layers` statuses distinctly from `preprocessing`, so the
user can distinguish "still parsing" from "ready for your input". A
"Layers" affordance on the row SHALL re-open the modal at any time
after Phase 1 completes (i.e., in any state from `awaiting_layers`
onward), with the file's current selection pre-checked.

#### Scenario: discovering_layers shown as in-progress
- **WHEN** a file's status is `discovering_layers`
- **THEN** the row shows an in-progress indicator with the copy
  "scanning layers"

#### Scenario: awaiting_layers shown as user-action-required
- **WHEN** a file's status is `awaiting_layers`
- **THEN** the row shows an "Action needed" badge that opens the modal on click

#### Scenario: Layers button re-opens modal with current selection
- **WHEN** the user clicks "Layers" on a file in `ready_to_match` with
  `selected_layers=["BD","SMD"]`
- **THEN** the modal opens with `BD` and `SMD` checked, every other layer
  unchecked

### Requirement: Viewer "Edit layers" affordance

The viewer header SHALL include an "Edit layers" button that opens the
same layer-selection modal as the dashboard. Confirming a new selection
SHALL post to `/api/files/{file_id}/layers`, watch for the status to
return to `ready_to_match`, and then reload the viewer page so the
canvas re-fetches the newly-filtered primitives. The button SHALL be
visible whenever the file has a layer manifest (i.e., post-Phase-1).

#### Scenario: Edit-layers reloads the viewer
- **WHEN** the user clicks "Edit layers", removes a layer, and confirms
- **THEN** the file re-runs Phase 2 with the new selection
- **AND** once status returns to `ready_to_match`, the viewer reloads
- **AND** primitives from the removed layer are absent from the canvas

#### Scenario: Edit-layers on a legacy file triggers discovery
- **WHEN** the user clicks "Edit layers" on a file with no layer manifest
- **THEN** layer discovery is requested for that file
- **AND** the modal shows a "scanning layers…" placeholder until the
  manifest arrives, then renders the layer cards

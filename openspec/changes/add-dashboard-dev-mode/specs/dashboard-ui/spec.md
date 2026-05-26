## ADDED Requirements

### Requirement: Dashboard developer-mode toggle

The dashboard SHALL render a "Developer Mode" toggle button in its
top toolbar. The toggle SHALL default to OFF, persist its state in
`localStorage` under the key `smdr2.dashboard.devMode` (string
`"1"` ON, absent / `"0"` OFF), and read that key on page load.
Toggling SHALL be synchronous: the dashboard re-renders the
dev-mode-gated affordances within the same tick, without a
round-trip or page reload.

The toggle's visible label SHALL communicate its current state
(e.g. `Developer Mode` when OFF, `Developer Mode: ON` when ON, or
equivalent visual treatment). The toggle SHALL be a focusable
`<button>` so it is keyboard-accessible.

When the toggle is OFF, every UI element added by this change
SHALL be absent from the DOM — not merely hidden via CSS — so a
non-dev user sees the dashboard exactly as it rendered before this
change.

#### Scenario: Default-off on first visit
- **WHEN** a user opens the dashboard with no `smdr2.dashboard.devMode` key set
- **THEN** the toggle reads as OFF
- **AND** no "Download Match" or "Download All Match" button is present in the DOM

#### Scenario: Persisted across reload
- **WHEN** the user toggles dev mode ON
- **AND** reloads the page
- **THEN** the toggle reads as ON on render
- **AND** the dev-mode-gated affordances render without further interaction

#### Scenario: Toggling off removes affordances
- **WHEN** dev mode is currently ON
- **AND** the user clicks the toggle
- **THEN** every "Download Match" and "Download All Match" button is removed from the DOM
- **AND** the `smdr2.dashboard.devMode` localStorage key is set to `"0"` or removed

### Requirement: Per-file download Match button

Each role-attached file row SHALL render a "Download Match" button
when dashboard dev mode is ON AND the file has `match_saved == true`. Clicking it SHALL trigger a client-side download of the
file's saved Match JSON, with the suggested filename
`match-<file_id>.json`. The download SHALL use the existing
`GET /api/files/{file_id}/match-json` endpoint and SHALL NOT
introduce any new backend route.

Files with `match_saved == false` SHALL NOT render this button (the
endpoint would 404; the existing match-progress indicator already
communicates the not-yet-saved state).

#### Scenario: Saved file shows the download button
- **WHEN** dev mode is ON
- **AND** a product card contains a role-attached file with `match_saved == true`
- **THEN** that file row contains a "Download Match" button

#### Scenario: Unsaved file hides the download button
- **WHEN** dev mode is ON
- **AND** a file row's file has `match_saved == false`
- **THEN** the row contains no "Download Match" button

#### Scenario: Clicking downloads the Match JSON
- **WHEN** the user clicks "Download Match" on a saved file
- **THEN** the browser saves a file named `match-<file_id>.json` containing the response body of `GET /api/files/{file_id}/match-json`

### Requirement: Per-product download all Match button

When dashboard dev mode is ON, each product card SHALL render a
"Download All Match" button. The button SHALL be disabled when the
product is not `ready_for_rule_check`, with a tooltip explaining
the precondition; otherwise clicking it SHALL trigger a client-side
download of the product's DRC handoff bundle, with the suggested
filename `drc-bundle-<product_id>.zip`. The download SHALL use the
existing `GET /api/products/{product_id}/drc-bundle` endpoint and
SHALL NOT introduce any new backend route.

#### Scenario: Ready product shows enabled button
- **WHEN** dev mode is ON
- **AND** a product has at least one role-attached DXF and every file has `match_saved == true`
- **THEN** the product card contains an enabled "Download All Match" button

#### Scenario: Not-ready product shows disabled button
- **WHEN** dev mode is ON
- **AND** a product has at least one role-attached file with `match_saved == false`
- **THEN** the product card contains a "Download All Match" button that is disabled
- **AND** the button has a tooltip or title attribute communicating the precondition

#### Scenario: Clicking downloads the DRC bundle
- **WHEN** the user clicks an enabled "Download All Match" button
- **THEN** the browser saves a file named `drc-bundle-<product_id>.zip` containing the response body of `GET /api/products/{product_id}/drc-bundle`

## ADDED Requirements

### Requirement: Skip layer picker dev affordance

The dashboard's upload zone SHALL render a `Skip layer picker
(dev: use all layers)` checkbox **only when `getDevMode()` returns
`true`**. When dev mode is off, the checkbox SHALL NOT appear in
the DOM (not hidden via CSS — absent entirely), so production
users see no change to the upload UI.

The checkbox state SHALL persist in `localStorage` under a new
key dedicated to this preference (e.g.
`smdr2.dashboard.skipLayerPick`), so a developer who flips it on
keeps the setting across page reloads and across the dev-mode
toggle being turned off and back on. The persisted state SHALL be
honoured on first render in dev mode.

When the operator initiates an upload (drop-zone drop or file
picker submit):

- If `getDevMode()` is `true` AND the checkbox is checked,
  the upload form data SHALL include `skip_layer_pick=true`.
- Otherwise (dev mode off, or checkbox unchecked), the form
  field SHALL NOT be included in the request — the server
  defaults to `false`.

The checkbox SHALL apply uniformly to every file in a multi-file
upload — there is no per-file override. The visible label SHALL
include the word "dev" so dev-mode users can tell at a glance
that the affordance is dev-only.

#### Scenario: Checkbox is absent when dev mode is off
- **WHEN** the dashboard renders with `getDevMode() === false`
- **THEN** no `Skip layer picker` checkbox is present in the
  upload zone's DOM

#### Scenario: Checkbox is present and respects persisted state when dev mode is on
- **WHEN** the dashboard renders with `getDevMode() === true`
- **AND** `localStorage` carries
  `smdr2.dashboard.skipLayerPick: "1"`
- **THEN** the checkbox is rendered checked
- **AND** flipping it off persists `"0"` (or removes the key)
  immediately

#### Scenario: Checked checkbox plus dev mode adds the form field
- **WHEN** the operator triggers an upload with the checkbox
  checked AND dev mode on
- **THEN** the multipart form body to
  `POST /api/products/{pid}/files` includes
  `skip_layer_pick=true`

#### Scenario: Unchecked checkbox does not add the form field
- **WHEN** the operator triggers an upload with the checkbox
  unchecked
- **THEN** the multipart form body SHALL NOT carry a
  `skip_layer_pick` field
- **AND** the server's default-false behaviour applies (Phase 1
  is submitted as today)

#### Scenario: Dev mode off mid-session disables sending the flag
- **WHEN** the operator had the checkbox checked, then turned
  dev mode off
- **AND** subsequently triggers an upload
- **THEN** the form body SHALL NOT include `skip_layer_pick`
  (even though the checkbox preference is still `"1"` in
  `localStorage`)
- **AND** the next time the operator re-enables dev mode, the
  checkbox renders checked again — the persisted preference is
  preserved across the toggle

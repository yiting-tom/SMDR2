## ADDED Requirements

### Requirement: Dev-parameter modal split across dashboard and viewer

Dev-mode parameter overrides SHALL be surfaced through two
dev-mode-only gear buttons — one on the Dashboard for DXF
preprocessing tunables, one on the Viewer page for matching
tunables — each opening a focused modal that edits only its own
slice of the allow-list. Both gears SHALL be controlled by the same
`localStorage["smdr2.dashboard.devMode"]` flag set by the Dashboard's
Developer Mode toggle; the Viewer SHALL NOT have its own toggle.

The Dashboard gear (`#dev-params-toggle` rendered after
`#dev-mode-toggle`) opens a modal labelled "Developer parameters"
that renders ONLY entries whose `module === "dxf"` from
`GET /api/dev/settings`, and exposes three actions:
- **Apply**: POSTs only the DXF-side overrides to
  `/api/dev/settings`.
- **Reset to defaults**: POSTs every visible DXF field's compiled
  default so the matching slice is left untouched.
- **Re-preprocess all files**: behind a confirmation dialog, POSTs
  `/api/dev/reprocess-all` and polls the returned job via the
  dashboard's existing status line.

The Viewer gear (`#dev-params-toggle` placed in the viewer header)
opens a modal labelled "Matching parameters" that renders ONLY
entries whose `module === "matching"`. It exposes only **Apply** and
**Reset to defaults** (each scoped to the matching slice); the
Re-preprocess action SHALL NOT appear on the viewer modal because
DXF preprocessing is not the per-file concern the viewer represents.

Both modals SHALL display a banner reminding the user that overrides
are in-memory only and not safe to change while jobs are running.
Both modals SHALL fetch state via `GET /api/dev/settings` every time
they open, so server state is authoritative even when
`localStorage["smdr2.dashboard.devOverrides"]` is stale after a
restart.

#### Scenario: Gears are invisible when Dev Mode is OFF
- **WHEN** Developer Mode is OFF
- **THEN** neither the dashboard gear nor the viewer gear is visible

#### Scenario: Dashboard gear shows the same flag on the viewer
- **WHEN** the user toggles Developer Mode ON on the dashboard and then opens any file's viewer page
- **THEN** the viewer's gear button is visible without requiring any additional toggle

#### Scenario: Dashboard modal hosts only DXF parameters
- **WHEN** the user opens the dashboard's parameter modal
- **THEN** every rendered input has `module === "dxf"`; matching constants (e.g. `TOLERANCE_ABS`) are absent

#### Scenario: Viewer modal hosts only matching parameters
- **WHEN** the user opens the viewer's parameter modal
- **THEN** every rendered input has `module === "matching"`; DXF constants (e.g. `BASE_TOLERANCE`) are absent

#### Scenario: Reset on dashboard does not touch matching overrides
- **WHEN** matching has an active override and the user clicks Reset in the dashboard modal
- **THEN** the matching override remains in place; only DXF entries return to defaults

#### Scenario: Apply round-trips through the backend
- **WHEN** the user edits `TOLERANCE_ABS` in the viewer modal and clicks Apply
- **THEN** the viewer POSTs the edited body to `/api/dev/settings`, updates the form from the response, and writes the echoed state to `localStorage`

#### Scenario: Re-preprocess requires explicit confirmation
- **WHEN** the user clicks "Re-preprocess all files" in the dashboard modal
- **THEN** a confirmation dialog is shown before any network call, and dismissing it sends no request

#### Scenario: Banner names the dev-only contract
- **WHEN** either modal is open
- **THEN** the body shows copy stating that overrides are in-memory only and not safe under concurrent jobs

## ADDED Requirements

### Requirement: Cross-role rule navigation centres the focused sub-rule

The viewer SHALL pan and zoom so that a sub-rule focused via cross-role
navigation — the viewer loaded with a `?rule=&idx=` request from the rule
sidebar's "→ {part} viewer" affordance — is centred and framed in the canvas,
so the operator lands directly on the entity rather than the default
whole-file view. The framing SHALL cover the union of the sub-rule's handle
geometry (`from` / `to` / `tol`) and coordinate geometry (`from_coordinates`
/ `to_coordinates` / `to_entity`), with a margin, and SHALL cap the zoom so a
tiny or single-point target does not fill the canvas. Focusing a sub-rule by
a local sidebar click (geometry already in the open file) SHALL NOT recentre
the view.

#### Scenario: Arriving via go-to-role centres the entity
- **WHEN** the viewer loads with `?rule=<name>&idx=<i>` for a sub-rule whose geometry is in this file
- **THEN** the sub-rule is focused (highlighted)
- **AND** the view is panned and zoomed so that geometry is centred and framed in the canvas

#### Scenario: Coordinate-mode target is framed
- **WHEN** the navigated sub-rule carries `from_coordinates`/`to_coordinates` and/or a `to_entity` outline
- **THEN** the centred frame covers those coordinate points (no handle required)

#### Scenario: Tiny target does not over-zoom
- **WHEN** the navigated sub-rule's geometry is a single point or near-zero-size bbox
- **THEN** the view centres on it at a capped standing zoom rather than filling the canvas

#### Scenario: Local click does not recentre
- **WHEN** the operator clicks a sub-rule whose geometry is already in the open file (local focus)
- **THEN** the sub-rule is highlighted
- **AND** the current pan/zoom is left unchanged

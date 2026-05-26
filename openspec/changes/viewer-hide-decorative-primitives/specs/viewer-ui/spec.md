## ADDED Requirements

### Requirement: Viewer canvas skips decorative primitives

The viewer canvas render loop SHALL skip every primitive whose
`decorative` property is truthy. This matches the behaviour of every
other downstream consumer of the primitive stream (matching pipeline,
library scan, click-to-select), all of which already treat
`decorative=true` as "non-load-bearing, ignore".

The `/api/files/{file_id}/primitives` endpoint contract SHALL NOT
change — the back end continues to ship every primitive including
decoratives. The filter is enforced client-side at the lowest common
point of the render dispatch so it applies uniformly to every
primitive type (line, circle, point, path, etc.).

#### Scenario: TEXT and MTEXT do not appear in the viewer
- **WHEN** a DXF contains TEXT or MTEXT entities (which the back-end flattener tags with `decorative=true`)
- **THEN** the viewer canvas SHALL NOT draw any of those primitives
- **AND** the user SHALL NOT see font-fallback placeholder rectangles when the DXF's referenced font is unavailable on their machine

#### Scenario: HATCH and DIMENSION do not appear in the viewer
- **WHEN** a DXF contains HATCH (solder-mask fills) or DIMENSION entities
- **THEN** the viewer canvas SHALL NOT draw any of their primitives, even if the primitive's `type` is `line` or `circle` (decorative tagging is type-agnostic)

#### Scenario: Non-decorative geometry remains visible
- **WHEN** a DXF contains LINE / CIRCLE / LWPOLYLINE / etc. entities (none of which the flattener tags as decorative)
- **THEN** every such primitive SHALL be drawn unchanged — the filter affects ONLY primitives with `decorative=true`

#### Scenario: Primitives API still returns decoratives
- **WHEN** `GET /api/files/{file_id}/primitives` is called for a file containing decorative entities
- **THEN** the response payload SHALL include every primitive, decoratives included, exactly as before this change

#### Scenario: Matching and selection behaviour is unchanged
- **WHEN** a user clicks on the region where a decorative primitive's bounds would have been, or runs scan-all / save-match
- **THEN** the click-pick / matching results SHALL be identical to before this change (those code paths already skipped decoratives via `skip_decorative=True` / `library.py:830` checks)

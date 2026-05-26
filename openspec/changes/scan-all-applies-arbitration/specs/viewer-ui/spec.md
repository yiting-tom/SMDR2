## MODIFIED Requirements

### Requirement: Scan-all overlay with per-class colours

Pressing `A` (or clicking "Scan All") SHALL toggle an overlay that
renders every library template's matches in its class's colour. The
overlay SHALL coexist with selection and near-miss highlights, with
selection/match (cyan) drawn on top.

**Class-view constraint filter.** The overlay SHALL apply the same
class-view constraint defined by `library.CLASS_VIEW_CONSTRAINTS`
(see `template-library` capability). For every handle whose class
is in the constraints registry, the renderer SHALL compute the
handle's bbox-center position relative to the file's
`top_view_rect`, `bottom_view_rect`, and `side_view_rect` (using
the priority `top_view > bottom_view > side_view`, matching
`split_matches_by_side`), and SHALL skip drawing the handle's match
when the resulting view (or `null` for unassigned) is not in the
class's allowed set. The per-class status counts SHALL reflect the
**post-filter** totals so the engineer sees the same number the
DRC will see in the eventual saved match JSON.

**Class arbitration.** The `GET /api/files/{file_id}/scan-all`
endpoint SHALL apply the same `class_arbitration.arbitrate` pipeline
that `save_match_json` uses, with the same
`library.CLASS_ARBITRATION_GROUPS` configuration. Every handle in
the returned `by_class` dict SHALL appear under the class
`arbitrate` would assign it in the persisted Match JSON — handles
that the matcher cross-fired across multiple member classes (e.g.
the FiducialCircle template hitting BGA balls because they share a
circle radius) SHALL be reassigned to the class their neighbour
count selects (e.g. dense-grid balls → BGABall, isolated circles →
FiducialCircle), respecting the population-fallback floor.

#### Scenario: Per-class colours visible
- **WHEN** the library has templates for `bga_ball` and `smd`
- **AND** the user presses `A`
- **THEN** BGA ball matches render in the `bga_ball` colour
- **AND** SMD matches render in the `smd` colour
- **AND** the dashboard status reads the per-class counts

#### Scenario: C4Ball outside top_view is not rendered
- **WHEN** Scan All is active, the file has `top_view_rect` set, and a `C4Ball` match's bbox center lies outside `top_view_rect`
- **THEN** the overlay SHALL NOT draw that match
- **AND** the `C4Ball` per-class count SHALL exclude that match

#### Scenario: BGABall inside top_view is not rendered
- **WHEN** Scan All is active and a `BGABall` match's bbox center lies inside `top_view_rect`
- **THEN** the overlay SHALL NOT draw that match
- **AND** the `BGABall` per-class count SHALL exclude that match

#### Scenario: Constrained class with no allowed view rect is fully hidden
- **WHEN** the file has `top_view_rect is null`
- **AND** Scan All is active
- **THEN** the overlay SHALL render zero `C4Ball` matches regardless of how many `C4Ball` handles exist in pre-match
- **AND** the `C4Ball` per-class count SHALL be 0

#### Scenario: Scan-all overlay matches saved-Match-JSON class assignment after arbitration
- **WHEN** the library has both a `BGABall` template and a `FiducialCircle` template whose circle radii are identical, and the drawing contains a dense BGA grid (≥ the group's `min_population`) plus a small number of widely-spaced fiducials
- **AND** the user runs Scan-all
- **THEN** every grid ball appears in `by_class["BGABall"]` (none leak into `by_class["FiducialCircle"]`) — arbitration's neighbour-count rule reassigns the FiducialCircle template's cross-fire hits to BGABall
- **AND** every real fiducial appears in `by_class["FiducialCircle"]` — its isolated neighbour count fits the class's `MaxNeighbors` rule
- **AND** the handle-to-class mapping in the overlay is identical to the handle-to-class mapping `save_match_json` writes to disk for the same file

#### Scenario: Population fallback applies to scan-all preview
- **WHEN** an arbitration group's non-default class has fewer than `min_population` instances after classification
- **THEN** the scan-all overlay SHALL show every pooled handle under the group's `default_class`, matching `save_match_json`'s output

#### Scenario: Scan-all response shape is unchanged
- **WHEN** the `GET /api/files/{file_id}/scan-all` endpoint is called
- **THEN** the response JSON SHALL be `{by_class: {<display_name>: [<handle>, ...], ...}, total: <int>}` — the same shape as before this change
- **AND** front-end code that reads `data.by_class[cls]` SHALL continue to work without modification

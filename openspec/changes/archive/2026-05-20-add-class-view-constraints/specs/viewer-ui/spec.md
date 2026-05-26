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

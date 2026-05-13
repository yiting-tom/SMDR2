# viewer-ui Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
### Requirement: Dashboard for files and libraries

The system SHALL provide a dashboard at `GET /` that lists uploaded
files (id, name, size, library, status, primitive count, upload time)
and lets the user create new libraries, switch the upload-target
library, and reassign a file's library inline.

#### Scenario: Dashboard renders without files
- **WHEN** the user opens `/` and no files have been uploaded
- **THEN** the dashboard shows an empty-state message and the upload zone

#### Scenario: Inline library reassignment
- **WHEN** the user changes the per-row library dropdown for a file
- **THEN** the file's status flips to `preprocessing`
- **AND** the dashboard polls until status returns to `ready_to_match`

### Requirement: Multi-file drop-zone upload

The dashboard SHALL accept `.dxf` files via either a drop-zone or a
file picker. Each upload SHALL be tagged with the currently-selected
library_id.

#### Scenario: Drop multiple DXFs at once
- **WHEN** the user drops three `.dxf` files onto the upload zone
- **THEN** three files appear in the table with status `preprocessing`
- **AND** each is bound to the selected library

### Requirement: Canvas viewer with correct hit-test

`GET /viewer/{file_id}` SHALL render the file's flattened primitives on
a Canvas 2D element. The screen→world transform used for rendering
SHALL be the same one used for cursor/hit-test calculations. The canvas
SHALL react to layout shifts (e.g., toolbar appearing) via
`ResizeObserver` so internal buffer dimensions never drift from the
displayed CSS size.

#### Scenario: Cursor coordinates match clicked geometry
- **WHEN** the user clicks on the boundary of a polyline at a given screen position
- **THEN** the picked entity is the polyline whose geometry includes that world position
- **AND** the highlighted entity overlays its source geometry exactly

### Requirement: AutoCAD-style interactions

The viewer SHALL implement AutoCAD-canonical mouse and keyboard
interactions:

- middle-drag = pan
- wheel = zoom (cursor as anchor)
- left-click on an entity = single pick within a pickbox tolerance (~5 device px)
- shift+left-click = toggle the clicked entity in the selection
- left-drag left→right = Window select (entire entity inside, blue border)
- left-drag right→left = Crossing select (geometry intersects rect, green dashed border)
- Esc = cascade: cancel drag → close scan-all → exit add-mode → clear selection

#### Scenario: Window-select inside a hollow rectangle does not select it
- **WHEN** the user drags a left→right window-select entirely inside a hollow rectangle
- **THEN** the rectangle is not added to the selection

#### Scenario: Crossing-select that does not intersect geometry rejects bbox-overlapped entities
- **WHEN** the user drags a right→left crossing-select inside a hollow polyline whose bbox surrounds the rect
- **THEN** the polyline is not added to the selection

### Requirement: Chain-select mode

A toggle "Chain" button SHALL enable an alternate selection mode where
single-click on an entity expands the selection to all entities whose
endpoints lie within a tolerance of the clicked entity, transitively.
Connectivity SHALL be computed lazily via a spatial hash over endpoint
positions.

#### Scenario: Chain-select gathers connected lines
- **WHEN** chain mode is on and the user clicks one of three lines whose endpoints share positions
- **THEN** the selection contains all three lines

### Requirement: Per-class hotkeys and scan workflow

The class toolbar SHALL render one button per class with hotkeys
`1` … `0`, `q` … `p`. Pressing a hotkey (or clicking the button) SHALL
enter add-mode for that class. In add-mode, pressing `S` SHALL run
`/api/files/{id}/match` with the current selection as the template;
matches and near-misses SHALL be highlighted (cyan and orange
respectively). Pressing `Enter` SHALL commit the staged template to the
file's library; the class button SHALL show `+ → ✓` while a preview is
staged.

#### Scenario: Hotkey enters add-mode
- **WHEN** the user presses `6` and class index 5 is `bga_ball`
- **THEN** the `bga_ball` button is marked active
- **AND** the status hint reads "ADD bga_ball · frame-select a pattern"

#### Scenario: S scans the current selection
- **WHEN** in add-mode with at least one entity selected, the user presses `S`
- **THEN** the matcher runs server-side
- **AND** matches are highlighted in cyan
- **AND** the active button icon flips to "✓"

#### Scenario: Enter commits the template
- **WHEN** with a staged preview, the user presses `Enter`
- **THEN** the template is persisted to the file's library
- **AND** the class button's count is incremented
- **AND** add-mode exits

### Requirement: Scan-all overlay with per-class colours

Pressing `A` (or clicking "Scan All") SHALL toggle an overlay that
renders every library template's matches in its class's colour. The
overlay SHALL coexist with selection and near-miss highlights, with
selection/match (cyan) drawn on top.

#### Scenario: Per-class colours visible
- **WHEN** the library has templates for `bga_ball` and `smd`
- **AND** the user presses `A`
- **THEN** BGA ball matches render in the `bga_ball` colour
- **AND** SMD matches render in the `smd` colour
- **AND** the dashboard status reads the per-class counts

### Requirement: Auto-shown pre-match on viewer load

When the viewer loads a file, it SHALL fetch the cached
`data/prematch/{file_id}.json` and display the overlay automatically so
the user sees the library's coverage of the file without manual
intervention.

#### Scenario: Viewer shows pre-match without user action
- **WHEN** the viewer page loads a file whose library has templates
- **THEN** the per-class overlay is rendered automatically

### Requirement: Library management modal

A "Library" button SHALL open a modal that lists every template in the
file's library grouped by class. Each row SHALL show a thumbnail
rendered from the template's geometry in the class colour, the
`class.index` key, entity/vertex counts, bbox dimensions, a
move-to-class dropdown, and a delete button. Class groups SHALL be
foldable; fold state SHALL persist for the session in `sessionStorage`.

#### Scenario: Delete a template
- **WHEN** the user clicks Delete on a template card and confirms
- **THEN** `DELETE /api/templates/{id}` is called
- **AND** the modal refreshes with the template gone
- **AND** the class toolbar count is updated

#### Scenario: Fold state persists across modal open/close
- **WHEN** the user folds the `bga_ball` group and re-opens the modal
- **THEN** `bga_ball` is still folded

### Requirement: Library switching in the viewer header

The viewer header SHALL contain a `<select>` listing every library
with the file's current library selected. Changing the selection SHALL
PATCH the file (with a confirm dialog), kick off re-preprocessing, and
reload the page so the toolbar/prematch refresh against the new
library.

#### Scenario: Switch library from viewer
- **WHEN** the user picks a different library from the header dropdown and confirms
- **THEN** `PATCH /api/files/{file_id}` is sent
- **AND** the file is re-preprocessed and the page reloads


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

### Requirement: Canvas renders the `circle` primitive natively

The viewer SHALL recognise primitives with `type == "circle"` and
render them via `ctx.arc(center.x, center.y, r, 0, 2π)`. The
primitive's bbox SHALL be `[cx - r, cy - r, cx + r, cy + r]`. The
hit-test for pickbox / single-pick SHALL treat the circle as a ring,
returning a hit when `|hypot(wx - cx, wy - cy) - r| ≤ tol`. Window
selection SHALL include the circle when its bbox lies fully inside
the selection rect. Crossing selection SHALL include the circle when
the circle's ring intersects the rect or the rect lies inside the
disk. OSNAP SHALL offer the existing center / quadrant / nearest snaps
for circle primitives. Chain mode SHALL NOT chain through circles
(they are closed shapes).

#### Scenario: A BGA-ball circle primitive renders as a circle
- **WHEN** the viewer loads a parsed file containing a `circle` primitive
- **THEN** the canvas shows a circular stroke at `(center, r)` in world coordinates
- **AND** no flattened polyline is rendered for that handle

#### Scenario: Pickbox hits the circle's ring
- **WHEN** the user clicks within `PICKBOX_CSS_PX` of the boundary of a circle primitive
- **THEN** the pick resolves to the circle's primitive index

#### Scenario: OSNAP center / quadrant work on circle primitives
- **WHEN** the cursor is near the center of a circle primitive in measure mode
- **THEN** the snap kind resolves to `"center"` with `(x, y) == (cx, cy)`
- **WHEN** the cursor is near a cardinal quadrant of a circle primitive
- **THEN** the snap kind resolves to `"quadrant"` with `(x, y)` matching the quadrant point

### Requirement: Viewport culling during render

`render()` SHALL compute the visible world rectangle from `view` and
the canvas dimensions, expanded by the active hairline-width margin,
and SHALL skip any primitive whose precomputed bbox lies fully outside
that rectangle. Culling SHALL be applied to the main draw pass and to
every highlight pass (scan-all, near-miss, selection, match, hover,
pinned, focused sub-rule).

#### Scenario: Zoomed-in pan skips off-screen primitives
- **WHEN** the user has zoomed into a region containing only a few hundred primitives
- **AND** `render()` runs
- **THEN** the status line reports a non-zero `culled` count for the just-completed frame
- **AND** the `drawn + culled` total equals the visible-layer primitive count for the file

### Requirement: Sub-pixel circle LOD batching

When a circle primitive's screen-space radius (`r * view.zoom / dpr`) is below `0.75` CSS pixels, the main draw pass SHALL render the circle as a 1×1 device-pixel dot at the circle's center. Dots SHALL be batched per color into a single `Path2D` and flushed in one fill call per color bucket. Highlight passes (scan-all / selection / etc.) SHALL continue to draw at fattened stroke width regardless of LOD so highlighted dots remain visible.

#### Scenario: Zoom-out collapses BGA balls into batched dots
- **WHEN** the viewer is zoomed out enough that each BGA-ball circle is below 0.75 px on screen
- **AND** `render()` runs
- **THEN** the status line reports a non-zero `dot` count for the just-completed frame
- **AND** every dot remains visible at its world position

#### Scenario: Selected sub-pixel circle still shows its highlight
- **WHEN** a circle primitive has been selected
- **AND** the view is zoomed out far enough that the circle would otherwise render as a dot
- **THEN** the selection-highlight pass draws a visible halo at the circle's screen position

### Requirement: Render status-line counters

The viewer status line SHALL display, alongside the existing fetch /
bbox / render timings, the most-recent frame's `drawn`, `culled`, and
`dot` counts. The counters SHALL be observable by a developer in the
DOM so the optimisation can be verified against a known-large file
such as `data/test_3layers.dxf` without opening DevTools.

#### Scenario: Status line shows counters after first render
- **WHEN** a file finishes loading and the first `render()` completes
- **THEN** the status line contains the substring `drawn` followed by a number
- **AND** the substring `culled` followed by a number
- **AND** the substring `dot` followed by a number

### Requirement: Dashboard flags suspect unit scale on a per-file basis

For each file shown on the dashboard, the server SHALL compute a
`unit_scale_warning` field derived from the persisted INSUNITS value
and the bbox diagonal, and the dashboard SHALL display a yellow
`⚠ unit` badge in the file's slot cell whenever the field is
non-null. Hovering the badge SHALL show a human-readable detail
string spelling out the raw INSUNITS value, the bbox diagonal, and
the reason the file is flagged. The warning SHALL be informational
only — it SHALL NOT block opening the file or running rule-check.

The derivation SHALL follow this table:

| insunits | bbox diagonal | warning value |
|---|---|---|
| any         | ≤ 100           | `null` |
| 4 / 5 / 6   | 100 ≤ D ≤ 1000  | `null` |
| 4 / 5 / 6   | > 1000          | `"suspect_scale"` |
| 0           | > 100           | `"suspect_scale"` |
| 0           | ≤ 100           | `"unitless"` |
| other / null| > 1000          | `"suspect_scale"` |
| other / null| otherwise       | `null` |

Legacy file rows whose INSUNITS column is `NULL` SHALL return `null`
warning (no badge) until they are re-preprocessed.

#### Scenario: A unitless file with packaging-scale bbox gets a mild warning
- **WHEN** a file with `insunits == 0` and bbox diagonal of 80 mm is rendered on the dashboard
- **THEN** the slot cell shows a `⚠ unit` badge
- **AND** the badge's `title` text contains `"INSUNITS=0"` and `"diagonal=80"`
- **AND** the warning kind is `"unitless"`

#### Scenario: A 1000×-scale file gets a strong warning
- **WHEN** a file with `insunits == 0` and bbox diagonal of 42_000 is rendered on the dashboard
- **THEN** the slot cell shows a `⚠ unit` badge
- **AND** the warning kind is `"suspect_scale"`

#### Scenario: A normal mm-scale file shows no badge
- **WHEN** a file with `insunits == 4` and bbox diagonal of 300 mm is rendered on the dashboard
- **THEN** the slot cell does not contain a `warn-badge`

#### Scenario: A legacy file with NULL insunits shows no badge
- **WHEN** a file uploaded before this change has `insunits == None` in its record
- **THEN** the slot cell does not contain a `warn-badge`
- **AND** re-preprocessing the file populates `insunits` and surfaces the badge if the heuristic now fires

### Requirement: Live readouts pinned to canvas-bottom status bar

The viewer SHALL render its four live readouts — most-recent pipeline
status, current mode hint, hovered-entity handle, and world-space
cursor coordinates — inside a `<footer id="canvas-statusbar">`
element positioned along the bottom edge of the canvas, NOT inside
the page `<header>`. The bar SHALL be visually distinct from the
header (semi-transparent dark background, monospace text) so the user
reads it as part of the drawing surface rather than the chrome.

Pointer events on the bar's background SHALL pass through to the
canvas underneath so the user can still pick geometry near the bottom
edge of the drawing; pointer events on the readout text spans
themselves remain active so tooltips work.

Each readout's element ID (`#status`, `#mode-hint`, `#handle-info`,
`#cursor-coords`) SHALL be preserved across this move so existing JS
write-sites continue to resolve without modification.

#### Scenario: All four readouts render in the canvas-bottom bar
- **WHEN** the user opens any file in the viewer
- **THEN** the document contains a `<footer id="canvas-statusbar">` element
- **AND** the four spans `#status`, `#mode-hint`, `#handle-info`, `#cursor-coords` are direct descendants of that footer
- **AND** none of those four spans appears anywhere inside `<header>`

#### Scenario: Cursor coordinates update in the new bar, not the header
- **WHEN** the user moves the mouse over the canvas
- **THEN** the text content of `#cursor-coords` inside `#canvas-statusbar` updates to reflect the new world-space XY
- **AND** the `<header>` element contains no element with id `cursor-coords`

#### Scenario: Bar does not block canvas picks
- **WHEN** the user left-clicks on geometry whose bounding box overlaps the area covered by `#canvas-statusbar`
- **THEN** the canvas hit-test selects that geometry as if the bar were not present


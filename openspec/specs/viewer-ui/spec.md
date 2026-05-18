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

When the `circle` primitive carries `filled: true` (set by the
backend for filled circular regions such as HATCH-bounded circles —
see the `dxf-pipeline` "Server-side DXF flatten" requirement), the
main draw pass SHALL fill the circle with `p.color` via `ctx.fill()`
instead of stroking the ring. Highlight passes (scan-all, near-miss,
selection / match, hover / pinned, focused sub-rule) that supply an
explicit `stroke` colour SHALL stroke the highlight on top of the
fill, mirroring the existing dual fill+stroke pattern used for
`filled_polygon`. When `filled` is missing or falsey, the main draw
pass SHALL stroke the ring exactly as before this change — the legacy
behaviour for `draw_path`-emitted CIRCLE entities is byte-identical.

Hit-test, OSNAP, selection, and bbox behaviour are independent of
`filled` — a filled circle resolves to the same primitive index, the
same center / quadrant snaps, and the same `(cx - r, cy - r, cx + r,
cy + r)` bbox as a stroke-only one of the same geometry.

#### Scenario: A BGA-ball circle primitive renders as a circle
- **WHEN** the viewer loads a parsed file containing a `circle` primitive without `filled`
- **THEN** the canvas shows a circular stroke at `(center, r)` in world coordinates
- **AND** no flattened polyline is rendered for that handle

#### Scenario: A filled circle primitive renders as a filled disc
- **WHEN** the viewer loads a parsed file containing a `circle` primitive with `filled: true`
- **THEN** the canvas shows a filled disc at `(center, r)` in the primitive's `color`
- **AND** no flattened ring-polygon is rendered for that handle

#### Scenario: Highlight pass strokes a filled circle on top of its fill
- **WHEN** a `filled: true` circle primitive is selected
- **AND** `render()` runs at a zoom level above the sub-pixel LOD threshold
- **THEN** the main draw pass fills the disc with `p.color`
- **AND** the selection-highlight pass strokes the ring at fattened width in the highlight colour on top of the fill

#### Scenario: Pickbox hits the circle's ring
- **WHEN** the user clicks within `PICKBOX_CSS_PX` of the boundary of a circle primitive (filled or not)
- **THEN** the pick resolves to the circle's primitive index

#### Scenario: OSNAP center / quadrant work on circle primitives
- **WHEN** the cursor is near the center of a circle primitive in measure mode (filled or not)
- **THEN** the snap kind resolves to `"center"` with `(x, y) == (cx, cy)`
- **WHEN** the cursor is near a cardinal quadrant of a circle primitive (filled or not)
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

The viewer SHALL apply level-of-detail compression to circle primitives
whose screen-space radius (`r * view.zoom / dpr`) is below
`DOT_THRESHOLD_CSS_PX` (default 3.0): each such circle SHALL render as
a single 1×1 device-pixel dot at the circle's centre. Dots SHALL be
bucketed per colour into a `Path2D` and emitted in one fill call per
colour bucket. The LOD SHALL apply both to the main draw pass (dot
colour = the primitive's own `color`) AND to every highlight pass
(dot colour = the highlight pass's colour: scan-all class colour,
near-miss colour, selection / match highlight colour, hover / pinned
highlight colour).

The LOD threshold SHALL depend ONLY on `p.type === "circle"` and the
screen-space radius — never on `p.filled`. Filled circles
(`filled: true`) and stroke-only circles (`filled` absent / false)
SHALL collapse into the SAME per-colour dot bucket when their
on-screen radius is below the threshold, so a HATCH-derived filled
ball at zoom-out costs the renderer no more than a `draw_path`-derived
stroke-only ball.

#### Scenario: Zoom-out collapses BGA balls into batched dots
- **WHEN** the viewer is zoomed out enough that each BGA-ball circle is below `DOT_THRESHOLD_CSS_PX`
- **AND** `render()` runs
- **THEN** the status line reports a non-zero `dot` count for the just-completed frame
- **AND** every dot remains visible at its world position

#### Scenario: Zoom-out collapses filled balls into batched dots
- **WHEN** the viewer is zoomed out enough that each filled (`filled: true`) circle is below `DOT_THRESHOLD_CSS_PX`
- **AND** `render()` runs
- **THEN** those circles render as 1×1 device-pixel dots in the same colour bucket as same-colour stroke-only circles
- **AND** the frame's `dot` counter increments for them
- **AND** the renderer does NOT fill any N-vertex polygon for them at that zoom

#### Scenario: Sub-pixel highlighted circle renders as a coloured dot at zoom-out
- **WHEN** a circle primitive has been selected (or returned by `scanAllByHandle`, `matchSet`, `nearMissSet`, `hoverSet`, or `pinnedSet`)
- **AND** the view is zoomed out far enough that the circle would otherwise render as a sub-pixel base dot
- **THEN** the corresponding highlight pass draws a 1×1 device-pixel dot at the circle's screen position in the pass's highlight colour
- **AND** the dot is visible even when many other highlighted circles surround it (it is not occluded by base-pass dots)

#### Scenario: Zoom-in past the LOD threshold restores fattened-stroke highlight
- **WHEN** the user zooms in until a previously-sub-pixel highlighted circle's screen radius exceeds `DOT_THRESHOLD_CSS_PX`
- **AND** `render()` re-runs
- **THEN** the highlight pass draws the fattened-stroke `drawPrimitive` halo for that circle instead of a dot

#### Scenario: Pan/zoom remains responsive with a 400 k-match scan
- **WHEN** a frame-select scan returns ≥ 100 000 matches on a BGA file
- **AND** the user pans or zooms at any zoom level where the matches are sub-pixel
- **THEN** the renderer batches every match as a colour-bucketed dot, not as 100 000 individual fattened strokes

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

### Requirement: Mark side regions mode

The viewer SHALL provide a "Mark sides" toolbar button and an `R`
hotkey that toggles a mark-side-regions mode. In this mode the viewer
SHALL cycle through three view slots in order — `top_view`,
`bottom_view`, `side_view` — and SHALL drive each slot through one of
three terminal user actions:

- **Draw + Enter** (left-press-drag-release defines a provisional
  rectangle; `Enter` commits it to the current slot and advances)
- **Bare-click skip** (a left-press-release at the same point, within
  the pickbox tolerance, advances without changing the current slot's
  stored rectangle)
- **Enter-without-draw** (commits the previously-stored rectangle for
  the current slot unchanged and advances)

After the third slot's Enter or bare-click, the viewer SHALL exit
mark mode and PATCH the three rectangles to the server in a single
request. The status hint SHALL read
`MARK <view> · drag a rectangle, Enter to keep, click to skip`
substituting `top_view`, `bottom_view`, or `side_view` for `<view>`
depending on the current slot.

Mark mode SHALL be mutually exclusive with add-mode, measure-mode,
and box-select; entering mark mode while another mode is active is a
no-op (the `R` hotkey is suppressed). While mark mode is active the
canvas SHALL NOT perform selection, pickbox, or pan-drag on
left-button events.

#### Scenario: Toolbar button enters mark mode
- **WHEN** the user clicks the "Mark sides" button or presses `R`
- **THEN** the button's active state is set
- **AND** the status hint reads `MARK top_view · drag a rectangle, Enter to keep, click to skip`

#### Scenario: Three consecutive drags capture all rectangles
- **WHEN** in mark mode, the user left-drags a rectangle and presses Enter, then drags and presses Enter, then drags and presses Enter
- **THEN** the three rectangles are persisted in slot order as `top_view_rect`, `bottom_view_rect`, `side_view_rect` (each normalised so x0<=x1, y0<=y1)
- **AND** the viewer exits mark mode automatically

#### Scenario: Bare-click skips the current view
- **WHEN** in mark mode at the `bottom_view` slot, the user does a bare left-click without dragging
- **THEN** the stored `bottom_view_rect` is left unchanged
- **AND** the slot advances to `side_view`

#### Scenario: Enter without drawing keeps the previously-stored rectangle
- **WHEN** in mark mode at the `top_view` slot, the user presses `Enter` without drawing
- **THEN** the stored `top_view_rect` is unchanged
- **AND** the slot advances to `bottom_view`

#### Scenario: Hotkey suppressed inside add or measure mode
- **WHEN** the user is in add-mode or measure-mode and presses `R`
- **THEN** mark mode does not activate
- **AND** the current mode is unchanged

### Requirement: Persistent side-region overlay

The viewer SHALL render `top_view_rect`, `bottom_view_rect`, and
`side_view_rect`, when present, as thin tinted outlines on the canvas
at all times (not just in mark mode). Each of the three views SHALL
use a distinct colour, and all three SHALL be drawn beneath
selection, near-miss, scan-all match overlays, and the active
box-drag rectangle so they never visually override interactive
feedback.

#### Scenario: Overlay visible after saving regions
- **WHEN** the user has saved at least one of the three rectangles and exits mark mode
- **THEN** every stored rectangle is still visible on the canvas
- **AND** each visible rectangle is colour-distinguishable from the others

#### Scenario: Overlay does not obscure selection
- **WHEN** the user selects an entity whose geometry lies inside the `top_view` rectangle
- **THEN** the selection highlight is rendered on top of the rectangle outline

#### Scenario: Only-side-view file shows one overlay
- **WHEN** the file has only `side_view_rect` set (the other two are null)
- **THEN** only the `side_view` outline is rendered
- **AND** no placeholder is rendered for the absent `top_view` and `bottom_view`

### Requirement: Redraw and clear side regions

The "Mark sides" toolbar button SHALL expose options to redraw a
single view's rectangle or clear all rectangles. Redrawing one view
SHALL keep the other two views' rectangles untouched. Clearing all
SHALL remove the overlay and unset all three columns server-side.

In addition, every committed view's persistent label SHALL render an
"×" delete glyph inside the label background. Left-clicking the ×
SHALL clear that specific view's rectangle (set the corresponding
`<view>_rect` to NULL server-side) and remove its overlay, without
affecting the other two views. The × hitbox SHALL take precedence
over every other left-click gesture (mark drag, measure pick, and
selection) so the user can always remove a view's rectangle from the
canvas chrome regardless of the current mode. When the × is clicked
during an active mark-mode session, the in-flight snapshot SHALL be
updated to reflect the deletion so a subsequent `Esc` cannot restore
the cleared rectangle.

#### Scenario: Redraw side_view only
- **WHEN** the user picks "Redraw side_view only" and drags a new rectangle
- **THEN** `side_view_rect` is overwritten with the new rectangle
- **AND** `top_view_rect` and `bottom_view_rect` are unchanged

#### Scenario: Clear all
- **WHEN** the user picks "Clear all"
- **THEN** all three of `top_view_rect`, `bottom_view_rect`, and `side_view_rect` are cleared server-side
- **AND** the overlay disappears

#### Scenario: × on label clears that view only
- **WHEN** all three rectangles are set and the user left-clicks the × on the `bottom_view` label
- **THEN** `bottom_view_rect` is cleared server-side
- **AND** the `bottom_view` overlay disappears
- **AND** `top_view_rect` and `side_view_rect` remain unchanged

#### Scenario: × during mark mode survives Esc
- **WHEN** the user enters mark mode with all three rectangles already set, left-clicks the × on the `top_view` label, then presses `Esc`
- **THEN** `top_view_rect` remains cleared (the Esc snapshot revert SHALL NOT bring it back)
- **AND** `bottom_view_rect` and `side_view_rect` are unchanged

### Requirement: Esc cancels in-progress mark mode

Pressing `Esc` while mark mode is active SHALL participate in the
existing Esc cascade. If the user is mid-drag on a slot's rectangle,
the drag SHALL be cancelled but mark mode stays active at the same
slot. If no drag is in progress, mark mode SHALL exit and SHALL
discard every provisional rectangle drawn during the current session:
the three stored rectangles revert to their pre-session values and
no server PATCH is sent.

#### Scenario: Esc during mid-drag cancels the drag
- **WHEN** the user is dragging the `top_view` rectangle and presses `Esc`
- **THEN** the in-progress rectangle is discarded
- **AND** mark mode remains active waiting for the same slot's rectangle

#### Scenario: Esc with no active drag cancels the session
- **WHEN** mark mode is active at the `side_view` slot, no drag is in progress, the user has already drawn and Entered a new `top_view_rect` this session, and the user presses `Esc`
- **THEN** mark mode exits
- **AND** the stored `top_view_rect` reverts to its pre-session value
- **AND** no PATCH is sent to the server


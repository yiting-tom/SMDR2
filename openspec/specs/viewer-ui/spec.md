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
positions. The `C` keyboard hotkey SHALL toggle the same mode as the
button — except while add-mode, measure-mode, or mark-mode owns the
canvas, where the hotkey SHALL be suppressed (chain mode is a
selection-modifier; firing it inside another tool would be confusing).

#### Scenario: Chain-select gathers connected lines
- **WHEN** chain mode is on and the user clicks one of three lines whose endpoints share positions
- **THEN** the selection contains all three lines

#### Scenario: C hotkey toggles chain mode
- **WHEN** the user presses `C` with no add-mode / measure-mode / mark-mode active
- **THEN** the chain mode toggles on (if off) or off (if on)
- **AND** the Chain button's active state mirrors the new mode

#### Scenario: C hotkey suppressed inside add / measure / mark mode
- **WHEN** the user presses `C` while add-mode, measure-mode, or mark-mode is active
- **THEN** chain mode is not toggled and the active tool keeps the keyboard

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

### Requirement: Scan-all overlay incrementally refreshes on commit

The viewer SHALL refresh the Scan All overlay on a successful
`POST /api/files/{file_id}/commit` so the newly-committed template's
matches become visible in their class colour, and SHALL avoid a
full server-side Scan All re-run in the common case by reusing the
live-preview `matchSet` already populated by the S-key match.

Trigger: the overlay is currently active in the viewer
(`scanAllByHandle !== null` in `app/static/canvas.js`) and the
commit response is HTTP 200.

The viewer's decision tree on a successful commit SHALL be:

1. **If Scan All is not active** (`scanAllByHandle === null`): no
   overlay state to update. The class-count chip on the toolbar still
   increments (as today). No status-line change beyond the existing
   `saved <Class> template (#count)` message.

2. **If Scan All is active AND the committed class is in the
   front-end's `CLASS_ARBITRATION_MEMBERS` set** (today `BGABall`,
   `FiducialCircle`): fall back to a full `runScanAll()` re-run.
   The front-end cannot reproduce the server-side neighbour-density
   arbitration that resolves cross-fire between arbitration-group
   members, so an incremental merge would mis-attribute handles.
   The status-line keeps its `saved <Class> template` message and
   the subsequent `scan-all: running…` status from `runScanAll()`
   takes over once the re-run starts.

3. **If Scan All is active AND the committed class is NOT in
   `CLASS_ARBITRATION_MEMBERS`**: merge incrementally. The handle
   set to merge is the **union of `selection` and `matchSet`** —
   `selection` is always non-empty at this point (the commit
   handler's early-return guard `!selection.size` enforces it), and
   covers the source pattern itself (which the server's
   `find_matches` excludes from its response via the
   `template_handle_set` skip). `matchSet` covers the other
   instances surfaced by the optional S-key live preview. For each
   handle in this union, set
   `scanAllByHandle.set(handle, committedClassName)`, overwriting
   any prior class assignment. Then:
   - recompute `byClass` counts from the updated `scanAllByHandle`;
   - re-apply view constraints via
     `applyViewConstraintsToScanAll(scanAllByHandle, byClass)` so
     any view-disallowed handles (e.g., a `C4Ball` match landing in
     `bottom_view`) are filtered out and excluded from the count;
   - replace `scanAllSummary` with the new
     `{ byClass, total: scanAllByHandle.size }`;
   - append a `· overlay +N <Class>` suffix to the status line where
     `N` is the post-view-constraint count for the new class.

The overlay merge SHALL NOT re-issue any server request other than
the `/api/files/{id}/commit` POST itself (and the optional
`/api/files/{id}/scan-all` GET in the arbitration-fall-back path).

#### Scenario: Non-arbitration commit with Scan All active merges incrementally
- **WHEN** Scan All is active with non-empty `scanAllByHandle`
- **AND** the user commits a new `SMD-2T` template via add-mode after
  pressing S to populate `matchSet`
- **THEN** every handle in `selection ∪ matchSet` SHALL appear in
  `scanAllByHandle` with class `"SMD-2T"`
- **AND** `scanAllSummary.byClass["SMD-2T"]` SHALL equal the number
  of `selection ∪ matchSet` handles that survive view-constraint
  filtering
- **AND** the status-line SHALL include `· overlay +N SMD-2T`
- **AND** no `GET /api/files/{id}/scan-all` request SHALL be issued

#### Scenario: Arbitration-class commit falls back to full Scan All
- **WHEN** Scan All is active
- **AND** the user commits a new `BGABall` or `FiducialCircle`
  template
- **THEN** the viewer SHALL call `runScanAll()` after the commit
  succeeds
- **AND** the incremental-merge code path SHALL NOT execute (i.e.,
  no `· overlay +N` suffix appears for this commit)
- **AND** the status-line SHALL transition through `saved <Class>
  template` then `scan-all: running…` then the final scan-all hit
  total once the re-run completes

#### Scenario: Commit with Scan All inactive does not enable overlay
- **WHEN** Scan All is NOT active (`scanAllByHandle === null`)
- **AND** the user commits a new template
- **THEN** `scanAllByHandle` SHALL remain null after the commit
- **AND** no overlay merge or re-run SHALL fire

#### Scenario: Commit without S-preview still highlights the source pattern
- **WHEN** Scan All is active
- **AND** the user commits a non-arbitration class without first
  pressing S (so `matchSet` is empty)
- **THEN** every handle in `selection` SHALL appear in
  `scanAllByHandle` with the committed class name
- **AND** the `· overlay +N <Class>` suffix SHALL include those
  handles in its count (subject to view-constraint filtering)
- **AND** no Scan All re-run SHALL fire

#### Scenario: Incremental merge re-applies view constraints
- **WHEN** Scan All is active
- **AND** the user commits a `C4Ball` template that matched some
  handles in `top_view` and some in `bottom_view`
- **THEN** only the `top_view` handles SHALL appear in
  `scanAllByHandle` with class `"C4Ball"` (per
  `CLASS_VIEW_CONSTRAINTS["C4Ball"] = ["top_view"]`)
- **AND** the `bottom_view` `C4Ball` matches SHALL be filtered out
  by `applyViewConstraintsToScanAll`
- **AND** the `· overlay +N` count SHALL reflect the
  post-view-constraint total

*Caveat*: `C4Ball` is itself in `CLASS_ARBITRATION_MEMBERS` only if
a future change adds it. Today it is not, so the incremental path
applies. This scenario documents the view-constraint composition,
not arbitration.

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
and the bbox diagonal, and SHALL include the per-file `applied_scale`
multiplier on the same payload. The dashboard SHALL render the
file's slot cell based on these fields as follows:

- If `applied_scale != 1.0` — the file was auto-rescaled — the slot
  SHALL display a neutral informational pill `ℹ rescaled <human>`
  (no warning colour). `<human>` SHALL be derived from the factor:
  - `M = 0.001` → `"÷1000"`
  - `M = 0.01`  → `"÷100"`
  - `M = 0.1`   → `"÷10"`
  - `M = 10`    → `"×10"`
  - `M = 100`   → `"×100"`
  - `M = 1000`  → `"×1000"`
  - `M = 25.4`  → `"×25.4 (inch)"`
  - any other declared-unit factor → `"×<factor>"` plus the unit
    suffix from the source INSUNITS
  The pill's `title` SHALL include the raw INSUNITS value, the
  **pre-rescale** bbox diagonal, and the applied factor.
- Else if `unit_scale_warning` is non-null — the file looks
  suspicious but no rescale was applied — the slot SHALL display
  the existing yellow `⚠ unit` badge with the existing detail text.
- Else the slot SHALL display nothing for unit scale.

The warning / pill SHALL be informational only — it SHALL NOT block
opening the file or running rule-check.

The `unit_scale_warning` derivation SHALL follow this table, applied
to the **pre-rescale** bbox diagonal so the heuristic is stable
regardless of auto-rescale:

| insunits | bbox diagonal (pre-rescale) | warning value |
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

The payload contract:

- `unit_scale_warning`: `null` | `"unitless"` | `"suspect_scale"`
- `unit_scale_warning_detail`: human-readable text. When
  `applied_scale != 1.0`, the text SHALL spell out the factor and
  the source unit, e.g.
  `"INSUNITS=0, pre-rescale diagonal=42000 → auto-rescaled ×0.001 (mm)"`
  or
  `"INSUNITS=1 (inch) → auto-rescaled ×25.4 (mm)"`.
- `applied_scale`: numeric multiplier, defaults to `1.0`.

#### Scenario: A 1000×-too-big rescaled file shows the informational pill
- **WHEN** a file with `insunits == 0`, pre-rescale bbox diagonal of 42 000, and `applied_scale == 0.001` is rendered on the dashboard
- **THEN** the slot cell shows a `ℹ rescaled ÷1000` pill (not the yellow warning badge)
- **AND** the pill's `title` text contains `"INSUNITS=0"`, `"diagonal=42000"`, and `"×0.001"`

#### Scenario: A declared-inch rescaled file shows the inch pill
- **WHEN** a file with `insunits == 1` and `applied_scale == 25.4` is rendered on the dashboard
- **THEN** the slot cell shows a `ℹ rescaled ×25.4 (inch)` pill
- **AND** the pill's `title` text contains `"INSUNITS=1"` and `"×25.4"`

#### Scenario: A unitless file with packaging-scale bbox still warns
- **WHEN** a file with `insunits == 0`, bbox diagonal of 80 mm, and `applied_scale == 1.0` is rendered on the dashboard
- **THEN** the slot cell shows a `⚠ unit` badge
- **AND** the badge's `title` text contains `"INSUNITS=0"` and `"diagonal=80"`
- **AND** the warning kind is `"unitless"`

#### Scenario: A 1000×-scale file that wasn't auto-rescaled still warns
- **WHEN** a file with `insunits == 4`, bbox diagonal of 42 000 mm, and `applied_scale == 1.0` is rendered on the dashboard
- **THEN** the slot cell shows a `⚠ unit` badge (declared mm + large bbox is not auto-rescaled)
- **AND** the warning kind is `"suspect_scale"`

#### Scenario: A normal mm-scale file shows nothing
- **WHEN** a file with `insunits == 4`, bbox diagonal of 300 mm, and `applied_scale == 1.0` is rendered on the dashboard
- **THEN** the slot cell does not contain a `warn-badge`
- **AND** the slot cell does not contain a `rescaled-pill`

#### Scenario: A legacy file with NULL insunits shows no badge
- **WHEN** a file uploaded before the auto-rescale change has `insunits == None` and `applied_scale == 1.0`
- **THEN** the slot cell does not contain a `warn-badge`
- **AND** re-preprocessing the file populates `insunits` (and may set `applied_scale` if it triggers the auto-rescale rule), then surfaces the appropriate pill / badge

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

### Requirement: Per-role sibling-DXF dropdown switcher

The viewer header SHALL render four conceptual role positions, in
left-to-right order:

1. `SBT`
2. `BD`
3. `POD`
4. A **split sub-slot pair** with `RING` on the left and `LID` on
   the right (see "Per-product 4th-slot pair rendering" below)

Positions 1–3 are immutable single-role slots and follow the
existing per-slot rules below. Position 4 is two adjacent role
buttons (`RING` on the left, `LID` on the right) sharing one
conceptual position so the toolbar still presents four columns —
each half is an independent role-btn for hit-testing.

**Per-product 4th-slot pair rendering:**

Each half of the 4th-slot pair SHALL be rendered independently
according to the per-slot rules below, using its own role key
(`"RING"` for the left half, `"LID"` for the right). Neither half's
rendering SHALL depend on the file count of the opposite half — both
halves MAY be enabled placeholders, both MAY hold ≥1 DXF, or any
mixed combination. The `.disabled` modifier SHALL NOT be applied to
either half on the basis of files held under the opposite role.

For slots 1–3 (always `SBT`/`BD`/`POD`) and for each half of the
4th-slot pair (each treated as its own single-role slot keyed by
`"RING"` or `"LID"`), the appearance and interaction SHALL be
determined by the number of DXFs the current file's product has
under that role, read from `files_by_role_all[role]` on the
`GET /api/products/{id}` payload (the backend already serves this
list — see the `product-files` capability):

- **Zero DXFs** in the role → render an unclickable
  `role-btn role-btn.empty` (dashed border, dimmed colour, `title`
  attribute states the role is not uploaded).
- **Exactly one DXF**, and that DXF IS the currently-loaded viewer
  file → render a `role-btn.current` (cyan accent, non-link, no
  cursor) — the engineer is already there.
- **Exactly one DXF**, and that DXF is NOT the currently-loaded
  file → render a plain `<a class="role-btn" href="/viewer/{file_id}">`
  that navigates to that file.
- **Two or more DXFs** in the role → render a
  `<button class="role-btn role-btn--multi" aria-haspopup="menu">`
  whose label includes the count (e.g., `BD ×3 ▾`). The button
  SHALL also carry the `.current` class when the role matches the
  currently-loaded file's role. Clicking the button SHALL toggle a
  dropdown menu, described below.

When the dropdown opens, it SHALL list each sibling DXF in
`files_by_role_all[role]` order (the backend orders `multi` first,
then `top`, `bottom`, `side`). Each item SHALL be labelled with the
file's `name` (the uploaded DXF filename); when `name` is missing the
item SHALL fall back to the file's `dxf_view` enum, then to its `id`.
Items SHALL be real `<a href="/viewer/{file_id}">` so middle-click
/ cmd-click open in a new tab — EXCEPT the item whose `file_id`
equals the currently-loaded viewer file, which SHALL be a non-link
element marked active (matching the `role-btn.current` cyan
accent).

Only one dropdown SHALL be open at a time. Opening one dropdown
SHALL close any other open dropdown in the switcher. The dropdown
SHALL close on: outside-click, selecting an item (browser navigates
away), pressing `Esc`, or opening a different role's dropdown.
Pressing `Esc` while no dropdown is open SHALL NOT intercept the
viewer's existing Esc handlers (mark-mode cancel, measure-tool
cancel, etc.).

The four slot positions SHALL remain hardcoded in the client so that
the toolbar always renders four conceptual columns in a stable
order — empty roles included — acting as an upload-progress
checklist for the engineer. The 4th position SHALL always render
both halves (RING on the left, LID on the right); only their
enabled/populated states vary per product. Positions 1–3 are
immutable single-role slots.

#### Scenario: Single-DXF role still renders as a one-click button
- **WHEN** the current file's product has exactly one DXF under role `POD`
- **AND** the currently-loaded file is NOT that POD file
- **THEN** the `POD` slot renders as a plain `<a>` with `href="/viewer/{pod_file_id}"` and no dropdown affordance
- **AND** clicking it navigates directly to that file with no extra interaction

#### Scenario: Multi-DXF role exposes every sibling via a dropdown
- **WHEN** the product has DXFs `A.top` and `A.bottom` under role `BD`
- **AND** the currently-loaded file is `A.top`
- **THEN** the `BD` slot renders as a `role-btn role-btn--multi current` button labelled `BD ×2 ▾`
- **AND** clicking the button opens a menu listing `top` (marked active, non-link) and `bottom` (a link to `/viewer/{A.bottom.id}`)

#### Scenario: Multi-DXF on a non-current role still opens a menu
- **WHEN** the product has DXFs `B.multi` and `B.top` under role `SBT`
- **AND** the currently-loaded file is NOT either of those SBT files (it's the product's `BD` file)
- **THEN** the `SBT` slot renders as `role-btn role-btn--multi` (without `.current`) labelled `SBT ×2 ▾`
- **AND** clicking opens a menu of both SBT siblings, both of which are real navigable links

#### Scenario: Middle-click on a dropdown item opens a new tab
- **WHEN** the engineer opens the dropdown for a multi-DXF role
- **AND** middle-clicks (or cmd-clicks) any sibling item that is not the current file
- **THEN** the browser opens that sibling's viewer URL in a new tab, matching the existing single-link-button behaviour

#### Scenario: Esc closes the dropdown without disturbing other Esc handlers
- **WHEN** the engineer presses `Esc` while a role dropdown is open
- **THEN** the dropdown closes and focus returns to its trigger button
- **WHEN** the engineer presses `Esc` while no role dropdown is open and a measure-tool operation is active
- **THEN** the measure-tool cancels exactly as before this change (the role-switcher's Esc handler is a no-op)

#### Scenario: Empty role keeps the dashed placeholder
- **WHEN** the product has zero DXFs under role `BD`
- **THEN** the `BD` slot renders as `role-btn.empty` (dashed border) with no dropdown
- **AND** clicking it does nothing — same as today

#### Scenario: The current file's role with a single DXF stays non-interactive
- **WHEN** the currently-loaded file is the product's only DXF under role `BD`
- **THEN** the `BD` slot renders as `role-btn.current` with no dropdown — the engineer is already viewing the only choice

#### Scenario: Opening one dropdown closes another
- **WHEN** the engineer opens the `BD` dropdown
- **AND** clicks the `SBT` dropdown trigger
- **THEN** the `BD` dropdown closes
- **AND** the `SBT` dropdown opens
- **AND** at most one dropdown is open at any given moment

#### Scenario: 4th-slot pair both halves empty when product has neither RING nor LID
- **WHEN** the product has zero DXFs under both `RING` and `LID`
- **THEN** the 4th position renders two adjacent role-btns: a `RING` half and a `LID` half
- **AND** both halves carry `role-btn.empty` (dashed border) and are enabled (click / drop targets active)
- **AND** neither half carries `.disabled`

#### Scenario: LID half stays an enabled placeholder when product already holds a RING file
- **WHEN** the product has one DXF under `RING` and zero under `LID`
- **THEN** the left (RING) half renders by the single-DXF rules (button or `.current` per current-file membership)
- **AND** the right (LID) half renders as `role-btn.empty` (dashed border) — enabled, no `.disabled` modifier
- **AND** clicking or dropping onto the LID half MAY initiate a LID upload via the dashboard's empty-slot flow

#### Scenario: RING half stays an enabled placeholder when product already holds a LID file
- **WHEN** the product has one DXF under `LID` and zero under `RING`
- **THEN** the right (LID) half renders by the single-DXF rules
- **AND** the left (RING) half renders as `role-btn.empty` (dashed border) — enabled, no `.disabled` modifier
- **AND** clicking or dropping onto the RING half MAY initiate a RING upload

#### Scenario: Both halves populated render independently
- **WHEN** the product has one DXF under `RING` and one DXF under `LID`
- **THEN** the left (RING) half renders by the single-DXF rules for the RING file
- **AND** the right (LID) half renders by the single-DXF rules for the LID file
- **AND** neither half carries `.disabled`
- **AND** the front-end SHALL NOT emit a console warning about the both-present state

#### Scenario: Current-file highlighting works on the LID half independently of RING
- **WHEN** the currently-loaded viewer file has `dxf_role = "LID"` and is the product's only LID file
- **THEN** the right (LID) half renders as `role-btn.current` (cyan accent, non-link)
- **AND** the left (RING) half renders according to its own file count (empty placeholder if zero, navigable link if one non-current, dropdown if multi)

### Requirement: Dashboard slot per-file action bar

Each file row in a dashboard product card's slot SHALL render a
consistent action bar regardless of whether the slot holds one file
or multiple. The action bar SHALL include, at minimum:

- An **Open** link to the viewer (when the file's status is
  `ready_to_match`) or a **Pick layers** action (when the status is
  `awaiting_layers`).
- A **Layers** button (when the status is not `discovering_layers`
  or `error`) that opens the layer-selection modal for that file.
- A **Replace** button that opens the file picker bound to the same
  `(product, role)` slot, with the current file id passed as
  `replace_file_id` so the upload evicts it before landing the new
  one.
- A **Delete** button (rendered as `✕`) that detaches the file from
  the slot via `DELETE /api/products/{product_id}/files/{file_id}`.
  The Delete action SHALL be available for both single-file and
  multi-DXF slots; the single-file case SHALL NOT hide it. The
  Delete button SHALL prompt for confirmation before issuing the
  request and SHALL refresh the dashboard on success.

The Delete affordance is a **detach**, not a destructive deletion:
the underlying file row remains in `FILE_STORE` (so reuploads of
the same content reuse it via content-addressable storage); only
the `product_id` / `dxf_role` / `dxf_view` binding clears, plus the
cached Match JSON.

The viewer header SHALL NOT carry a Delete affordance; file
management remains scoped to the dashboard.

#### Scenario: Single-file slot exposes Delete
- **WHEN** a product has exactly one DXF under role `SBT`
- **THEN** the SBT slot's action bar SHALL include a Delete button (`✕`) alongside Replace
- **AND** the Delete button SHALL share the styling and confirm-on-click behaviour used by the multi-DXF case

#### Scenario: Delete on a single-file slot detaches the file
- **WHEN** the engineer clicks Delete on the SBT slot's only file and confirms the prompt
- **THEN** `DELETE /api/products/{product_id}/files/{file_id}` is issued
- **AND** on HTTP 204 the dashboard refreshes
- **AND** the SBT slot returns to the empty drop-zone state

#### Scenario: Delete on a multi-DXF slot detaches only the targeted file
- **WHEN** a product has DXFs `A` and `B` under role `BD`
- **AND** the engineer clicks Delete on `A`'s row and confirms
- **THEN** `DELETE /api/products/{product_id}/files/{A.id}` is issued
- **AND** the slot continues to show `B`

#### Scenario: Delete unlocks RING / LID configuration switching
- **WHEN** a product holds one DXF under role `RING` and zero under `LID`
- **AND** the engineer clicks Delete on the RING slot's file and confirms
- **THEN** the RING file is detached and the LID half of the 4th slot becomes enabled
- **AND** the engineer MAY then upload a LID file without rebuilding the product

#### Scenario: Delete confirm dialog cancels cleanly
- **WHEN** the engineer clicks Delete on any file
- **AND** dismisses the confirm dialog
- **THEN** no DELETE request is issued
- **AND** the dashboard state is unchanged

#### Scenario: Viewer header has no Delete control
- **WHEN** the engineer opens any file in the viewer
- **THEN** the viewer header SHALL NOT render a Delete button for the loaded file
- **AND** the only role-related controls in the header SHALL be the per-role sibling-DXF switcher described above

### Requirement: Dashboard products grouped into foldable customer sections

The dashboard at `GET /` SHALL group product cards by their
`library_id` (the customer dimension) into foldable sections.
Each section SHALL render:

- A header element with role `button` and `tabindex="0"` showing the
  customer name, the count of products in the section, and a
  chevron (`▸` when folded, `▾` when expanded). The whole header
  SHALL be clickable; Enter and Space SHALL toggle the section
  while the header has focus.
- A container of product cards, hidden when the section is folded.
  When expanded the cards SHALL retain their full existing
  appearance and behavior (header, slot grid, footer with Rule
  Check / Download All Match / Delete, etc.).

Customer sections SHALL be ordered by library name
(case-insensitive ascending), with `library_id` as the deterministic
tiebreak when two libraries share a name.

Libraries with zero products SHALL NOT render a section at all (no
empty headers, no zero-count placeholders).

Sections SHALL default to **folded** on first page load (i.e. when
no fold-state record exists in storage). Fold state SHALL persist
under `sessionStorage` key `smdr2.dashboard.foldedCustomers`, whose
value SHALL be a JSON array of `library_id` strings representing
the currently folded sections. The renderer SHALL treat the absence
of the key as "every section folded".

When the fold state references a `library_id` that no longer exists
(library deleted), the renderer SHALL ignore the stale entry; no
active pruning is required.

The library bar at the top of the page, the New Library / New
Product buttons, the per-card actions, and every existing endpoint
SHALL remain unchanged. This requirement is purely a presentation
layer transform.

#### Scenario: First page load shows all sections folded
- **WHEN** the dashboard loads with `smdr2.dashboard.foldedCustomers` absent from sessionStorage
- **AND** the user has products under at least two libraries
- **THEN** every customer section renders with the `▸` chevron
- **AND** no product cards are visible

#### Scenario: Clicking a folded header expands the section
- **WHEN** the user clicks a section header whose chevron is `▸`
- **THEN** the section's product cards become visible
- **AND** the header's chevron becomes `▾`
- **AND** the `aria-expanded` attribute updates to `"true"`
- **AND** `sessionStorage["smdr2.dashboard.foldedCustomers"]` no longer contains that library's id

#### Scenario: Clicking an expanded header folds the section
- **WHEN** the user clicks a section header whose chevron is `▾`
- **THEN** the section's product cards are hidden
- **AND** the header's chevron becomes `▸`
- **AND** `sessionStorage["smdr2.dashboard.foldedCustomers"]` contains that library's id

#### Scenario: Keyboard activation toggles the section
- **WHEN** a customer-section header has keyboard focus
- **AND** the user presses Enter or Space
- **THEN** the section toggles its fold state exactly as a click would
- **AND** the default page-scroll behavior of Space SHALL NOT fire

#### Scenario: Empty library is hidden
- **WHEN** a library exists but no product references its `library_id`
- **THEN** the dashboard renders no section for that library
- **AND** the library nonetheless remains selectable in the top-bar library dropdown

#### Scenario: Section header includes a product count
- **WHEN** a customer has N products (N ≥ 1)
- **THEN** the section header's text SHALL include `(N products)` (or `(1 product)` for N = 1)

#### Scenario: Stale folded id is ignored after library deletion
- **WHEN** `sessionStorage["smdr2.dashboard.foldedCustomers"]` contains a library id whose library no longer exists
- **THEN** the renderer SHALL skip that id silently
- **AND** the remaining customer sections render normally

#### Scenario: Customer sections render in alphabetical order
- **WHEN** the dashboard has products under libraries `Beta Co` and `acme corp`
- **THEN** the `acme corp` section renders above `Beta Co` (case-insensitive ascending)

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

### Requirement: Viewer unit-override picker

The viewer SHALL render a unit-override picker control in the
viewer header, co-located with the existing `library-switcher`
dropdown so it sits next to the other file-level interpretation
controls. The control SHALL:

- Present a dropdown labelled `Unit:` with exactly five options, in
  this order: `mm`, `cm`, `m`, `inch`, `μm`.
- Pre-select the option whose implied multiplier matches the file's
  current `applied_scale`:

  | `applied_scale` | selected option |
  |---|---|
  | `1.0`    | `mm`   |
  | `10.0`   | `cm`   |
  | `1000.0` | `m`    |
  | `25.4`   | `inch` |
  | `0.001`  | `μm`   |

  For any other multiplier (e.g. an unrecognised power-of-10 from a
  legacy auto-rescale), the dropdown SHALL select `mm` and display a
  trailing badge `(actual ×<scale>)` so the operator is not misled.
- Display a `set by you` badge to the right of the dropdown when the
  file row has `user_unit_override IS NOT NULL`. The badge is absent
  when authority is the detector.
- Display an inline soft hint `⚠ Differs from file declaration (<unit>)`
  to the right of the dropdown whenever the currently selected
  option's multiplier disagrees with the source `INSUNITS` mapping
  (e.g. selected = `mm` but `insunits == 1` → hint says
  `Differs from file declaration (inch)`). The hint is informational
  only — it SHALL NOT disable the dropdown or block submission.
- Be disabled (greyed out, non-interactive) while a recompute job
  triggered by this picker is in flight; the in-flight job id SHALL
  be displayed adjacent to the dropdown so cross-session recovery
  works the same way as rule-check.

When the operator picks a value that differs from the currently
selected option, the viewer SHALL open a confirm modal **before**
firing any POST. The modal SHALL state, plainly:

1. Preprocess will re-run for this file.
2. Cached connectivity and pre-match for this file will be rebuilt.
3. Match JSON for every product containing this file will be cleared
   and need to be re-run; the modal SHALL include the count of
   affected products and the names of the first three (then "and N
   more" if applicable).
4. The override can be undone by picking the detector's choice
   again — the modal SHALL state which unit that is.

Only when the operator confirms the modal SHALL the viewer POST to
`/api/files/{file_id}/unit-override` and switch the picker into the
disabled / job-in-flight state.

#### Scenario: Picker default reflects detector-derived applied_scale
- **WHEN** a file with `applied_scale == 25.4` and `user_unit_override IS NULL` is opened in the viewer
- **THEN** the dropdown's selected option is `inch`
- **AND** no `set by you` badge is rendered
- **AND** if `insunits == 1`, no soft hint is rendered (selection matches declaration)

#### Scenario: Picker shows "set by you" when override is active
- **WHEN** a file with `user_unit_override == "mm"` and `applied_scale == 1.0` is opened
- **THEN** the dropdown's selected option is `mm`
- **AND** a `set by you` badge is rendered next to the dropdown

#### Scenario: Soft hint appears when selection contradicts INSUNITS
- **WHEN** a file with `insunits == 1` (inch) has its override set to `"mm"` and is opened
- **THEN** the dropdown's selected option is `mm`
- **AND** the inline hint reads `⚠ Differs from file declaration (inch)`

#### Scenario: Changing the picker opens the confirm modal first
- **WHEN** the operator picks a new unit different from the current selection
- **THEN** a confirm modal appears with the four enumerated points above
- **AND** the affected-products count and first-three names are shown
- **AND** no POST has been fired yet

#### Scenario: Cancelling the modal does not change state
- **WHEN** the confirm modal is open and the operator clicks cancel
- **THEN** the dropdown reverts to the prior selection
- **AND** no POST is fired
- **AND** the file row is unchanged

#### Scenario: Confirming the modal POSTs and disables the picker
- **WHEN** the operator confirms the modal
- **THEN** the viewer POSTs `{"unit": <selected>}` to `/api/files/{file_id}/unit-override`
- **AND** on `202`, the picker enters the disabled / job-in-flight state with the returned `job_id` shown
- **AND** on `409`, the picker enters the same disabled state but displays the conflict's `job_id`

#### Scenario: Picker re-enables after the recompute job completes
- **WHEN** the in-flight recompute job for this file finishes successfully
- **THEN** the picker becomes interactive again
- **AND** the dropdown re-selects the option matching the post-recompute `applied_scale`
- **AND** the `set by you` badge reflects the post-recompute `user_unit_override`

### Requirement: Dashboard rescaled pill annotates user-override origin

When the dashboard renders the `ℹ rescaled <human>` pill (per the
existing "Dashboard flags suspect unit scale on a per-file basis"
requirement), and the file row has `user_unit_override IS NOT NULL`,
the pill text SHALL be suffixed with ` (user override)`. The pill's
colour SHALL remain the same neutral informational style — the
suffix is the sole visible difference.

The per-file dashboard payload SHALL include a `user_unit_override`
field carrying the string value or `null`. Existing fields
(`applied_scale`, `unit_scale_warning`, `unit_scale_warning_detail`,
`insunits`) SHALL retain their existing semantics.

The pill's `title` text SHALL additionally include
`user_unit_override=<value>` when the override is set, so hover
inspection makes the origin explicit.

#### Scenario: Override-driven rescale shows the suffix
- **WHEN** a file with `applied_scale == 25.4` and `user_unit_override == "inch"` is rendered on the dashboard
- **THEN** the slot cell shows a pill reading `ℹ rescaled ×25.4 (inch) (user override)`
- **AND** the pill's `title` includes `"user_unit_override=inch"`

#### Scenario: Detector-driven rescale shows no suffix
- **WHEN** a file with `applied_scale == 0.001` and `user_unit_override IS NULL` is rendered on the dashboard
- **THEN** the slot cell shows a pill reading `ℹ rescaled ÷1000`
- **AND** the pill's `title` does not contain `"user_unit_override"`

#### Scenario: Override that lands at applied_scale == 1.0 shows no rescale pill but still annotates
- **WHEN** a file with `applied_scale == 1.0` and `user_unit_override == "mm"` is rendered on the dashboard
- **THEN** the existing requirement's rule applies — no rescale pill is shown (because `applied_scale == 1.0`)
- **AND** the payload still carries `"user_unit_override": "mm"` for clients that need it

### Requirement: Save Match button is non-blocking

The viewer's Save Match button SHALL submit
`POST /api/files/{file_id}/match-json`, expect an **HTTP 202**
response carrying `{"job_id": "<uuid>", ...}`, and SHALL poll
`GET /api/jobs/{job_id}` until the job reaches a terminal state
(`done` or `error`). The button SHALL remain disabled and a
saving-in-progress status SHALL be visible from the moment the
POST fires until the terminal state is reached. While a Save
Match job is in flight the viewer SHALL suppress further
invocations of Save Match against the same file — clicking the
button is a no-op until the in-flight job resolves.

On `done`, the status line SHALL summarise the result using fields
from `job.result` (at minimum `template_keys.length` and
`total_matches`, and the `saved_to` relative path), the local
`currentFileInfo.match_saved` flag SHALL be set to `true`, and the
role switcher's per-file readiness indicator SHALL be refreshed.
On `error`, the status line SHALL surface `job.error` (or a
generic message if missing) and the in-flight guard SHALL be
released without flipping `match_saved`. In both terminal cases
the button SHALL re-enable.

Polling SHALL run at a cadence of approximately 500 ms — fast
enough that the operator perceives the completion as immediate,
slow enough to avoid request flooding. Transient `GET /api/jobs/`
failures SHALL NOT abort the poll loop; the loop SHALL retry on
the next tick until the underlying job is observed or the user
navigates away.

#### Scenario: POST locks the button and starts polling
- **WHEN** the operator clicks Save Match while no job is in
  flight for the current file
- **THEN** the viewer fires `POST /api/files/{file_id}/match-json`
- **AND** on a `202` response the button becomes `disabled`
- **AND** the status line shows a saving-in-progress message
- **AND** the viewer begins polling `GET /api/jobs/{job_id}`

#### Scenario: Double-click while saving is suppressed
- **WHEN** a Save Match job for the current file is already in
  flight
- **AND** the operator clicks Save Match again
- **THEN** no additional `POST /api/files/{file_id}/match-json` is
  fired
- **AND** the polling loop continues against the original
  `job_id`

#### Scenario: Job done updates status and unlocks the button
- **WHEN** the polled job transitions to `status: "done"`
- **THEN** the status line summarises the result using
  `job.result.template_keys`, `job.result.total_matches`, and
  `job.result.saved_to`
- **AND** `currentFileInfo.match_saved` is set to `true`
- **AND** the role switcher is refreshed
- **AND** the Save Match button is no longer `disabled`

#### Scenario: Job error surfaces error and unlocks the button
- **WHEN** the polled job transitions to `status: "error"`
- **THEN** the status line surfaces `job.error`
- **AND** `currentFileInfo.match_saved` is not changed
- **AND** the Save Match button is no longer `disabled`

#### Scenario: Transient poll failure does not abort the loop
- **WHEN** `GET /api/jobs/{job_id}` returns a transient network
  failure during polling
- **AND** the next poll succeeds
- **THEN** the polling loop continues until the job reaches a
  terminal state
- **AND** the button remains disabled across the transient
  failure

### Requirement: Dashboard surfaces DXF recover notes

The per-file dashboard payload (the JSON returned by `GET /api/files`
and `GET /api/files/{file_id}`) SHALL include the field
`dxf_recover_notes`, mirroring the value stored in
`FileRecord.dxf_recover_notes`. The field SHALL be `null` for files
that parsed via strict mode and a JSON object for files that took
the recover fallback. When present the object SHALL carry, at
minimum, the keys `strict_error`, `n_fixed`, `n_unrecoverable`, and
`audit_messages` (a list).

For each file shown on the dashboard, when `dxf_recover_notes` is
non-null the slot cell SHALL display a neutral informational pill
reading `ℹ recovered (Nfixed/Munrecoverable)` — where `Nfixed` is
`n_fixed` and `Munrecoverable` is `n_unrecoverable`. The pill's
visual style SHALL match the existing `ℹ rescaled` pill (same
colour family, same monospace label form, same neutral chrome).
The pill's `title` attribute SHALL include the value of
`strict_error` so hover inspection surfaces the original parser
error.

When the file ALSO carries a `rescaled` pill (the existing
unit-scale pattern) the dashboard SHALL render both pills side by
side; the recover pill SHALL appear after the rescale pill in
visual order.

#### Scenario: Strict-OK file shows no recover pill
- **WHEN** a file with `dxf_recover_notes IS NULL` is rendered on
  the dashboard
- **THEN** no recover-related pill is shown on its slot cell
- **AND** the file's payload includes `"dxf_recover_notes": null`

#### Scenario: Recovered file shows a recover pill with counts
- **WHEN** a file with
  `dxf_recover_notes == {"strict_error": "DXFStructureError: …",
  "n_fixed": 12, "n_unrecoverable": 1, "audit_messages": […]}`
  is rendered on the dashboard
- **THEN** the slot cell shows a pill reading
  `ℹ recovered (12/1)`
- **AND** the pill's `title` attribute contains
  `"DXFStructureError: …"`

#### Scenario: Recovered file with rescale also rendered shows both pills
- **WHEN** a file carries both a non-null `dxf_recover_notes` and
  `applied_scale != 1.0`
- **THEN** the slot cell shows the rescale pill first, then the
  recover pill
- **AND** both pills use the same neutral informational style

### Requirement: Rule-check modal distinguishes locatable from text-only sub-rules

The rule-check results modal (`showRuleResults` in
`app/static/dashboard.js`) SHALL classify each sub-rule it renders
as **locatable** or **text-only**:

- A sub-rule is **locatable** when at least one of its handle
  fields — `from`, `to`, or `tol` — is non-null. (Per the DRC
  integration contract, a well-formed sub-rule is always locatable;
  this classification stays defensive against malformed emit.)
- A sub-rule is **text-only** when all three handle fields are
  null or missing.

For each sub-rule row:

- **Locatable rows** SHALL render with a `🎯` glyph prefix and
  SHALL include the existing clickable `View in <PART> →` link
  pointing at `/viewer/<file_id>?rule=<name>&idx=<i>`, unchanged
  from prior behaviour.
- **Text-only rows** SHALL render with an `ℹ` glyph prefix, the
  text in a dimmed style (lower opacity than locatable rows), and
  SHALL NOT render a `View in <PART> →` link — the click would
  not produce any highlight in the viewer.
- The existing "**PART not uploaded**" branch (no file uploaded
  for the role) SHALL remain unchanged: no link, no glyph swap,
  the message text reads as before.

Each rule card header SHALL additionally show a small chip
summarising sub-rule locator counts:

- When `rule.rules` is a non-empty list, the chip text SHALL read
  `🎯 N · ℹ M`, where N is the count of locatable sub-rules in
  this rule and M is the count of text-only sub-rules. Either
  count MAY be zero; both are displayed.
- When `rule.rules` is empty, the chip SHALL read `ℹ no locator`.
- The chip SHALL use the same neutral-informational style as the
  existing `rescaled` / recover pills on the dashboard (the
  `.rescaled-pill` family).

These affordance changes SHALL NOT alter the underlying
rule-check JSON shape, the `?rule=&idx=` query parameter contract,
or the viewer's `focusedSubRule` highlight pipeline.

#### Scenario: All-locatable rule shows clickable rows with full chip
- **WHEN** a rule card renders for a rule whose `rules` list
  contains three sub-rules, each with `from` non-null
- **THEN** all three sub-rule rows are prefixed with `🎯`
- **AND** each row renders a clickable `View in <PART> →` link
- **AND** the rule card header chip reads `🎯 3 · ℹ 0`

#### Scenario: All-text-only rule dims the rows and hides links
- **WHEN** a rule card renders for a rule whose `rules` list
  contains two sub-rules, neither carrying `from`, `to`, nor `tol`
- **THEN** both sub-rule rows are prefixed with `ℹ`
- **AND** both rows render in the dimmed text-only style
- **AND** neither row renders the `View in <PART> →` link
- **AND** the rule card header chip reads `🎯 0 · ℹ 2`

#### Scenario: Mixed rule shows mixed icons and bucketed chip counts
- **WHEN** a rule card renders for a rule whose `rules` list has
  two locatable sub-rules and one text-only sub-rule
- **THEN** the two locatable rows are prefixed with `🎯` and
  clickable
- **AND** the one text-only row is prefixed with `ℹ` and dimmed
  with no link
- **AND** the rule card header chip reads `🎯 2 · ℹ 1`

#### Scenario: Empty-rules rule shows the no-locator chip
- **WHEN** a rule card renders for a rule whose `rules` list is
  empty
- **THEN** the existing "No sub-rules emitted" empty-state row is
  preserved unchanged
- **AND** the rule card header chip reads `ℹ no locator`

#### Scenario: PART not uploaded branch is preserved
- **WHEN** a sub-rule's referenced file is not uploaded to the
  product (no `file` resolves)
- **THEN** the row continues to render the existing
  `<PART> not uploaded` message
- **AND** the row is not prefixed with either `🎯` or `ℹ`
- **AND** no `View in <PART> →` link is rendered

#### Scenario: tol-only sub-rule is locatable
- **WHEN** a sub-rule has `from` and `to` null but `tol` non-null
- **THEN** the row is classified as locatable
- **AND** the row is prefixed with `🎯`
- **AND** the row renders the clickable `View in <PART> →` link

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

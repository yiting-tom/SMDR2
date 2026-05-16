## ADDED Requirements

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

When a circle primitive's screen-space radius
(`r * view.zoom / dpr`) is below `0.75` CSS pixels, the main draw pass
SHALL render the circle as a 1×1 device-pixel dot at the circle's
center. Dots SHALL be batched per color into a single `Path2D` and
flushed in one fill call per color bucket. Highlight passes
(scan-all / selection / etc.) SHALL continue to draw at fattened
stroke width regardless of LOD so highlighted dots remain visible.

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

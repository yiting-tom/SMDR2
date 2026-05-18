## MODIFIED Requirements

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

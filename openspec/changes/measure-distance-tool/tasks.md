## 1. Snap resolver (pure geometry)

- [x] 1.1 Add `resolveSnap(wx, wy)` in `canvas.js` returning `{x, y, kind, primIndex?}` for `endpoint | midpoint | nearest | free`, using the same `tol` formula as `pickIndexAt`.
- [x] 1.2 Walk `line`, `polyline`, `point`, and `filled_polygon` primitives (skip `decorative`) to enumerate vertex and segment-midpoint candidates with bbox cull.
- [x] 1.3 Implement priority order: endpoint within tol → midpoint within tol → perpendicular foot on segment within tol → free `(wx, wy)`.
- [x] 1.4 Factor a small `closestPointOnSegment` helper next to `distPointToSegmentSq` and reuse it for the nearest case. *(Reused the existing `closestPointOnSegment(p, a, b)` at canvas.js:493 — no duplicate added.)*
- [ ] 1.5 Unit-style smoke test in the browser console: snap to a known polyline endpoint, midpoint, and edge of a fixture file.

## 2. Mode state and pointer gating

- [x] 2.1 Add module-level `let measureMode = false;` and `let measureState = { first: null, frozen: null, snapHint: null };` near the existing `addMode` declarations.
- [x] 2.2 In each existing pointer handler, early-return when `measureMode` is true. *(Single-handler intercept: the left-button branch in `mousedown` routes to the measure click flow before any `click_pending` drag is created.)*
- [x] 2.3 Verify middle-drag pan and wheel zoom still operate while `measureMode` is true (no early-return for those).
- [x] 2.4 Guard `measureMode` activation: refuse to enter if `addMode` is active; refuse to enter add-mode while `measureMode` is active.

## 3. Click + move handling in measure mode

- [x] 3.1 In `mousedown` measure branch, call `resolveSnap` and either set `measureState.first` or freeze the dimension into `measureState.frozen` and reset `first`.
- [x] 3.2 In `mousemove`, when `measureMode` is on and no drag is active, update `measureState.snapHint = resolveSnap(wx, wy)` and trigger a redraw.
- [x] 3.3 Ensure freezing a measurement does not mutate `selection`.

## 4. Rendering

- [x] 4.1 Add a `drawMeasureOverlay()` call at the end of `render()` after `ctx.restore()`, alongside `drawFocusedLabel`.
- [x] 4.2 Draw the snap marker per `kind`: square (endpoint), triangle (midpoint), X (nearest); skip if `free`.
- [x] 4.3 Draw the rubber-band line: dashed `[6, 4]` while pending second click, solid when `frozen`. Use screen-space stroke widths.
- [x] 4.4 Draw endpoint dots on both picked points when `frozen`.
- [x] 4.5 Implement the floating readout as an absolutely-positioned HTML element inside `<main>`; update its content with total distance, Δx, Δy (3 dp, trim trailing zeros).

## 5. Activation surfaces

- [x] 5.1 Add a "Measure" button to the viewer header in `viewer.html`; wire its click to toggle measure mode.
- [x] 5.2 Add the `D` hotkey in the existing `keydown` handler; toggles measure mode, no-op when `addMode` is active.
- [x] 5.3 Update the status hint element to display `MEASURE · pick first point` / `MEASURE · pick second point (Shift = ortho)`.
- [x] 5.4 Add a styled `.measure-readout` rule in `style.css`. *(Reused the existing global `header button.active` rule for the button — no separate `.measure-btn.active`.)*

## 6. Esc cascade and tool exit

- [x] 6.1 Insert a measure-clearing step in the Esc cascade before the scan-all close step.
- [x] 6.2 Toggling measure mode off (button or `D`) clears `measureState.first`, `frozen`, and `snapHint`, then forces a redraw.
- [x] 6.3 No cursor override on enter, so nothing to restore; `selection` is never touched by the measure flow.

## 7. Shift = ortho modifier

- [x] 7.1 Add `applyOrtho(wx, wy, shiftKey)` helper that returns an axis-locked snap when Shift is held and `measureState.first` exists; null otherwise.
- [x] 7.2 Wire `applyOrtho(...) ?? resolveSnap(...)` into both the measure `mousedown` click flow and the measure `mousemove` snap-hint update.
- [x] 7.3 Update the mode hint to read `MEASURE · pick second point (Shift = ortho)` after the first pick.
- [x] 7.4 Cache `measureState.lastCursor` on every measure-mode `mousemove`, and add `keydown`/`keyup` listeners that re-resolve when `Shift` transitions, so ortho engages/disengages without mouse motion.
- [x] 7.5 In `applyOrtho`, run `resolveSnap` and take the on-axis coordinate from the snap target (X under horizontal lock, Y under vertical); fall back to the raw cursor only when no OSNAP candidate is found. Preserve the snap's original `kind` so the marker still renders.
- [x] 7.6 Pick the axis from the raw cursor delta — not the snap delta — so the lock direction stays stable as the cursor approaches a target.

## 8. Circle CEN + QUA snap

- [x] 8.1 Add `detectCircle(pts)` returning `{cx, cy, r}` or `null` based on the ≥ 8 vertex + `(rmax − rmin)/rmean ≤ 0.02` heuristic.
- [x] 8.2 Add `primCircles[]` parallel to `primitives`, populated by `computePrimCircles()` for closed polylines and single-ring filled_polygons. Call it from `load()` right after `computeBBoxes()`.
- [x] 8.3 In `resolveSnap`, when `primCircles[i]` is non-null, contribute center + 4 quadrants + nearest-on-true-perimeter, and `continue` past the vertex/midpoint enumeration for that primitive.
- [x] 8.4 Extend the OSNAP priority chain to `endpoint → midpoint → center → quadrant → nearest → free`.
- [x] 8.5 Add center (circle + small cross-hair) and quadrant (diamond) markers in `drawSnapMarker`.

## 9. Continuous chaining

- [x] 9.1 Replace `measureState.first` / `measureState.frozen` with `measureState.picks: []`; add a `measureAnchor()` helper returning the last pick or null.
- [x] 9.2 In the measure `mousedown` branch, append the snap-resolved point to `picks` (no second-click freeze logic).
- [x] 9.3 In `drawMeasureOverlay`, draw a solid segment between each consecutive pair of picks, plus endpoint dots on every pick, plus a dashed rubber-band from `picks[last]` to the snap cursor.
- [x] 9.4 In `updateMeasureReadout`, compute the live segment from `anchor → snap`, and when `picks.length ≥ 2` also display a running total `Σ` summing all frozen segments + the live segment.
- [x] 9.5 Update the status hint to read `MEASURE · pick first point` for 0 picks, `MEASURE · pick next point · N pts (Shift = ortho, Esc to clear)` for N ≥ 1.
- [x] 9.6 Update Esc cascade and `exitMeasureMode` to clear `picks` instead of `first`/`frozen`.
- [x] 9.7 Update Shift `keydown`/`keyup` listeners and `applyOrtho` to read the chain anchor via `measureAnchor()`.
- [x] 9.8 Add a `.m-total` style block in `style.css` so the running total stands out from the per-segment deltas. *(Reverted — running total moved onto the live segment's canvas label in step 9b.)*

## 9b. Per-segment midpoint labels (replaces the readout-centric design)

- [x] 9b.1 Add `drawSegmentLabel(a, b, text)` that renders a yellow-on-black text box at the perpendicular-offset midpoint of the segment, mirroring `drawFocusedLabel` styling.
- [x] 9b.2 In `drawMeasureOverlay`, label every frozen segment with `fmtCoord(d)`, and label the live segment with `fmtCoord(d)` plus ` · Σ=<total>` once `picks.length ≥ 2`.
- [x] 9b.3 Simplify `updateMeasureReadout` to show only Δx / Δy for the live segment; drop the `d` and `Σ` lines (now on canvas labels) and remove the related `.m-d` / `.m-total` CSS rules.

## 10. Verification (browser, manual)

- [ ] 10.1 Open a real DXF in the viewer, press `D`, snap-pick two endpoints across a BGA ball edge; confirm distance matches a quick cross-check.
- [ ] 10.2 Verify endpoint > midpoint > nearest precedence by hovering near an L-junction (endpoint within pickbox of two segments).
- [ ] 10.3 Verify selection is preserved across an enter-measure / pick / exit cycle.
- [ ] 10.4 Verify `Esc` cascade: with a frozen dimension and a selection, one `Esc` clears the dimension; a second `Esc` clears the selection.
- [ ] 10.5 Verify class hotkeys (`1`..`p`) are no-ops while measure mode is active, and `D` is a no-op while in add-mode.
- [ ] 10.6 Verify the readout repositions correctly after toggling library modal / resizing the window.
- [ ] 10.7 Hold Shift after the first pick; confirm the rubber-band locks to H or V depending on cursor direction.
- [ ] 10.8 With the cursor stationary off-axis, press Shift; confirm the rubber-band snaps to the axis line immediately without moving the mouse.
- [ ] 10.9 With Shift held, hover near a vertex that is off-axis from the first point; confirm the rubber-band endpoint snaps to (vertex.X, first.Y) under horizontal lock (or (first.X, vertex.Y) under vertical lock), and the endpoint marker renders at that projected position.
- [ ] 10.10 Hover the cursor over a BGA-ball-style circle; confirm a circle marker appears at its center and the snap locks there.
- [ ] 10.11 Hover near the top / right / bottom / left edge of a circle; confirm a diamond marker appears at the quadrant point.
- [ ] 10.12 Hover on a rounded-rectangle silkscreen; confirm it is NOT detected as a circle (no center/quadrant markers; falls back to endpoint/midpoint/nearest on its segments).
- [ ] 10.13 Click 4 points along a row of SMD pads; confirm 3 solid frozen segments + 1 dashed rubber-band render, and the readout shows both the live segment values and `Σ`.
- [ ] 10.14 With a 3-segment chain on screen, press Esc; confirm all picks and segments clear in one keystroke (no need to press Esc multiple times).
- [ ] 10.15 Confirm each segment shows its `d` at the midpoint and the live segment additionally shows `· Σ=<total>` once a second pick exists.
- [ ] 10.16 Confirm the HTML readout near the cursor shows only Δx / Δy, not `d` or `Σ` (those moved onto the canvas labels).

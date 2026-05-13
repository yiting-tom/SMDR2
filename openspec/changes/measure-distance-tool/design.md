## Context

The viewer (`app/static/canvas.js` + `app/templates/viewer.html`) already
ships every primitive of the open file to the client and renders them with a
single screen↔world transform (`view.zoom` + `view.pan`). The same transform
feeds `pickIndexAt(wx, wy)`, which uses `distPointToSegmentSq` and a
configurable pickbox in CSS px (`PICKBOX_CSS_PX`). All geometry needed for
distance measurement is already client-side — the backend is not involved.

The viewer is a single global-state JS file: there is no formal mode enum.
Modes today are encoded as combinations of mutable booleans (`addMode`,
`chainMode`, scan-all flag, drag state). Measure mode needs to slot in here
without forcing a wholesale refactor.

Stakeholders: the packaging engineer using the viewer is fluent in AutoCAD,
expects `D` to map to `DIST`, and expects OSNAP markers exactly where AutoCAD
puts them — see [[feedback_autocad_ux]].

## Goals / Non-Goals

**Goals:**
- Two-click distance measurement in world units, with live rubber-band.
- OSNAP for endpoint, midpoint, and nearest-on-edge using the same pickbox
  tolerance the rest of the viewer uses, so picks feel consistent.
- Tool is fully read-only and round-trippable: enter, use, exit — nothing
  about selection/library/add-mode changes.
- Hotkey + button parity (every interaction reachable from both).
- Implementation lives entirely in existing files (no new modules); changes
  to `canvas.js` are scoped to a clearly demarcated "measure" section.

**Non-Goals:**
- Perpendicular snap, tangent snap, intersection snap — these can come later.
- Quadrant snap at non-cardinal angles (45°, 22.5°, etc.) — only the four
  cardinal points are emitted, since the typical use case is measuring
  axis-aligned distances between BGA balls.
- Multi-segment chained measurement (AutoCAD `MEASUREGEOM > Continuous`).
- Persisting measurements to the file, library, or any data store.
- Unit conversion / display in non-native units.
- Measuring arcs/circles by true arc length (the DXF pipeline already
  flattens these to polylines, so we measure on the flattened geometry).
- Touch / mobile gestures.

## Decisions

### D1. Activation: dedicated `measureMode` boolean, mutually exclusive with `addMode`

We add a single boolean `measureMode` to canvas.js global state, plus a
`measureState` object holding `{ first: [wx, wy] | null, frozen: {...} |
null, snapHint: {...} | null }`.

- Entering measure mode requires `!addMode`. Pressing `D` while in add-mode
  is a no-op (the spec's "blocked during add-mode" scenario).
- Entering measure mode does **not** clear the current `selection` set; it
  only changes which pointer handlers are routed.
- The Esc cascade is extended to: `cancel drag → clear measure → close
  scan-all → exit add-mode → clear selection`. Measure is inserted between
  drag-cancel and scan-all so a frozen dimension can be cleared without
  losing add-mode if the user happens to also be staging a class — though
  the spec forbids that combination, we keep the ordering defensive.

**Alternatives considered:**
- *A formal `viewerMode` enum.* Cleaner long-term but a large refactor; the
  existing code is two booleans, not five. Deferred.
- *Make measure a sub-mode of add-mode.* Rejected — measure is read-only and
  conceptually orthogonal to staging a template.

### D2. Snap resolver: pickbox-scoped, priority-ordered, reuses existing helpers

We add `resolveSnap(wx, wy) -> { x, y, kind: 'endpoint'|'midpoint'|'nearest'|'free' }`.

Algorithm, all within `tol = PICKBOX_CSS_PX * dpr / view.zoom` (same as
`pickIndexAt`):
1. Bbox-cull primitives like `pickIndexAt` does.
2. For each surviving primitive, collect candidate snap points:
   - line/polyline: each vertex (endpoint candidate), each segment midpoint.
   - point: its `pos` (endpoint candidate).
   - filled_polygon: each ring vertex (endpoint candidate) + each edge
     midpoint. (Center snap is out of scope.)
3. Pick the closest endpoint candidate within `tol`. If found → return.
4. Else pick the closest midpoint candidate within `tol`. If found → return.
5. Else compute the closest perpendicular foot on any segment within `tol`
   (reuse the `distPointToSegmentSq` math, but return the foot, not the
   distance). If found → return `nearest`.
6. Else return `free` with `(wx, wy)`.

This keeps snap behavior consistent with what the user already trusts from
pickbox-based single-pick. The decorative-skip rule (`p.decorative`) is also
respected, so axis labels and similar overlays never snap.

**Alternatives considered:**
- *Build a separate snap spatial index.* Premature — `pickIndexAt` is already
  a linear scan with bbox cull and is fast enough on real DXFs.
- *Snap to grid.* Out of scope; AutoCAD `DIST` doesn't enforce grid snap by
  default, and the viewer has no grid.

### D3. Rendering: piggy-back on the existing `draw()` redraw loop

Measure overlay is drawn at the end of `draw()` after entities and selection
highlights, in screen space (so the dashed pattern is stable under zoom):

- Snap marker (12 CSS px shapes, 1.5 px stroke, contrast color).
- Rubber-band line: dashed (`[6, 4]` CSS px) while live; solid when frozen.
- Endpoint markers on both picked points when frozen.
- Floating readout: HTML overlay positioned via `style.left/top` against the
  canvas's bounding rect, so we don't have to deal with canvas font metrics
  or measurement-text bounding boxes. Falls back to existing
  `ctx.measureText` if HTML overlay proves janky on resize.

`pointermove` already triggers redraws when relevant; we extend the existing
handler so that in measure mode it also re-resolves snap and schedules
`requestAnimationFrame(draw)`.

### D4. Pointer routing: gate selection handlers in measure mode

The existing pointer handlers (single pick, shift-toggle, window/crossing
drag) check `if (measureMode) return;` early and route to a measure-specific
click handler instead. Wheel-zoom and middle-drag pan continue to work
unchanged — AutoCAD lets you pan/zoom mid-`DIST`.

### D5. Hotkey: `D` (single key), not chord

`D` is currently unused in the viewer hotkey table (`1`-`0`, `q`-`p` are
class hotkeys; `S`, `A`, `Enter`, `Esc` are workflow keys). It maps to
AutoCAD `DIST`. We do not introduce a Ctrl/Cmd chord to avoid colliding with
browser shortcuts (Cmd-D = bookmark).

### D6. Ortho via Shift, dominant-axis lock, OSNAP runs on the on-axis coordinate

Holding `Shift` between the two picks constrains the candidate second point
to the dominant axis from the first point (lock `y = fy` when `|Δx| ≥ |Δy|`,
else lock `x = fx`). Implementation lives in `applyOrtho(wx, wy, shiftKey)`
which returns either an ortho snap or `null` so callers can chain
`applyOrtho(...) ?? resolveSnap(...)`.

OSNAP still runs under ortho: `applyOrtho` calls `resolveSnap` internally
and, when a non-free candidate is found, uses the snap's on-axis coordinate
(`snap.x` under horizontal lock, `snap.y` under vertical) while keeping the
off-axis coordinate locked to the first point. The marker is drawn at the
projected position with the OSNAP's original kind so the user sees which
real target supplied the on-axis value. When no OSNAP fires, we fall back
to the raw cursor's on-axis coordinate and a "free" kind (no marker).

The axis decision (horizontal vs vertical) uses the **raw cursor delta**
rather than the snap delta, so the lock direction stays stable while the
cursor moves *toward* a snap target. Without this, swinging past an OSNAP
candidate could flip the lock axis mid-motion.

Live ortho without mouse motion: we cache the last cursor world position in
`measureState.lastCursor` on every `mousemove`, then re-resolve from a small
`keydown`/`keyup` listener when the `Shift` key transitions. Without this,
the rubber-band would only update on mouse jitter, which feels broken when
the user is holding the mouse still and toggling the modifier.

**Alternative considered:** "OSNAP candidates that lie ON the ortho line are
honored verbatim, otherwise free cursor on axis." This is closer to
AutoCAD's literal behavior, but in our pickbox-scoped world it's rare for a
real vertex to land exactly on the axis line, so the feature would almost
never trigger. Projecting the on-axis coordinate gives the user the
*intended* snap value (the X or Y of the target) on every measurement.

### D7. Circle detection client-side, lazy at load, replaces vertex/midpoint candidates

The DXF pipeline (`app/dxf.py`) flattens DXF `CIRCLE` / arc-bearing entities
to closed polylines via ezdxf's `Frontend` + `draw_path` — no `circle`
primitive type and no center/radius metadata ever reach the client. So
recovery has to happen client-side, on the rendered polyline data we already
have in memory.

**Heuristic.** A closed `polyline` (or single-ring `filled_polygon`) is a
circle when (a) it has ≥ 8 vertices and (b) the radial distances from the
centroid satisfy `(rmax − rmin) / rmean ≤ 0.02`. The threshold is tight on
purpose: ezdxf's default flattening tolerance (0.01 mm in our pipeline)
produces radial errors that are essentially zero, while a rounded-rectangle
(common false-positive candidate) has long straight sides whose vertices
sit far from any circumscribing circle, easily failing the radial check.

**Caching.** Detection runs once during `load()`, right after
`computeBBoxes()`, populating a parallel `primCircles[]` array (`null` or
`{ cx, cy, r }`). Cost is O(N) over vertices, amortised against the rest of
the bootstrap.

**Snap targets.** When `primCircles[i]` is non-null the resolver skips that
primitive's vertex / segment enumeration entirely and emits only:
- Center at `(cx, cy)`
- Quadrants at `(cx ± r, cy)` and `(cx, cy ± r)` — 4 cardinal points
- Nearest-on-perimeter at the closest point on the true `(cx, cy, r)`
  circle to the cursor

This avoids the polyline's flattening vertices polluting the endpoint and
midpoint candidate sets — those snaps would otherwise fire at arbitrary
points on the ball perimeter, which is rarely what the user wants.

**Priority.** Endpoint → Midpoint → Center → Quadrant → Nearest → Free.
Endpoint stays first so "real" geometric endpoints (line ends, pad corners)
still win against a center snap that happens to land in the same pickbox.

**Alternatives considered:**
- *Recover circles from DXF metadata server-side.* Would require parallel
  primitive types (`circle` etc.) and a second-pass through the matching
  pipeline. Heavier and out of scope.
- *Detect at first OSNAP call, then cache.* Same outcome, but `load()` is
  already the natural hook and the eager pass is fast enough.
- *Skip nearest-on-perimeter for circles.* Pondered — without it, hovering
  the cursor on a circle's edge but outside any pickbox of center/quadrant
  would fall through to "free" cursor instead of snapping to the actual
  perimeter. The current implementation gives the user a reliable edge
  snap on arbitrary angles, which is consistent with the non-circle case.

### D8. Formatting: 3 decimal places, locale-independent

`Number.prototype.toFixed(3)` with trailing-zero trimming. dx and dy keep
their signs. Units are whatever the file's world units already are (the
viewer doesn't track explicit units today, so the readout is unit-less).

## Risks / Trade-offs

- **Risk:** Snap to midpoint on a very long segment can feel "magnetic" —
  user wanted nearest-on-edge but got pulled to the midpoint. → **Mitigation:**
  midpoint snap only fires when the *midpoint itself* is inside the pickbox,
  not just the segment, matching AutoCAD's MID behavior.
- **Risk:** Floating HTML readout drifts on canvas resize. → **Mitigation:**
  reposition the readout inside the existing `ResizeObserver` callback that
  already keeps canvas dimensions in sync.
- **Risk:** Measure mode held open across a library switch (which reloads
  the page) leaves stale UI state in `sessionStorage`. → **Mitigation:** we
  do not persist `measureMode` anywhere; page reload starts cold.
- **Trade-off:** Implementing as boolean rather than a mode enum trades
  future cleanliness for shipping speed. Acceptable given the small surface
  area and the existing convention in this file.
- **Trade-off:** No center / perpendicular / tangent snap in v1. Users
  rounding a BGA ball center will currently have to eyeball it; if that
  shows up as a real pain point we add center snap as a follow-up.

### D9. Continuous chaining via `picks[]` array

The measure tool is **continuous**: each click appends to
`measureState.picks` (a flat array of `[x, y]` world points). Frozen
segments are the consecutive pairs `picks[i], picks[i+1]`; the live
rubber-band runs from `picks[last]` to the snap-resolved cursor.

This replaces the original two-click model. Reason: the user's primary
workflow is measuring multi-segment paths through SMD pads and BGA arrays;
forcing a re-enter between segments breaks flow. Esc clears the whole chain
in one step (no "undo one pick" yet — see below).

**Alternatives considered:**
- *Keep the two-click + multiple independent measurements model
  (Shift+D = "add another").* Heavier UI, doesn't match AutoCAD muscle
  memory — AutoCAD users don't think in terms of "discrete measurements".
- *Undo-one-pick (e.g., right-click or Backspace).* Useful but deferred —
  Esc-and-restart is fast enough in practice; can revisit if users ask.

### D10. Per-segment midpoint labels on the canvas, HTML carries Δx/Δy only

Each measurement segment carries its own midpoint label rendered directly
on the canvas (yellow text on black box with yellow border, mimicking the
existing `drawFocusedLabel` rule-check overlay for visual consistency).
The label sits at the perpendicular offset (~10 device-px) from the
segment, so it doesn't overlap the line.

The live segment label additionally appends `· Σ=<total>` once two or more
picks exist, keeping the running total visible next to the line the user
is actively drawing. Earlier iterations of this change kept the running
total only in a floating HTML readout near the cursor; moving `Σ` to the
live segment label keeps measurement results on the drawing itself, in
line with [[feedback_autocad_ux]].

The HTML readout now carries only `Δx` and `Δy` for the live segment —
those are awkward to fit on a single midpoint label and useful for
verifying axis-aligned picks (especially with `Shift` ortho lock). `d` and
`Σ` are intentionally not duplicated in HTML; the user's eye goes to the
segment, not to the floating panel.

**Trade-off:** for very short on-screen segments, the label can overflow
the line length and visually dominate. Acceptable for v1 — the user can
zoom in for closer reads. A future refinement could shrink the font or
hide the label below a length threshold.

## Open Questions

- Should the readout also display **angle** (AutoCAD `DIST` shows angle in
  XY plane)? Lean yes, but holds until follow-up if it adds UI complexity.
- Should there be an "undo last pick" gesture (Backspace / right-click)?
  Not in v1; revisit after real-use feedback.

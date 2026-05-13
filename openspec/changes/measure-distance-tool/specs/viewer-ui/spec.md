## ADDED Requirements

### Requirement: AutoCAD-style measure-distance tool

The viewer SHALL provide an AutoCAD `DIST`-style measure-distance tool that
lets the user pick two world points and read off the distance between them
without modifying selection or library state.

Activation:
- A "Measure" button in the viewer header (or class toolbar area) SHALL toggle
  the tool on/off.
- The `D` hotkey SHALL toggle the tool, mirroring AutoCAD's `DIST` command.
- While the tool is active, the status hint SHALL read
  `MEASURE · pick first point` when no pick exists, and
  `MEASURE · pick next point · N pts (Shift = ortho, Esc to clear)` once
  the chain has `N ≥ 1` picks.

OSNAP (object-snap) targets, evaluated in priority order within the existing
pickbox tolerance:
1. **Endpoint** — endpoints of `line`, open `polyline` segments, and `point`
   primitives, plus every polyline vertex (except for primitives detected as
   circles — see below).
2. **Midpoint** — the midpoint of every line segment / polyline segment
   currently under the pickbox (excluding circle-detected primitives).
3. **Center** — for primitives detected as circles, the centroid of the
   arc-flattened polyline.
4. **Quadrant** — for primitives detected as circles, the 4 cardinal points
   on the perimeter at `(cx±r, cy)` and `(cx, cy±r)` (top, bottom, left,
   right). Intermediate angles are out of scope.
5. **Nearest-on-edge** — the perpendicular foot from the cursor onto the
   nearest segment, when no higher-priority snap is available. For circle
   primitives, this is the closest point on the true perimeter circle.
6. **Free point** — if no primitive lies in the pickbox, the world position
   of the cursor itself.

Circle detection: a closed `polyline` (or single-ring `filled_polygon`) with
≥ 8 vertices whose distances from the centroid have
`(max − min) / mean ≤ 0.02` SHALL be treated as a circle. Detected circles
SHALL contribute only Center / Quadrant / Nearest-on-perimeter candidates —
their individual polyline vertices and segment midpoints SHALL NOT be
emitted as Endpoint / Midpoint snap candidates, because they are
arc-flattening artefacts rather than meaningful geometry.

The active snap target SHALL be drawn as a small marker at the resolved
world position so the user can confirm what will be picked:

- Endpoint → square
- Midpoint → triangle
- Center → circle (with small cross-hair inside)
- Quadrant → diamond
- Nearest → X
- Free → no marker

Rubber-band:
- After the first click, a dashed line SHALL be drawn from the most recent
  pick (the chain's current anchor) to the live cursor (post-snap)
  position, redrawing on every `pointermove`. Frozen segments SHALL be
  drawn as solid lines.

Readout: covered under "Continuous chaining" above.

Ortho (Shift) modifier:
- Between the first and second click, holding `Shift` SHALL constrain the
  candidate second point to the dominant axis from the first point — when
  `|wx − fx| ≥ |wy − fy|` the candidate Y is locked to `fy` (horizontal);
  otherwise candidate X is locked to `fx` (vertical). Mirrors AutoCAD's
  Shift = Ortho modifier inside `DIST`.
- OSNAP SHALL continue to operate under ortho lock. When an OSNAP candidate
  is found within tolerance of the cursor, its **on-axis** coordinate
  (the X under horizontal lock, the Y under vertical lock) SHALL be used,
  while the off-axis coordinate SHALL remain locked to the first point.
  The snap marker SHALL be drawn at this projected position using the
  original OSNAP kind (square / triangle / X), so the user can see which
  target supplied the on-axis value.
- When no OSNAP candidate is found, the on-axis coordinate SHALL fall back
  to the raw cursor and the marker SHALL be suppressed (free point).
- Toggling Shift up/down between picks SHALL re-resolve the rubber-band
  immediately without requiring mouse motion.

Continuous chaining (no explicit "finalize"):
- Each click appends a world point to the chain of picks. The first click
  starts the chain; every subsequent click freezes a segment from the
  previous pick to the new pick, and the new pick becomes the anchor for
  the next rubber-band.
- All frozen segments SHALL remain rendered on the canvas with endpoint
  dots on every picked point, until the chain is cleared.
- Each segment (frozen and live) SHALL carry a **midpoint label** drawn at
  the perpendicular offset from the line, showing the segment's distance
  (3 decimal places, trailing zeros trimmed). The live segment's label
  SHALL additionally append `· Σ=<total>` once the chain has ≥ 2 picks,
  where `Σ` is the sum of all frozen segments + the live segment.
- A floating HTML element near the cursor SHALL show only `Δx` and `Δy`
  for the live segment — `d` and `Σ` already live on the canvas labels.
- `Esc` SHALL clear the entire chain in one step and is inserted into the
  existing Esc cascade *before* the "close scan-all overlay" step.
- Toggling the tool off (button or `D` again) SHALL clear the chain and
  exit measure mode in one keystroke.

Read-only guarantee:
- While measure mode is active, left-click, shift+left-click, and left-drag
  SHALL NOT modify the current selection or initiate a window/crossing
  selection.
- Entering measure mode SHALL NOT change the current selection; exiting
  SHALL leave the selection unchanged.
- The tool SHALL NOT be enterable while add-mode is active; pressing `D`
  during add-mode SHALL be a no-op. Conversely, class hotkeys SHALL be a
  no-op while measure mode is active.

#### Scenario: Activate measure mode via hotkey

- **WHEN** the user is in the viewer with no add-mode active and presses `D`
- **THEN** the Measure button is marked active
- **AND** the status hint reads `MEASURE · pick first point`
- **AND** the canvas cursor reflects measure mode

#### Scenario: Endpoint snap wins over nearest-on-edge

- **WHEN** measure mode is active and the cursor is within the pickbox of
  both a line's endpoint and its mid-segment
- **THEN** the snap marker is drawn at the endpoint (square marker)
- **AND** clicking locks the first/second point exactly on the endpoint
  coordinates

#### Scenario: Midpoint snap on a line

- **WHEN** measure mode is active and the cursor hovers within the pickbox
  of a line segment but outside any endpoint pickbox
- **THEN** the snap marker is drawn at the segment midpoint (triangle marker)

#### Scenario: Center snap on a BGA-ball-style circle

- **WHEN** the file contains a closed polyline with 14 vertices approximating
  a circle of radius 0.4 mm centered at `(50, 50)`, and the cursor is inside
  that disk within pickbox tolerance of the centroid
- **THEN** the snap marker is drawn at `(50, 50)` (center kind, circle marker)
- **AND** clicking locks the picked point to `(50, 50)`

#### Scenario: Quadrant snap on a circle perimeter

- **WHEN** the cursor is within pickbox of the rightmost point `(50.4, 50)`
  of the same circle
- **THEN** the snap marker is drawn at `(50.4, 50)` (quadrant kind, diamond
  marker)
- **AND** clicking locks the picked point to `(50.4, 50)`

#### Scenario: Circle vertex midpoint does not snap

- **WHEN** the cursor is within pickbox of an arbitrary vertex of the
  circle-detected polyline (one that is *not* at a cardinal angle)
- **THEN** no endpoint or midpoint marker is drawn for that vertex
- **AND** the closest applicable snap is either center, quadrant, or
  nearest-on-perimeter

#### Scenario: Free point when nothing is in pickbox

- **WHEN** measure mode is active and the cursor is in empty space
- **THEN** no snap marker is drawn
- **AND** clicking locks the world position of the cursor as the picked point

#### Scenario: Rubber-band label updates with cursor

- **WHEN** the user has clicked the first point and moves the cursor
- **THEN** a dashed line is drawn from the first point to the current
  (snap-resolved) cursor position
- **AND** the live segment's midpoint label updates each frame to show the
  current distance
- **AND** the floating Δx / Δy panel near the cursor updates in sync

#### Scenario: Shift locks the second point to the dominant axis

- **WHEN** the user has clicked the first point at `(10, 10)`, moves the
  cursor to `(20, 12)` in empty space, and holds `Shift`
- **THEN** the rubber-band endpoint snaps to `(20, 10)` — horizontal lock
  because `|Δx| ≥ |Δy|`
- **AND** no OSNAP marker is drawn on the rubber-band endpoint (free point)

#### Scenario: Ortho + OSNAP pulls on-axis coordinate from a vertex

- **WHEN** the first point is `(10, 10)`, a polyline vertex sits at
  `(50, 12)`, the cursor is within pickbox of that vertex, and the user
  holds `Shift` (horizontal lock active because `|Δx| ≥ |Δy|`)
- **THEN** the rubber-band endpoint snaps to `(50, 10)` — X taken from the
  vertex, Y locked to the first point
- **AND** the endpoint OSNAP marker (square) is drawn at `(50, 10)` so the
  user sees that the on-axis coordinate came from a real vertex

#### Scenario: Shift down without mouse motion engages ortho

- **WHEN** a first point is set, the cursor is stationary off-axis, and the
  user presses `Shift`
- **THEN** the rubber-band immediately snaps to the axis-locked position
  without requiring a `mousemove`

#### Scenario: Second click freezes a segment and starts the next one

- **WHEN** the user clicks a second point in measure mode
- **THEN** the segment from the first to the second point becomes a solid
  line with endpoint dots on both points
- **AND** the second point becomes the new anchor for the live rubber-band
  to the cursor
- **AND** the status hint reads `MEASURE · pick next point · 2 pts (...)`
- **AND** the readout begins showing the running total `Σ` in addition to
  the live segment's `d`, `Δx`, `Δy`

#### Scenario: Continuous chain of 4 measurements

- **WHEN** the user clicks 4 points in measure mode at world positions
  `P1`, `P2`, `P3`, `P4`
- **THEN** 3 solid frozen segments are drawn — `P1→P2`, `P2→P3`, `P3→P4`
- **AND** each frozen segment has a midpoint label showing its `d`
- **AND** the dashed rubber-band extends from `P4` to the current cursor
- **AND** the live segment's midpoint label reads `<d> · Σ=<total>`, where
  the total equals `|P1P2| + |P2P3| + |P3P4| + |P4→cursor|`

#### Scenario: Esc clears the whole chain in one keystroke

- **WHEN** any number of picks are on the chain, no drag or scan-all
  overlay is active, and the user presses `Esc`
- **THEN** every pick and frozen segment is cleared in one step
- **AND** measure mode remains active

#### Scenario: Toggling the tool off clears the chain

- **WHEN** the user presses `D` (or clicks the Measure button) while measure
  mode is active with any number of picks
- **THEN** measure mode exits
- **AND** the entire chain is removed from the canvas
- **AND** the previous selection and pickbox behavior are restored

#### Scenario: Measure mode does not modify selection

- **WHEN** the user has a non-empty selection, enters measure mode, picks
  any number of points on entities that are not currently selected, and
  exits measure mode
- **THEN** the selection is identical to what it was before entering measure
  mode

#### Scenario: Measure mode blocked during add-mode

- **WHEN** the user is in add-mode for some class and presses `D`
- **THEN** measure mode does not activate
- **AND** add-mode remains active

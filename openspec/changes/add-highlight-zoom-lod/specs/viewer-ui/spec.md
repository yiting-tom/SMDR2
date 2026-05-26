## MODIFIED Requirements

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

#### Scenario: Zoom-out collapses BGA balls into batched dots
- **WHEN** the viewer is zoomed out enough that each BGA-ball circle is below `DOT_THRESHOLD_CSS_PX`
- **AND** `render()` runs
- **THEN** the status line reports a non-zero `dot` count for the just-completed frame
- **AND** every dot remains visible at its world position

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

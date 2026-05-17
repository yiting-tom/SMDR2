## ADDED Requirements

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

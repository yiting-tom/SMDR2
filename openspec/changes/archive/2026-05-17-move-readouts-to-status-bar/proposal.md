## Why

The viewer `<header>` currently mixes three unrelated kinds of UI on a
single row: navigation chrome, action buttons, and four live readouts
that update on every mouse move (`#status`, `#mode-hint`,
`#handle-info`, `#cursor-coords`). Those readouts are conceptually part
of "looking at the drawing", not part of the page chrome — and that's
where the engineer's AutoCAD muscle memory expects them (see
[[feedback_autocad_ux]]). Moving them out of the header to a thin bar
along the bottom of the canvas frees roughly 40% of the header width
for actions that actually belong there, and matches the layout the user
already reaches for instinctively.

## What Changes

- A new `<footer id="canvas-statusbar">` is added directly under the
  canvas (still inside `<main>`), containing the four readouts in the
  same left-to-right order they appeared in the header.
- The four `<span>` elements (`#status`, `#mode-hint`, `#handle-info`,
  `#cursor-coords`) and the `.spacer` between them are removed from
  `<header>` and reborn inside the new status bar. IDs stay identical
  so all existing JS write-sites (`setBaseStatus`, hover handlers, the
  cursor-coords updater, etc.) continue to work unmodified.
- The status bar is styled as a thin, dark, monospace strip that sits
  flush against the bottom of the canvas. It is semi-transparent so it
  doesn't visually amputate the bottom of the drawing.
- No JS behaviour changes — this is a DOM/CSS move only. Every
  selector that reaches for those four IDs continues to resolve.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `viewer-ui`: adds a requirement that the four live readouts live in
  a canvas-bottom status bar (not the page header). This pins the
  AutoCAD-style layout into the spec so a future header refactor can't
  regress it back into the chrome.

## Impact

- `app/templates/viewer.html` — remove the four readout spans + spacer
  from `<header>`; add `<footer id="canvas-statusbar">` inside `<main>`
  after `#dxf-canvas`.
- `app/static/style.css` — add a `#canvas-statusbar` rule block (dark
  semi-transparent background, monospace font, absolute or flex layout
  pinning it to the bottom of `<main>`). The corresponding rules in
  `header { … }` that styled the readouts as part of the header
  (`.hint`, `#handle-info`, `#cursor-coords`, `.spacer`) may move,
  stay, or simplify — to be decided in design.
- No JS, no API, no DB, no tests. The four IDs and their write-sites
  in `app/static/canvas.js` are untouched.

## Why

Packaging engineers reviewing a DXF in the viewer often need to sanity-check
geometry — pad pitch, ball spacing, clearance to silkscreen — before deciding
whether a template or rule is correct. Today the only way is to eyeball
coordinates or jump to AutoCAD. An in-viewer measure tool that behaves like
AutoCAD's `DIST` command removes that round-trip and matches the muscle memory
the user already has from [[feedback_autocad_ux]].

## What Changes

- Add a **measure-distance tool** to the viewer, activated via a toolbar button
  and the `D` hotkey (AutoCAD `DIST`).
- Pick two world points with **OSNAP-style snapping**: endpoint, midpoint, and
  nearest-on-edge against the same primitives the pickbox already considers.
- Show a **rubber-band** dimension line between the first picked point and the
  cursor, with a live readout of total distance, Δx, Δy.
- Clicking the second point **freezes** the measurement on the canvas (line +
  readout stay visible) until the user starts a new measure or cancels.
- `Esc` cancels the active measurement; it joins the existing Esc cascade
  *before* the "exit add-mode" step so an active measure is killed first.
- The tool is **read-only**: it does not change selection, library state, or
  add-mode; entering measure mode temporarily suppresses the pickbox/box-select
  handlers and restores them on exit.

## Capabilities

### New Capabilities
(none — this extends the existing viewer)

### Modified Capabilities
- `viewer-ui`: add an "AutoCAD-style measure-distance tool" requirement
  covering activation, OSNAP, rubber-band rendering, finalize/cancel, and
  interaction with selection / Esc cascade.

## Impact

- **Frontend (`app/static/canvas.js`, `app/templates/viewer.html`,
  `app/static/style.css`)**: new tool-mode state machine, snap resolver,
  rubber-band overlay renderer, toolbar button, hotkey binding, status hint.
- **No backend changes**: measurement is computed purely from in-memory
  primitives already shipped to the client.
- **No persistence**: measurements are session-local and not written to the
  library or any data file.
- **Existing interactions** ([[project_smdr2_workflow]] frame-select, add-mode,
  scan-all): unaffected functionally, but measure mode gates them while active.

## Why

A pass over the GUI (dashboard + canvas viewer) surfaced a cluster of
friction points for the primary user — an expert packaging engineer who
lives in AutoCAD and works on dense DXFs (10k+ entities, BGA/via grids).
The highest-leverage gaps are in the viewer's AutoCAD fidelity and in
everyday feedback; a few dashboard flows still fall back to the browser's
native `prompt()` / `confirm()` / `alert()`, which break the app's visual
language and keyboard flow.

None of these are correctness bugs — they are daily-use ergonomics. This
change groups the ones that are concrete, low-risk, and verifiable by
inspection into a single GUI-quality pass.

Several items from the original review were dropped after reading the
code, because they are **already implemented**: the `More ▾` toggle
already shows a hidden-match count (`canvas.js` ~ `More ▾ ×${hiddenHits}`),
and the Window/Crossing box already renders blue-solid vs green-dashed
(`STYLE_WINDOW` / `STYLE_CROSSING`). The live Window↔Crossing flip during
a box drag is **correct AutoCAD behaviour** (mode follows cursor direction
relative to the first point) and is deliberately left as-is — this change
only adds a transient textual mode label.

## What Changes

**Viewer (AutoCAD fidelity & feedback) — `app/static/canvas.js`, `viewer.html`, `style.css`:**

- **Zoom-to-extents.** Add a first-class "fit the whole drawing" action —
  the single most-missed AutoCAD gesture once the user has panned away.
  Bind it to `Home`, to a middle-mouse **double-click** (AutoCAD's Zoom
  Extents gesture), and to a toolbar "Fit" button. Today the only way back
  to the full view is a page reload.
- **Pan feedback + drag-state recovery.** Give middle-drag pan a
  `grabbing` cursor (the `.panning` class is set but unstyled), and clear
  any in-flight drag (pan or box) on `window` `blur`, so releasing the
  mouse off-window or alt-tabbing mid-drag never leaves the canvas stuck.
- **Sub-pixel circle visibility.** Circles below the dot threshold collapse
  to a 1×1 device-pixel dot; on HiDPI displays a whole BGA ball field can
  read as nearly nothing at low zoom. Scale the collapsed dot to a DPR-aware
  minimum so the field stays perceptible.
- **In-app shortcut reference.** The hotkey list lives only in a source
  comment. Add a `?`-key help overlay listing the shortcuts, and a transient
  `WINDOW` / `CROSSING` mode label in the status bar while box-dragging.
- **Readout numeric stability.** Apply `font-variant-numeric: tabular-nums`
  to the status-bar readouts so coordinates/handles don't jitter horizontally
  as digits change.

**Dashboard (in-app dialogs) — `app/static/dashboard.js`, `dashboard.html`, `style.css`:**

- Replace the native `prompt()` (new-version label), `confirm()` (remove a
  file from a role), and the signed-off `alert()` (409 conflict) with the
  app's own modal / inline patterns, so these flows match the rest of the UI,
  are keyboard-navigable, and are dismissable with Esc.

**Visual quality — `app/static/style.css`:**

- **Contrast floor.** Lift the lowest secondary-text colours (`--text-3` and
  a few helper colours sitting around 3:1 on the dark surfaces) to ≈4.5:1.
  This is a real legibility win on dark + small text, not just a compliance
  checkbox.
- **Narrow-viewport robustness.** The viewer's fixed-width rule sidebar /
  panel and the non-wrapping class toolbar overflow on split-screen / laptop
  widths. Add breakpoints so they shrink / wrap instead of overflowing.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `viewer-ui`: adds requirements for zoom-to-extents, pan cursor + drag
  recovery, sub-pixel dot visibility, an in-app shortcut reference + box-mode
  label, tabular status readouts, dashboard in-app dialogs, a secondary-text
  contrast floor, and narrow-viewport behaviour for viewer panels and the
  class toolbar. No existing requirement's behaviour is changed; the
  "AutoCAD-style interactions" requirement is extended, not altered.

## Impact

- **Code**: `app/static/canvas.js` (zoom-extents, pan cursor/recovery, dot
  floor, help overlay, box-mode label), `app/static/dashboard.js` (in-app
  dialog helpers), `app/templates/viewer.html` + `dashboard.html` (Fit
  button, help overlay markup, dialog markup), `app/static/style.css`
  (cursor, tabular-nums, contrast tokens, responsive breakpoints, dialog
  styling). Frontend only.
- **APIs / backend / DB / migrations**: none. No server route, payload,
  schema, or Match-JSON contract changes.
- **Tests**: UI-only; no Python test changes. Verification is manual in the
  browser (tasks §"Manual verification"). The existing `ruff` + `pytest`
  suite is run only to confirm nothing server-side regressed.
- **Out of scope (deliberately deferred):**
  - A full CSS design-token sweep across all 2756 lines. This change adds
    the spacing/font-size/radius scale to `:root` and uses it for the new/
    touched rules, but does not retro-tokenise every existing literal — that
    is a mechanical follow-up best done incrementally to keep the diff
    reviewable.
  - The dev-mode `editClassStrategy` `prompt()`s in `canvas.js` — expert /
    dev-only, far lower traffic than the dashboard flows; left for a later
    pass.
  - Broad screen-reader / ARIA / 44px-touch-target work. This is a
    desktop-first internal tool for expert engineers; that effort is low
    value here and is explicitly not undertaken.
- **Already-done items confirmed during scoping** (no work): `More ▾` hidden
  count; Window/Crossing box colour styling; fixed-3dp coordinate formatting.

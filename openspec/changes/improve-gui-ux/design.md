# Design notes

Frontend-only change. The decisions worth recording:

## Zoom-to-extents

`fitToBbox(bbox)` already exists and is called once on load with `data.bbox`.
The drawing's bbox is currently consumed inline and not retained. Capture it
into a module-level `loadedBbox` at load time and add `zoomExtents()` =
`fitToBbox(loadedBbox); render()`.

Triggers, in AutoCAD-mnemonic order of fidelity:
- **Middle-mouse double-click** — AutoCAD's canonical Zoom Extents. Bind via
  `dblclick` on the canvas, guarded to `e.button === 1` is not reliable on
  `dblclick`; instead detect middle via the `dblclick` event only firing for
  the primary button, so we additionally bind a manual double-press detector
  on middle `mousedown` (two middle-downs within 400 ms). Keep it simple: a
  `lastMiddleDownAt` timestamp compared in the existing middle-button branch.
- **`Home` key** — added to the main keydown handler, guarded the same way as
  the other viewer hotkeys (ignored while typing in an input / while a modal
  is open).
- **Toolbar "Fit" button** — discoverable, mouse-only path.

`MIN_FRAME_SPAN` / `recenterOnFocusedSubRule` are untouched; extents is a
separate path from rule-focus framing.

## Pan cursor + drag recovery

`.panning` is already toggled on the canvas around a middle-drag; it just has
no CSS. Add `#dxf-canvas.panning { cursor: grabbing; }`.

Stuck-drag recovery: the existing `mouseup` is on `window`, so an in-window
release already clears state. The gap is a release **outside** the browser or
an alt-tab mid-drag. Add a `window` `blur` handler that, if a drag is active,
clears `drag`, `markDrag`, and the `.panning` class and re-renders — a cheap
catch-all that cannot make state worse.

## Sub-pixel dot floor

`flushDotBuckets` paints each collapsed circle as a 1×1 device-pixel rect.
Change the rect size to `max(1, round(dpr))` device px (2 px on a 2× display)
so the dot keeps a perceptible footprint on HiDPI without changing world-space
semantics or the batching strategy. The threshold (`DOT_THRESHOLD_CSS_PX`)
that decides *when* a circle collapses is unchanged — only the painted size of
an already-collapsed dot.

## In-app shortcut reference + box-mode label

A static overlay (`#shortcut-help`, hidden by default) toggled by the `?` key
and a `?` toolbar affordance; content is authored from the same list that
lives in the canvas.js header comment. It is a plain absolutely-positioned
panel, dismissed by `?`, `Esc`, or a backdrop click — no focus-trap machinery
(desktop, keyboard-optional).

Box-mode label: while `drag.kind === "box"`, show `WINDOW` / `CROSSING` in the
status bar's mode-hint, cleared on drag end. Reuses the existing `#mode-hint`
slot; no new element.

## Dashboard in-app dialogs

Add two small promise-returning helpers in `dashboard.js`:
`uiPrompt({title, label, value, confirmText})` → `Promise<string|null>` and
`uiConfirm({title, body, confirmText, danger})` → `Promise<bool>`, both built
on the existing `.modal` / `.modal-panel` / `.modal-bg` markup the page already
uses, with `Enter` = confirm, `Esc` / backdrop = cancel, and focus moved to the
field/confirm button on open. Replace:
- `createNewVersion` `prompt()` → `await uiPrompt(...)`
- remove-file `confirm()` → `await uiConfirm(... danger: true)`
- signed-off 409 `alert()` → an inline, auto-dismissing banner near the
  product card (non-blocking), reusing the same styling as other inline
  notices.

These helpers are deliberately minimal and local to the dashboard; they are
not a general toast/dialog framework.

## Contrast + responsive

Contrast: bump `--text-3` and the handful of `#5d8aa8`-class helper colours to
the next step that clears ~4.5:1 on `--surface` / `--bg-page`. Done at the
token level so every consumer lifts at once; values chosen to stay within the
existing blue-grey hue so the look is unchanged, only lighter.

Responsive: one extra `@media (max-width: 900px)` block — narrow the rule
sidebar/panel and let `#class-toolbar` wrap (`flex-wrap: wrap; overflow-x:
visible`) instead of horizontal-scrolling. No JS; purely additive CSS.

New `:root` scale tokens (`--sp-*`, `--fs-*`, `--radius-*`) are introduced and
used by the rules this change adds/touches; pre-existing literals are left for
an incremental follow-up (see proposal Impact).

## 1. Viewer — zoom-to-extents (`canvas.js`, `viewer.html`, `style.css`)

- [x] 1.1 In `canvas.js`, add a module-level `let loadedBbox = null;` and set it where the drawing loads (`fitToBbox(data.bbox)` site) before the fit call.
- [x] 1.2 Add `function zoomExtents()` that calls `fitToBbox(loadedBbox)` then `render()`, guarded to no-op when `loadedBbox` is null.
- [x] 1.3 Bind middle-mouse double-click → `zoomExtents()`: in the middle-button `mousedown` branch, compare a `lastMiddleDownAt` timestamp; two middle-downs within 400 ms call `zoomExtents()` (and skip starting a pan for the second).
- [x] 1.4 Add `Home` to the main keydown handler → `zoomExtents()`, guarded like the other viewer hotkeys (ignore while typing in an input / a modal open).
- [x] 1.5 Add a "Fit" toolbar button in `viewer.html` (title includes the `Home` hint) and wire its click to `zoomExtents()`.

## 2. Viewer — pan cursor + drag recovery (`canvas.js`, `style.css`)

- [x] 2.1 In `style.css`, add `#dxf-canvas.panning { cursor: grabbing; }`.
- [x] 2.2 In `canvas.js`, add a `window` `blur` handler: if any drag is active (`drag` or `markDrag`), clear `drag`, `markDrag`, remove `.panning` from the canvas, and `render()`.

## 3. Viewer — sub-pixel dot floor (`canvas.js`)

- [x] 3.1 In `flushDotBuckets`, paint each collapsed dot at `d = Math.max(1, Math.round(dpr))` device px instead of 1×1 (square `d × d`, offset so it stays centred on the point). Leave `DOT_THRESHOLD_CSS_PX` and the batching unchanged.

## 4. Viewer — shortcut reference + box-mode label (`canvas.js`, `viewer.html`, `style.css`)

- [x] 4.1 Add a hidden `#shortcut-help` overlay to `viewer.html` listing the shortcuts from the `canvas.js` header comment (middle-drag pan, wheel zoom, single/shift pick, L→R window / R→L crossing, Esc cascade, A scan-all, D measure, V views, Home fit, `?` help).
- [x] 4.2 In `canvas.js`, toggle `#shortcut-help` on `?` keydown (no input focused); hide on `?` again, `Esc`, or backdrop click. Add a small `?` affordance to the toolbar that opens it.
- [x] 4.3 In the box-drag mousemove/render path, set `#mode-hint` to `WINDOW` / `CROSSING` per `drag.mode` while `drag.kind === "box"`, and clear it on drag end (mouseup / Esc / blur).
- [x] 4.4 In `style.css`, style `#shortcut-help` (overlay panel + backdrop) consistent with existing floating panels.

## 5. Viewer — tabular readouts (`style.css`)

- [x] 5.1 Add `font-variant-numeric: tabular-nums;` to `#canvas-statusbar` (covering `#status`, `#mode-hint`, `#handle-info`, `#cursor-coords`).

## 6. Dashboard — in-app dialogs (`dashboard.js`, `dashboard.html`, `style.css`)

- [x] 6.1 Add `uiPrompt({title, label, value, confirmText})` → `Promise<string|null>` and `uiConfirm({title, body, confirmText, danger})` → `Promise<boolean>` in `dashboard.js`, built on the existing `.modal` markup; `Enter` confirms, `Esc` / backdrop cancels, focus moves to the field / confirm button on open.
- [x] 6.2 Add the dialog container markup to `dashboard.html` (a reusable `#ui-dialog` modal the helpers populate), if a reusable host isn't already present.
- [x] 6.3 Replace the `createNewVersion` `prompt()` with `await uiPrompt(...)` (cancel → return without creating).
- [x] 6.4 Replace the remove-file `confirm()` with `await uiConfirm({..., danger: true})`.
- [x] 6.5 Replace the signed-off 409 `alert()` with an inline, auto-dismissing notice rendered near the affected product card (who signed off + when).
- [x] 6.6 In `style.css`, add styling for the destructive confirm variant and the inline 409 notice (reuse existing tokens).

## 7. Visual — contrast floor + responsive (`style.css`)

- [x] 7.1 Raise `--text-3` and the helper colours near 3:1 (e.g. the `#5d8aa8` family used by rule counts, rescaled / unit badges, link buttons) to the next blue-grey step clearing ≈4.5:1 on `--surface` / `--bg-page`.
- [x] 7.2 Introduce `:root` scale tokens (`--sp-*`, `--fs-*`, `--radius-*`) and use them in the rules this change adds/touches (not a full retro-sweep — see proposal Impact).
- [x] 7.3 Add `@media (max-width: 900px)`: reduce `#rule-sidebar` / `#rule-panel` width and set `#class-toolbar { flex-wrap: wrap; overflow-x: visible; }`.

## 8. Regression gate

- [x] 8.1 Run `uv run ruff check app tests` and `uv run pytest -q`; confirm green (frontend-only change must not have touched anything the suite covers).
- [x] 8.2 `git grep -n "prompt(\|confirm(\|alert("` `app/static/dashboard.js` returns no hits for the three replaced call sites.

## 9. Verification

Smoke-tested end-to-end via Playwright against the full `docker compose` OIDC
stack (logged in as `admin1`, product `PW全流程-料號A`, `test.dxf` = 24,814
primitives / 24,548 collapsed dots). The one `[USER]` item left wants a human
eye on live cursor feel.

- [x] 9.1 Dev stack up; viewer opened on `test.dxf`. (Also: dev MariaDB was at
  Alembic `0006`; ran `upgrade head` → `0007` so `/api/products` stopped 500ing
  — pre-existing env drift, not part of this change.)
- [x] 9.2 `Home`, middle double-click, and the "Fit" button each re-framed the
  drawing (render-stats `culled` 24,810 → 0 on each path).
- [x] 9.4 24,548 sub-pixel circles render as a visible dot field (screenshot).
- [x] 9.5 `?` opens the overlay (15 rows) and `?`/`Esc` close it; box-drag L→R
  set `WINDOW`, R→L set `CROSSING`, cleared on release.
- [x] 9.6 Status bar carries `font-variant-numeric: tabular-nums`; coords render.
- [x] 9.7 New-version dialog = in-app `uiPrompt` (focused field, Esc cancels);
  signed-off 409 renders the top-right `uiToast`. Remove-file / delete use the
  same verified `uiConfirm` path.
- [x] 9.8 At 760 px the `max-width:900px` query matches; class toolbar computes
  `flex-wrap: wrap` / `overflow-x: visible`.
- [ ] 9.3 **[USER]** Confirm the `grabbing` pan-cursor *feel* and the alt-tab /
  off-window blur recovery interactively (the `.panning` CSS rule + the `blur`
  handler are verified to exist; a live cursor can't be asserted via automation).

## 10. Archive

- [ ] 10.1 After tasks 1–9 pass, run `/opsx:archive improve-gui-ux` to fold the modified `viewer-ui` spec into the live spec and mark the change archived.

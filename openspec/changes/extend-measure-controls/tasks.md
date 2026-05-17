## 1. State model

- [ ] 1.1 Update the `measureState` initializer near canvas.js:220 from
      `{ picks, snapHint, lastCursor }` to
      `{ chains: [], picks: [], snapHint: null, lastCursor: null, cancelHitboxes: [] }`.
- [ ] 1.2 Update `exitMeasureMode()` (canvas.js:2273) so its reset assignment
      uses the new shape; verify no other site mutates `measureState`
      structurally (`grep -n "measureState =" app/static/canvas.js`).
- [ ] 1.3 Confirm `measureAnchor()` still references only `measureState.picks`
      and needs no change.

## 2. Right-click → pop active pick

- [ ] 2.1 Add a `contextmenu` listener on `$canvas`. When `measureMode` is
      true, `preventDefault()` unconditionally so the browser menu never
      appears in measure mode.
- [ ] 2.2 If `measureState.picks.length > 0`, pop one entry. Then if
      `measureState.lastCursor` exists, re-resolve `snapHint` via
      `applyOrtho(wx, wy, false) ?? resolveSnap(wx, wy)` so the rubber-band
      visual updates immediately without a mousemove.
- [ ] 2.3 Call `updateStatus()` and `render()` after a pop.
- [ ] 2.4 Verify the `contextmenu` listener does not fire on the canvas's
      surrounding chrome (header buttons, library modal).

## 3. Enter → commit active chain

- [ ] 3.1 In the `keydown` handler (canvas.js:2178+), add a new branch
      *before* the existing add-mode Enter branch:
      ```
      if (e.key === "Enter" && measureMode) { ... }
      ```
- [ ] 3.2 If `measureState.picks.length >= 2`, push `measureState.picks`
      onto `measureState.chains` (no copy needed — we reset `picks` to a
      new array immediately after).
- [ ] 3.3 Always reset `measureState.picks = []`, `measureState.snapHint = null`,
      hide `$measureReadout`, call `updateStatus()` and `render()`, and
      `e.preventDefault()` so Enter does not bubble.
- [ ] 3.4 Confirm the existing `addMode` Enter handler (commit template) is
      unreachable while `measureMode` is true (the mutually-exclusive guard
      at toggle time means `addModeClass` is `null` whenever
      `measureMode` is true, but a defensive check is fine).

## 4. Esc → clear all measurements

- [ ] 4.1 Widen the existing Esc condition at canvas.js:2180 from
      `if (measureMode && measureState.picks.length)` to
      `if (measureMode && (measureState.picks.length || measureState.chains.length))`.
- [ ] 4.2 Inside that branch, clear `chains = []`, `picks = []`,
      `snapHint = null`; hide `$measureReadout`; `updateStatus()`; `render()`;
      `return`.

## 5. Render: committed chains + ✕ hitboxes

- [ ] 5.1 At the top of `drawMeasureOverlay()` (canvas.js:920+), clear
      `measureState.cancelHitboxes = []` so the array is rebuilt each frame.
- [ ] 5.2 Iterate `measureState.chains` and for each chain `c`:
      - draw segments `c[i-1]..c[i]` for `i` in `1..c.length-1` via
        `drawMeasureSegment(a, b, /*dashed*/ false)`.
      - draw an endpoint dot at each `c[i]` via `drawPickDot(c[i])`.
      - draw each segment's midpoint label via
        `drawSegmentLabel(c[i-1], c[i], fmtCoord(distance))`.
- [ ] 5.3 Add a new helper `drawCancelButton(a, b, chainIndex)` that:
      - computes the same perpendicular-offset midpoint that
        `drawSegmentLabel` uses for chain `c`'s first segment;
      - measures the existing label's text width (yes, re-measure — keeps
        the helper independent of label rendering);
      - renders a 14×14 CSS-px ✕ box immediately to the right of the label
        box (≈4 CSS px gap), with yellow-on-black-yellow-border styling
        identical to the segment label;
      - draws the ✕ glyph as two diagonal strokes;
      - pushes `{ cssLeft, cssTop, cssRight, cssBottom, chainIndex }` onto
        `measureState.cancelHitboxes`. Use CSS pixels (divide by `dpr`).
- [ ] 5.4 Call `drawCancelButton(c[0], c[1], chainIndex)` once per chain
      while iterating in 5.2.
- [ ] 5.5 Confirm the existing active-chain rendering (frozen + live + live
      label with Σ) is left intact and runs *after* the committed-chains
      pass so the active rubber-band layers on top.

## 6. Click routing: cancel hitbox wins over pickbox

- [ ] 6.1 In `mousedown` at canvas.js:1551 (left button), once `measureMode`
      is true, hit-test `measureState.cancelHitboxes` *before* the existing
      "append to picks" logic.
- [ ] 6.2 Convert `e.clientX/clientY` to canvas-local CSS pixels using
      `$canvas.getBoundingClientRect()`. Match against `cssLeft / cssTop /
      cssRight / cssBottom`.
- [ ] 6.3 On a hit: `measureState.chains.splice(chainIndex, 1)`, call
      `updateStatus()` and `render()`, and `return` (do not append a pick).
- [ ] 6.4 On a miss: fall through to the existing pick-append branch
      unchanged.

## 7. Status hint

- [ ] 7.1 In `updateStatus()` (canvas.js:1520), extend the `measureMode`
      branch to use the four-quadrant table from design D6:
      - `picks=0, chains=0` → `MEASURE · pick first point`
      - `picks=0, chains≥1` → `MEASURE · pick first point · N chain[s] saved (Esc to clear all)`
      - `picks≥1, chains=0` → existing string with `Esc to clear`
      - `picks≥1, chains≥1` → existing string but swap `Esc to clear` →
        `Esc to clear all`
- [ ] 7.2 Pluralize `chain[s]` and `pt[s]` correctly.

## 8. Verification (browser, manual)

- [ ] 8.1 Enter measure mode, click 3 picks, then right-click — confirm the
      last pick disappears and the rubber-band returns to the second pick.
- [ ] 8.2 Right-click twice more from the same 3-pick chain — confirm the
      chain empties cleanly and the status hint returns to
      `MEASURE · pick first point`.
- [ ] 8.3 Click 2 picks, press Enter — confirm the segment turns into a
      *committed* chain (still drawn) and a new active rubber-band starts
      from the next mouse click.
- [ ] 8.4 Build a 3-segment chain, press Enter; build another 2-segment
      chain, press Enter. Confirm both chains stay on screen with their
      own labels and a ✕ each.
- [ ] 8.5 Click the ✕ on one committed chain — confirm only that chain
      disappears, the other chain stays, and any active rubber-band is
      untouched.
- [ ] 8.6 With two committed chains plus a 2-pick active chain in flight,
      press Esc — confirm every measurement is removed in one keystroke
      and measure mode stays active.
- [ ] 8.7 Press Enter with `picks.length === 0` — confirm it's a no-op
      (no chain pushed, no error).
- [ ] 8.8 Press Enter with `picks.length === 1` — confirm `picks` resets
      to empty and no committed chain is pushed.
- [ ] 8.9 Right-click outside measure mode — confirm the browser context
      menu appears normally (regression check).
- [ ] 8.10 Right-click inside measure mode with `picks.length === 0` and
      no committed chains — confirm the browser context menu is
      suppressed and nothing else changes.
- [ ] 8.11 Toggle measure mode off via `D` while two committed chains
      exist — confirm exiting clears the chains (same as today's behavior
      for the single chain).
- [ ] 8.12 With a committed chain whose ✕ sits near a regular DXF entity,
      verify the ✕ click removes only the chain and does NOT alter
      selection or trigger any pickbox action.
- [ ] 8.13 Confirm the status hint flips correctly across the four
      `(chains, picks)` states from D6.

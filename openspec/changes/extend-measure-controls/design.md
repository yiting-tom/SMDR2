## Context

The existing tool stores measurement state as a single linear array:

```js
let measureState = { picks: [], snapHint: null, lastCursor: null };
```

`picks[i]..picks[i+1]` are the frozen segments of the one active chain;
`picks[last]` is the rubber-band anchor. `Esc` clears `picks`. There is
no concept of multiple chains, no undo, and no in-tool commit step —
the chain just keeps extending until `Esc` or the tool is toggled off.

The frame rendering (`drawMeasureOverlay`, `drawSegmentLabel`,
`drawPickDot`) is purely canvas-2D and is called from the end of
`render()`. The HTML readout near the cursor (`#measure-readout`) only
shows live `Δx` / `Δy` for the currently-tracked rubber-band.

This change introduces (a) multiple chains on the canvas at once and
(b) two new event-routing rules (right-click and the ✕ cancel hitbox)
that have to fit alongside the existing left-click / pickbox routing.

## Goals / Non-Goals

**Goals**

- Keep the existing single-chain behavior unchanged for users who never
  press Enter — the tool should "feel like before" until they explicitly
  opt into multi-chain by pressing Enter.
- Right-click recovery from a mis-pick should be one keystroke and must
  not affect any committed chain.
- Per-chain cancel must be a click target the user can find without
  reading documentation — i.e. drawn unambiguously close to the chain's
  geometry, with a hit area that is generous on small screens.
- Esc must remain the "panic" key — one press clears the whole tool
  state (then the cascade kicks in on the next press if held).

**Non-Goals**

- Reordering or renaming committed chains.
- Showing a chain index / numeric label per chain.
- Right-click "redo" — once a pick is undone it's gone.
- Esc-while-active-chain-only-clears-active behavior. The user
  explicitly asked for "Esc clears everything" (background point 3 of
  the request).
- Treating committed chains as part of the selection. They are an
  overlay; click-through for selection still hits the underlying
  entities (the ✕ hitbox is the only canvas-overlay click target).

## Decisions

### D1. Promote `picks` → `{ chains, picks }`; keep helpers backwards-compatible

```js
let measureState = {
  chains: [],           // committed chains: [[ [x,y], [x,y], ... ], ...]
  picks: [],            // active chain (unchanged shape)
  snapHint: null,       // unchanged
  lastCursor: null,     // unchanged
  cancelHitboxes: [],   // rebuilt each render: [{rect, chainIndex}, ...]
};
function measureAnchor() {
  return measureState.picks.length
    ? measureState.picks[measureState.picks.length - 1]
    : null;
}
```

- `measureAnchor()` continues to read from `picks` only. The active
  chain is always the rubber-band's owner; committed chains are
  rendered passively.
- `cancelHitboxes` is **derived state**, recomputed by
  `drawMeasureOverlay()` each frame in screen-space CSS pixels. We
  rebuild rather than cache+invalidate because the hitbox positions
  follow the pan/zoom transform, which changes constantly.

**Alternative considered**: a single flat array with chain-boundary
sentinel values. Rejected — boundary handling in every loop is
brittle; a 2-D `chains` is clearer and trivially serializable if we
ever do want to persist.

### D2. Right-click via `contextmenu`, not `mousedown` button 2

Two event paths exist for right-click on macOS:

1. `mousedown` with `e.button === 2`.
2. `contextmenu` event.

Different browsers / platforms / macOS preferences (Ctrl-click as
right-click, two-finger tap) fire these in different orders. The most
reliable cross-platform handler is `contextmenu`:

```js
$canvas.addEventListener("contextmenu", (e) => {
  if (!measureMode) return;
  e.preventDefault();
  if (measureState.picks.length) {
    measureState.picks.pop();
    // re-resolve so the snap marker / Δx / Δy update before the next move
    if (measureState.lastCursor) {
      const [wx, wy] = measureState.lastCursor;
      measureState.snapHint =
        applyOrtho(wx, wy, false) ?? resolveSnap(wx, wy);
    }
    updateStatus();
    render();
  }
});
```

When measure mode is on, the context menu is **always** suppressed (even
with `picks.length === 0`) so the user can right-click anywhere on
the canvas without surprise. When measure mode is off, the context menu
behaves normally (browser default).

**Why pop is allowed when `picks.length === 1`**: dropping back to
`picks.length === 0` matches what the user expects ("退回 chain 的前
一個 step"). It cleanly resets the rubber-band, the status hint flips
back to `pick first point`, and the next left-click starts a brand new
active chain. No need for a special-case "exit measure mode on the
N+1-th right-click".

### D3. Enter commits if `picks.length >= 2`; clears otherwise

```js
// inside the existing `keydown` Enter branch
if (e.key === "Enter" && measureMode) {
  if (measureState.picks.length >= 2) {
    measureState.chains.push(measureState.picks);
  }
  measureState.picks = [];
  measureState.snapHint = null;
  if ($measureReadout) $measureReadout.hidden = true;
  updateStatus();
  render();
  e.preventDefault();
  return;
}
```

The Enter branch is placed **above** the existing add-mode Enter
handler so that, in the (already impossible by spec) case of both
modes being on, measure mode wins. Today `addMode` and `measureMode`
are mutually exclusive at toggle time, so this is defensive.

A chain with exactly one pick has no segment to display — committing
it would draw nothing. Reset-without-commit is the only sensible
behavior; we don't issue a status error because the user always knows
how many picks they've made (the hint shows the count).

### D4. Esc clears `chains` and `picks` together; cascade ordering unchanged

The current Esc branch is:

```js
if (drag && drag.kind === "box") { ... }
if (measureMode && measureState.picks.length) { picks=[]; ... return; }
// ... rest of cascade
```

The condition `measureState.picks.length` is widened to also fire when
any committed chain exists:

```js
if (
  measureMode &&
  (measureState.picks.length || measureState.chains.length)
) {
  measureState.chains = [];
  measureState.picks = [];
  measureState.snapHint = null;
  if ($measureReadout) $measureReadout.hidden = true;
  updateStatus();
  render();
  return;
}
```

The user gets one `Esc` to wipe all measurements (still in measure
mode); a second `Esc` falls through to the rest of the cascade
(scan-all / add-mode / selection). Toggling the tool off via `D` or
the button still calls `exitMeasureMode()`, which already does a full
reset — it just needs to clear `chains` too.

### D5. Per-chain ✕ rendered on canvas; hitbox tested before pickbox

**Render**:

`drawMeasureOverlay()` walks `chains` first. For each committed chain
`c` with `picks.length ≥ 2`:

1. Draw every segment `c[i]..c[i+1]` solid (reuse `drawMeasureSegment`
   with `dashed=false`).
2. Draw an endpoint dot at each `c[i]` (reuse `drawPickDot`).
3. Draw each segment's midpoint label (reuse `drawSegmentLabel`).
4. **New**: next to the first segment's label, draw a small ✕ button
   and push its screen-space rect onto `cancelHitboxes`.

The ✕ glyph is a 14 CSS-px square with the same yellow-on-black-with-
yellow-border styling as the existing labels. It sits immediately to
the right of the first segment's label box, separated by ~4 CSS px so
the click target is unambiguous. The drawn rectangle and the hitbox
rectangle are identical so what-you-see-is-what-you-click.

**Hitbox structure**:

```js
cancelHitboxes.push({
  cssLeft, cssTop, cssRight, cssBottom,   // CSS-pixel screen coords
  chainIndex: i,                          // index into measureState.chains
});
```

We store CSS pixels (not device) because `mousedown`'s
`e.clientX/clientY` are CSS pixels.

**Hit test**: in `mousedown` left-branch (button 0), *before* any
mark-mode / measure-mode / pickbox routing:

```js
if (measureMode) {
  const cssX = e.clientX - $canvas.getBoundingClientRect().left;
  const cssY = e.clientY - $canvas.getBoundingClientRect().top;
  for (const h of measureState.cancelHitboxes) {
    if (cssX >= h.cssLeft && cssX <= h.cssRight &&
        cssY >= h.cssTop  && cssY <= h.cssBottom) {
      measureState.chains.splice(h.chainIndex, 1);
      updateStatus();
      render();
      return;
    }
  }
  // fall through to existing measure pick handling
}
```

**Why a single ✕ per chain, not one per segment**: putting an ✕ on
every segment label clutters dense chains (8 segments → 8 ✕ buttons,
visually noisy). One ✕ per chain at the chain's start is enough — a
chain is logically one measurement; partial deletion would break the
chain's geometry anyway.

**Why next to the first segment's label, not the chain centroid**: the
first label is always rendered; chain centroids would need recomputing
and could land inside dense geometry. Anchoring to the first segment
keeps the position deterministic and out of the chain's visual flow.

### D6. Status hint reflects total chains + active picks

The current hint reads:

- `MEASURE · pick first point` when `picks.length === 0`.
- `MEASURE · pick next point · N pt[s] (Shift = ortho, Esc to clear)`
  when `picks.length ≥ 1`.

Extended:

- When `chains.length > 0` and `picks.length === 0`, append
  ` · ${chains.length} chain[s] saved` to the "pick first point"
  variant.
- When `picks.length ≥ 1`, the existing chain count refers to the
  *active* chain's pick count and is preserved; the saved-chain count
  is **not** appended in that state to keep the line short.

Concrete strings:

| chains | picks | hint |
|---|---|---|
| 0 | 0 | `MEASURE · pick first point` |
| 0 | N≥1 | `MEASURE · pick next point · N pt[s] (Shift = ortho, Esc to clear)` |
| M≥1 | 0 | `MEASURE · pick first point · M chain[s] saved (Esc to clear all)` |
| M≥1 | N≥1 | `MEASURE · pick next point · N pt[s] (Shift = ortho, Esc to clear all)` |

The "Esc to clear" suffix swaps to "Esc to clear all" once committed
chains exist, so the user knows Esc has a wider blast radius now.

### D7. `exitMeasureMode()` clears chains too

`exitMeasureMode` already does a full reset:

```js
measureState = { picks: [], snapHint: null, lastCursor: null };
```

It just needs to learn the new shape:

```js
measureState = { chains: [], picks: [], snapHint: null, lastCursor: null,
                 cancelHitboxes: [] };
```

Toggling measure mode off therefore drops every committed chain, same
as toggling did with the single chain today. This is consistent with
"the tool owns its state; exiting the tool drops the state".

## Risks / Trade-offs

- **Risk**: the ✕ hitbox overlaps a primitive the user wanted to click
  on. → **Mitigation**: the hitbox only exists while measure mode is
  active; in measure mode the left-click is never a selection action
  anyway. The ✕ is small (14×14 CSS px) and offset from the segment,
  so it doesn't shadow the geometry under it.
- **Risk**: Esc-clears-all surprises a user who expected the old
  "Esc clears just the active chain" behavior. → **Mitigation**: the
  status hint changes to "Esc to clear all" the moment a committed
  chain exists, signaling the change.
- **Risk**: dense canvas with many committed chains makes ✕ buttons
  overlap each other if chains start near the same point. → **Accepted
  for v1**: typical workflow puts measurements on distinct features.
  A future refinement could push the ✕ along the first segment's
  perpendicular until it doesn't collide with another label.
- **Trade-off**: no right-click "redo". Adding it doubles the state
  machine and is not in the user's stated requirements. Deferred.

## Open Questions

- Should a committed chain's ✕ have a hover state on the canvas
  (color flip)? Easy to add later; not in scope for v1.
- Should pressing Enter while `picks.length === 0` be a no-op or
  toggle measure mode off? Currently spec says no-op; revisit if the
  user finds it annoying.

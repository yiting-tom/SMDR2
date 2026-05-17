## Why

The measure-distance tool today supports a single, linear chain of picks. In
real use the engineer often:

1. Mis-clicks on the wrong OSNAP target mid-chain — the only recovery is
   `Esc`, which destroys the entire chain instead of just the bad pick.
2. Wants to record several independent distances on the same view (e.g.
   pad pitch in X, pad pitch in Y, ball-to-silkscreen clearance) without
   losing prior measurements when starting the next one.
3. Wants to remove one specific measurement without clearing everything.

The original `measure-distance-tool` change left these as open questions
("Should there be an 'undo last pick' gesture?" and the absence of a
finalize step). This change resolves them with a control set that matches
the user's AutoCAD muscle memory recorded in [[feedback_autocad_ux]].

## What Changes

- **Right-click** inside measure mode pops the last pick from the active
  chain (AutoCAD `U` inside a command). The chain stays open; the
  rubber-band redraws from the new anchor. If the active chain only had
  one pick, right-click empties it and the status hint returns to
  `MEASURE · pick first point`. The default browser context menu is
  suppressed while measure mode is active.
- **Enter** inside measure mode freezes the active chain into a
  *committed chain* — it stays drawn on the canvas (solid segments,
  endpoint dots, per-segment labels) but is no longer the rubber-band's
  anchor. The next left-click starts a fresh active chain. Pressing
  Enter with `< 2` picks is a no-op clear (nothing to commit).
- **Esc** inside measure mode clears *all* measurements — both the
  active chain and every committed chain — in one keystroke. Measure
  mode itself stays on.
- **Each committed chain shows a "✕" cancel affordance** drawn next to
  its first segment's midpoint label. Left-clicking the ✕ removes that
  one chain (its picks, segments, labels, and the ✕ itself) without
  touching any other chain or the active chain.

The original two-key escape ladder (`Esc` clears chain → `Esc` again
falls through to scan-all / add-mode / selection per the existing
cascade) is preserved; it just now clears more when the user has
accumulated multiple chains.

## Capabilities

### Modified Capabilities

- `viewer-ui` — extends the AutoCAD-style measure-distance tool added by
  the `measure-distance-tool` change with multi-chain state, in-chain
  undo, per-chain commit/cancel, and a scoped Esc behavior.

## Impact

- **Frontend (`app/static/canvas.js`)**:
  - `measureState` grows from `{ picks, snapHint, lastCursor }` to
    `{ chains, picks, snapHint, lastCursor, cancelHitboxes }`.
  - `drawMeasureOverlay()` iterates `chains` first, then the active
    `picks`; per-chain ✕ hitboxes are built each frame in screen space.
  - `mousedown` left-branch checks `cancelHitboxes` before any other
    routing so the ✕ click wins over selection / pickbox.
  - New `contextmenu` listener on the canvas — `preventDefault` while
    in measure mode, pops `picks` if non-empty.
  - The Enter branch in the existing `keydown` handler commits the
    active chain into `chains` when in measure mode (separate from the
    existing add-mode commit path).
  - The Esc branch clears both `chains` and `picks`.
- **CSS / HTML**: no new persistent UI — the cancel "✕" is drawn on the
  canvas overlay; no extra DOM nodes.
- **Backend**: none.
- **Persistence**: none — committed chains live for the session only,
  exactly like the single chain does today. Reload starts cold.

## Out of scope

- Persisting committed chains across page reloads or to the library.
- Editing a committed chain in place (drag a pick to move it).
- A separate label or sidebar listing the committed chains numerically.
- Touch-device gesture parity for right-click / context-menu.

## Context

`<header>` in `app/templates/viewer.html` ends with four `<span>`
elements that update on every mouse move or pipeline event:

```html
<span id="status">loading…</span>
<span class="spacer"></span>
<span id="mode-hint" class="hint"></span>
<span id="handle-info">Handle —</span>
<span id="cursor-coords">—</span>
```

Their write-sites live in `app/static/canvas.js` (`setBaseStatus`, the
mousemove handler that fills `#handle-info` + `#cursor-coords`, and the
add-mode/measure-mode prompts that drive `#mode-hint`). Their styling
lives in `app/static/style.css` lines 90–122 as `header #…` rules.

The `<main>` element below the header is already
`position: relative; overflow: hidden`, so a child element pinned to
its bottom via `position: absolute; bottom: 0` will stick to the
canvas's bottom edge regardless of viewport size — no JS layout work
needed.

This change is the first concrete cut at the header-decluttering
discussion. The `← Products` / SMDR2 dedup was the warm-up
([[remove-viewer-title-link]]); this change is the higher-leverage
move that actually creates header breathing room.

## Goals / Non-Goals

**Goals:**
- Move all four live readouts and the `.spacer` between them out of
  `<header>` into a new `<footer id="canvas-statusbar">` inside
  `<main>`, pinned to the bottom of the canvas.
- Keep every readout's element ID, JS contract, and per-readout color
  cue (status grey, handle cyan, mode-hint orange) intact so canvas.js
  needs zero changes.
- Style the bar to feel like a status bar, not a second toolbar:
  thin (~22 px), monospace-aligned, semi-transparent dark background
  so geometry near the bottom of the drawing stays visible.

**Non-Goals:**
- Restructuring the rest of the header (action buttons / mode toggles
  grouping is a separate proposal).
- Adding new readouts (e.g. snap mode, layer count). The bar takes the
  current four and stops there.
- Changing the values, formatting, or update cadence of any readout.
- Making the bar collapsible or movable. AutoCAD's is always present;
  the engineer's eye expects the same.

## Decisions

**Place `<footer id="canvas-statusbar">` inside `<main>`, not after
it.** `<main>` already owns the canvas geometry and is the natural
parent for any "overlay" affordance. Putting the bar inside lets it
absolute-position against `<main>`'s bottom edge and inherit the same
left/right bounds as the canvas — no risk of it sliding under the
sidebars that `<main>` already contains
(`#visibility-panel`, `#rule-sidebar`).

**Absolute positioning with `pointer-events: none` on the bar and
`pointer-events: auto` on its text children.** The bar floats over
the bottom of the canvas. Clicks that miss the text should fall
through to the canvas (so the user can still pick geometry under the
bar without being blocked by a transparent strip). The text spans get
`pointer-events: auto` so tooltips on `#handle-info` still work.

**Semi-transparent background `rgba(15, 19, 24, 0.72)` with
`backdrop-filter: blur(2px)`.** Matches the header's `#0f1318` family
but lets a hint of geometry show through, so the bar feels overlaid
rather than carved out of the canvas. Falls back gracefully on
browsers without backdrop-filter (still readable on the dark canvas
background).

**Migrate the four `header #…` CSS rules to `#canvas-statusbar #…`
rules, drop `header .spacer`.** The status bar uses its own
`display: flex` layout — `.spacer` keeps doing what it always did
(`flex: 1` between status and mode-hint), but it's scoped to the new
container. The old `header .spacer` rule and the old `header #…`
readout rules are deleted (they have no other consumers — verified by
grep).

**Order is preserved: status (left) · spacer · mode-hint · handle ·
coords (right).** This matches the header order users already see;
re-ordering would force re-learning.

## Risks / Trade-offs

- **[Risk]** Status bar overlaps geometry the user wants to click near
  the bottom of the drawing → **Mitigation:** semi-transparent
  background + `pointer-events: none` on the container, so clicks
  fall through to the canvas. The bar is thin (~22 px) so the
  occlusion is small. If this still bothers in practice we can shrink
  it further or auto-hide on hover.
- **[Risk]** `position: absolute` inside `<main>` competes with the
  existing absolute-positioned `<aside>` panels (`#visibility-panel`,
  `#rule-sidebar`) and `#measure-readout` → **Mitigation:** give the
  bar a low z-index (1) so panels render above it; verify each panel
  visually after the change.
- **[Trade-off]** Bottom-of-canvas placement is an extra screen
  region the user has to learn → accepted because it matches AutoCAD
  muscle memory, which the user has already told us to honour
  ([[feedback_autocad_ux]]).
- **[Trade-off]** Two passes of header cleanup instead of one big bang
  → accepted; smaller diffs are easier to review and roll back, and
  the action-button refactor still needs its own design discussion.

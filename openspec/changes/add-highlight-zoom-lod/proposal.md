## Why

The circle-scan fast path ([[add-circle-scan-fast-path]]) now returns
400,767 matches in 23.5 s on the reference BGA file — but most of that
wall time is the *render* loop, not the matcher. With ≈400 k cyan
highlight strokes drawn per frame regardless of zoom, pan and zoom
drop to <1 fps once a single BGA ball has been frame-selected.

The base-pass already collapses sub-pixel circles into 1-px batched
dots ([[archive/2026-05-15-optimize-bga-render]]); the four
highlight passes (scan-all / near-miss / selection+match /
hover+pinned) were intentionally exempted at that time so that
small-match counts (10s to 100s) remained eye-visible at zoom-out.
That exemption was the right call when the worst single scan returned
a few thousand matches. With the bucket fast path it can now return
hundreds of thousands, and at zoom-out the user can't visually
distinguish overlapping cyan strokes from a sea of cyan dots anyway —
both read as "lots of stuff here". The dot rendering is two orders of
magnitude faster.

## What Changes

- Every highlight pass in `app/static/canvas.js` SHALL apply the same
  sub-pixel circle LOD as the main pass: when a circle primitive's
  screen-space radius is below `DOT_THRESHOLD_CSS_PX`, render the
  highlight as a single device-pixel dot in the pass's highlight
  colour instead of a fattened `drawPrimitive` stroke.
- Each highlight pass batches per-colour dot positions into a
  `Path2D` (matching the main-pass bucket pattern at
  `canvas.js:565–583`) and emits one `fill` per colour in
  device-pixel space.
- Highlight colours retained per pass:
  - scan-all → `classColor(cls)` (one bucket per class)
  - near-miss → `NEARMISS_COLOR`
  - selection + match → `HIGHLIGHT_COLOR`
  - hover + pinned → `HOVER_COLOR`
- Non-circle primitives in highlight passes (polylines, lines) keep
  drawing through `drawPrimitive` — they don't have the
  rotational-symmetric "everything below 1 px is a point" property
  that justifies the LOD.
- No backend change. No API change. No persisted data change.

## Capabilities

### Modified Capabilities
- `viewer-ui`: relax the LOD requirement so highlight passes are
  permitted (and now expected) to batch sub-pixel circles as dots.

## Impact

- **Frontend (`app/static/canvas.js`)**: each of the four highlight
  passes gains a per-colour dot-bucket Map + a device-pixel flush block
  identical in shape to the existing main-pass implementation.
- **No tests touched** — there is no jsdom render harness; the
  scenario change is verified visually + on the existing render-timing
  status-line counters.
- **Status-line counters**: the existing `drawn / culled / dot`
  counters in the status line continue to reflect the *main* pass.
  Adding per-pass counters is out of scope; if needed for diagnosis,
  the developer can read frame time directly.

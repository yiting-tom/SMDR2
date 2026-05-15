## Why

Semiconductor-packaging DXFs routinely contain hundreds of thousands to
millions of BGA balls modelled as `CIRCLE` entities. The current pipeline
flattens every circle to a closed polyline of ~30+ vertices at
`CURVE_FLATTENING_DISTANCE = 0.01`, and the canvas renderer issues one
`beginPath`/`stroke` per primitive over the entire list every frame —
with no viewport culling and no level-of-detail. The viewer becomes
unusably laggy on real packaging files (e.g. `data/test_3layers.dxf`,
36 MB): pan/zoom/hover all stutter because every frame re-walks
millions of vertices.

The fix is local and additive: keep circles as circles, skip what
isn't on screen, and collapse sub-pixel circles into batched dots.
Picking, OSNAP, measure, and selection logic stay intact.

## What Changes

- **Backend**: `JSONBackend` (app/dxf.py) gains a `draw_path` fast-path
  that detects circular sub-paths (single closed sub-path whose vertices
  fit a circle within tolerance) and emits a new primitive
  `{type: "circle", center: [x, y], r: float, ...}` instead of a
  polyline. Non-circular paths continue to flatten as today.
- **Frontend draw**: `drawPrimitive` (canvas.js) learns the `circle`
  type and uses `ctx.arc` directly. `bboxOf` and `computePrimCircles`
  add a `circle` case (the latter just copies the values — no detection
  needed). Selection / hit-test / chain / snap paths add a `circle`
  case so behavior matches today's flattened-polyline output.
- **Viewport culling**: `render()` consults `primBBoxes` (already
  precomputed at `canvas.js:347`) and skips any primitive whose bbox
  is fully outside the visible world rect. Applied to the main pass
  and every highlight pass.
- **LOD — sub-pixel circle batching**: When a circle's screen-space
  radius is below ~0.75 px it is appended to a flat `Float32Array` of
  (x, y) and rendered as a single batched `Path2D` of 1-px dots
  (`ctx.fillRect` style) in one call per color. Eliminates per-circle
  draw overhead at zoom-out where individual balls are smaller than a
  pixel.
- **Status line**: extend the existing render-timing readout
  (`canvas.js:2519`) with `drawn / culled / dot` counts so we can
  verify the optimization on `data/test_3layers.dxf` and detect
  regressions in future.

No breaking change to public APIs or to persisted JSON formats:
`primitive_count`, `bbox`, and `background` all stay; new primitives
just carry a new `type` value that older consumers would skip.

## Capabilities

### New Capabilities
<!-- None — all changes extend existing capabilities. -->

### Modified Capabilities

- `dxf-pipeline`: add `circle` to the allowed primitive `type` enum;
  CIRCLE / fully-circular CIRCULAR-ARC entities SHALL be emitted as
  `circle` primitives, not flattened polylines.
- `viewer-ui`: canvas SHALL render the `circle` primitive natively;
  per-frame draw SHALL skip primitives whose bbox is outside the
  visible world rect; sub-pixel circles SHALL be drawn as batched dots.

## Impact

- **Code**: `app/dxf.py` (`JSONBackend.draw_path`, new helper
  `_detect_circle_subpath`); `app/static/canvas.js` (`drawPrimitive`,
  `bboxOf`, `computePrimCircles`, picking, chain spatial hash, snap,
  `render` culling + LOD batching); status-line label in viewer
  template.
- **Tests**: extend `tests/test_dxf.py` with a CIRCLE → `circle`
  primitive case (and one for true non-circular path → polyline);
  add a `tests/test_canvas_culling.js` node:test alongside
  `measure_core.js` if pure helpers are extracted (e.g.
  `circleScreenRadius`, `bboxIntersectsRect`).
- **Persisted artifacts**: `data/parsed/{file_id}.json` may now contain
  `circle` primitives. Existing parsed files keep working (no `circle`
  primitives → no new code path triggered); re-running preprocess
  produces the new shape.
- **Bench**: target file `data/test_3layers.dxf`. Acceptance numbers
  recorded in `tasks.md`: fetch, primitive count, bbox time, first
  render, and steady-state pan render — before vs. after.
- **No impact** on: matching engine (operates on handles), rule
  check, library DB schema, OpenSpec specs other than the two listed
  above.

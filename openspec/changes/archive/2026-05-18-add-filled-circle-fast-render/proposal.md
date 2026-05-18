## Why

The viewer's `circle`-primitive fast render path — `ctx.arc` for normal
zoom plus sub-pixel dot batching when on-screen radius ≤ 0.75 CSS px
(see `viewer-ui` requirement "Sub-pixel circle LOD batching") — only
fires when the backend emits `type == "circle"`. Filled circles that
come through ezdxf's `Frontend.draw_filled_paths` (the canonical path
for HATCH entities bounded by a circle, plus any other filled-circle
source) currently bypass the circle detector entirely and land as
`type == "filled_polygon"` with N flattened ring vertices. Two
consequences:

1. On packaging DXFs where balls / pads carry a HATCH fill, zoom-out
   redraws fill-rasterise hundreds of N-vertex rings per frame instead
   of one Path2D of 1×1 device-pixel dots — the same regression that
   motivated the original [[add-circle-scan-fast-path]] work on the
   matching side, except here it bites render performance.
2. Two visually identical filled balls produced by different DXF
   authoring paths (CIRCLE+HATCH vs an emit that already collapsed
   to a circle) carry different primitive types, so any downstream
   handling that branches on `type == "circle"` (`computePrimCircles`
   for OSNAP, the matcher's `kind == "circle"` dispatch via
   [[add-circle-scan-fast-path]]) treats them inconsistently.

User report: 「我發現有些CIRCLE會被填色 這會導致他不會使用我們的
fast_render 我希望就算被填色也套用一樣的fast_render」.

## What Changes

- `JSONBackend.draw_filled_paths` (`app/dxf.py`) gains a fast path:
  when the call has exactly one `NumpyPath2d` containing exactly one
  closed sub-path with `has_curves == True`, the backend flattens the
  sub-path, runs `_detect_circle_subpath`, and on success emits a
  single `{"type": "circle", "filled": True, "center": [x, y], "r": float, ...}`
  primitive instead of a `filled_polygon`. Bbox tracking SHALL use the
  circle's bounds, not the flattened vertices.
- The detection predicate (`_detect_circle_subpath`) is unchanged; the
  same `has_curves` gate as `draw_path` ensures a filled N-gon SMD pad
  (a polyline-only path) is never collapsed to a circle.
- Fallback for everything else (multi-path HATCH, multi-subpath HATCH
  with holes, non-circular sub-path) stays on the existing
  `filled_polygon` emit. No behavior change for those.
- Primitives with `type == "circle"` gain an OPTIONAL boolean `filled`
  field. Missing / falsey = stroke-only (the legacy default for
  `draw_path`-emitted CIRCLE entities); `true` = fill. Existing CIRCLE
  emit sites are NOT modified.
- `drawPrimitive` in `app/static/canvas.js` honours `p.filled` in its
  `case "circle"`: filled circles run `ctx.fill()`; if a highlight
  pass passes an explicit `stroke`, the circle is stroked on top
  (mirroring the existing `filled_polygon` branch). The sub-pixel
  dot-batch path is unchanged — it already keys on `p.type === "circle"`
  alone, so filled circles automatically inherit the LOD collapse.

## Capabilities

### Modified Capabilities
- `dxf-pipeline`: extend the circle-promotion rule to cover
  `Frontend.draw_filled_paths` and introduce the optional `filled`
  field on the `circle` primitive.
- `viewer-ui`: extend the canvas `circle`-render and sub-pixel LOD
  rules to cover filled circles.

## Impact

- **Backend (`app/dxf.py`)**: new fast path in `draw_filled_paths`;
  one new optional field (`filled`) on the `circle` primitive shape.
- **Frontend (`app/static/canvas.js`)**: `drawPrimitive` case
  `"circle"` honours `p.filled` (fill instead of stroke; both when a
  highlight pass passes `stroke`).
- **Downstream consumers**: `bboxOf`, `computePrimCircles`,
  `collect_entity_points`, `collect_entity_kinds`, the matcher's
  single-CIRCLE fast path, and the viewer hit-test / OSNAP code paths
  all already branch on `type === "circle"` (with no reference to
  fill state), so the new `filled` field is purely additive — no
  refactor required.
- **Persistence / JSON shape**: the parsed-primitives file
  (`data/parsed/{file_id}.json`) gains `filled: true` on the
  affected circle entries. The field is optional; older parsed JSONs
  re-loaded against the new code render correctly as stroke-only
  circles (matches their author intent — they were originally
  emitted from `draw_path`, not `draw_filled_paths`).
- **Matching**: zero change. Decorative HATCH primitives are
  filtered out of the matching pipeline at handle-index time, and
  any non-decorative filled circle's matcher contract was already
  the synthesized 8–64 point cloud from `collect_entity_points`
  (now reached via `type == "circle"` instead of via the
  `filled_polygon` ring fallback — better, not worse).
- **Tests**: one new test
  (`test_hatch_bounded_by_circle_emits_filled_circle`) in
  `tests/test_dxf.py` exercising the HATCH → filled-circle promotion
  and the `decorative` flag inheritance.

## Why

HATCH entities in packaging DXFs are pure decorative noise — solder-mask
fills, copper-pour patterns, hatched indicators — but the viewer still
spends render budget on them and their boundary edges emit polylines that
don't promote to `circle` primitives, so they bypass the sub-pixel dot-LOD
batching path. The result: degraded pan/zoom FPS on dense files and
visually jagged N-gon outlines when the user zooms in (the polyline
boundary is drawn with `ctx.lineTo`, never as a smooth arc). The existing
`decorative: true` flag excludes HATCH from selection/match/chain but does
nothing to keep them out of the render pipeline.

## What Changes

- **BREAKING**: `flatten_for_render` SHALL delete every HATCH entity from
  modelspace before driving ezdxf's `Frontend.draw_layout`. No HATCH ever
  produces a primitive — the result contains zero primitives whose source
  handle belongs to a HATCH entity.
- **BREAKING**: The HATCH-bounded → `circle` (filled) promotion (added by
  commit `48100bf`) is removed in effect, since HATCH never reaches the
  flatten pipeline. The `draw_filled_paths` code path stays (still used by
  SOLID hatch-fill from other entities), but its HATCH-specific scenarios
  are gone.
- The `decorative: true` flag mechanism stays: still applies to TEXT,
  MTEXT, DIMENSION. Only HATCH is removed from `DECORATIVE_DXFTYPES`
  membership — there is no longer anything decorative to flag for HATCH
  because no HATCH-sourced primitive is emitted.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `dxf-pipeline`: HATCH entities are stripped at parse time and emit no
  primitives. Existing HATCH→circle promotion scenarios are removed; a new
  scenario asserts HATCH emits zero primitives.

## Impact

- `app/dxf.py`: add HATCH-strip step in `flatten_for_render`; remove HATCH
  from `DECORATIVE_DXFTYPES`.
- `tests/test_dxf.py`: drop `test_hatch_bounded_by_circle_emits_filled_circle`,
  `test_hatch_bounded_by_polyline_circle_emits_filled_circle`, and the
  multi-sub-path HATCH test; add `test_hatch_emits_no_primitives`.
- `openspec/specs/dxf-pipeline/spec.md`: remove HATCH-bounded promotion
  scenarios, add HATCH-stripped scenario.
- Viewer (`canvas.js`): no code change required — fewer primitives in the
  JSON automatically. `dot` count rises, `drawn` count drops, render time
  improves on HATCH-heavy files.
- Downstream (matcher, rule-check, DRC bundle): unaffected — they already
  filtered on `decorative === true`, and HATCH primitives no longer exist
  at all.

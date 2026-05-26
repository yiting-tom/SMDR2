## Why

The viewer canvas renders every primitive the backend returns,
including those flagged `decorative=true` (TEXT / MTEXT / DIMENSION /
HATCH). When a DXF references a font that isn't installed on the
user's machine, ezdxf's drawing frontend falls back to per-character
placeholder rectangles — the user sees text as "boxes" stacked next
to each other ("一格格的長方形文字"). The fallback is a property of
the user's local font environment, so the same DXF renders cleanly
on one machine and as boxes on another.

The `decorative` flag already drives every other consumer's "ignore
this" behaviour:

- `find_matches_from_pointsets` / `_match_*` (`app/library.py:830`) —
  matching pipeline skips decorative primitives entirely
- `collect_entity_points(..., skip_decorative=True)`
  (`app/dxf.py:721`) — selection skips decorative

The viewer is the only consumer that still renders them — by
oversight, not by design. Filtering them out of the render loop
removes the font-fallback artefact and gives a cleaner, more useful
viewer (TEXT / MTEXT / DIMENSION / HATCH carry no information the
matching workflow uses).

## What Changes

- **`app/static/canvas.js`** — primitive render loop SHALL skip any
  primitive whose `decorative` property is true. Filter happens
  client-side at the bottom of the render path.
- **`/api/files/{file_id}/primitives` endpoint** — unchanged. The
  backend still ships every primitive (including decoratives) so
  future features that need them (e.g. a "Show text" toggle) can opt
  back in without an API change.
- **Matching / selection / scan-all** — unchanged. They already
  ignore decorative.

## Capabilities

### New Capabilities

_None._ This is a defect fix in an existing capability.

### Modified Capabilities

- `viewer-ui`: the viewer canvas rendering contract gains an explicit
  requirement that decorative primitives are not drawn. Existing
  "render every primitive the backend returns" wording (if present)
  needs to acknowledge the decorative exclusion.

## Impact

- **Code**: `app/static/canvas.js` only — one filter in the render
  loop. Estimated ~5 lines of code change.
- **APIs**: none. Backend `/primitives` contract unchanged.
- **Tests**: unit tests covering `prim["decorative"] = True` tagging
  in `tests/test_dxf.py` already exist and stay green (no behaviour
  changes there). No new backend tests needed. Front-end render is
  not unit-tested today; manual verification suffices.
- **Dependencies**: none.
- **Operational**: rendering work for typed-text-heavy DXFs drops
  modestly (fewer paths to draw); negligible perf delta on most
  packaging files.

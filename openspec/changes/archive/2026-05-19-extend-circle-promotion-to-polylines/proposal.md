## Why

Some packaging DXFs author BGA balls (and a handful of other "round"
features) as **closed LWPOLYLINE / POLYLINE entities approximating a
circle with straight line segments** — a many-sided regular polygon
that is visually indistinguishable from a CIRCLE. ezdxf surfaces these
through `Frontend.draw_path` with `sub.has_curves == False`, so today's
`_detect_circle_subpath` predicate (gated on `sub.is_closed and
sub.has_curves`) never runs and they land as `polyline` primitives.

User report: 「我發現有些dxf裡面的bga ball混雜著CIRCLE以及POLYLINE的
正多邊形（通常長的和CIRCLE一模一樣）」.

Three consequences of leaving these as polylines:

1. **Render**: the viewer's `circle`-primitive fast render path
   (`ctx.arc` + sub-pixel dot batching, see the `viewer-ui`
   capability) never fires for these balls. Zoom-out redraws stroke
   N-vertex closed polylines per ball instead of one Path2D of 1×1
   device-pixel dots — exactly the regression the original circle
   fast-path work motivated, except the BGA balls drawn as polylines
   miss the optimisation.
2. **Match scan**: the matcher's circle fast path
   (`type == "circle"` → radius-bucket O(N) scan, see
   [[add-circle-scan-fast-path]]) does not engage; polyline candidates
   take the general PCA + Chamfer path, multiplied by the BGA ball
   count (~hundreds per file).
3. **Consistency**: two visually identical balls authored by different
   CAD pipelines (CIRCLE vs LWPOLYLINE N-gon) carry different
   primitive types, so downstream type-switching code
   (`computePrimCircles` for OSNAP, the matcher dispatch, the rule
   check report) treats them inconsistently.

The existing `has_curves` gate is load-bearing: it protects real
low-N polygon pads (e.g., an N=8 octagonal SMD pad) from being eaten
by the radial test, which a perfectly regular octagon trivially
passes. So the fix is not to drop the gate outright, but to widen
the detector with a stricter vertex-count threshold for the
no-curves case.

## What Changes

- `app/dxf.py` introduces a second threshold
  `CIRCLE_MIN_VERTS_NOCURVE = 11`, distinct from the existing
  `CIRCLE_MIN_VERTS = 8`. The new threshold applies only when the
  sub-path has `has_curves == False`.
  - Rationale for 11: domain-confirmed (BGA balls drawn as polylines
    are typically N ≥ ~16 from CAD flattening), and 11 is comfortably
    above the largest legitimate polygon-pad vertex count seen in
    practice (the deliberate-pad cases are N ∈ {3, 4, 6, 8, 12}, of
    which 12 is rare and N=11 is essentially never authored on
    purpose because odd-N polygons aren't load-bearing pad shapes).
- `_detect_circle_subpath(points, min_verts=CIRCLE_MIN_VERTS)` gains
  an optional `min_verts` parameter so callers can pass the
  no-curves threshold. The internal `len(points) < min_verts` and
  `n < min_verts` checks use the parameter. The radial-variance
  predicate (`(rmax - rmin) / rmean ≤ CIRCLE_RADIAL_TOL`) is
  unchanged.
- `JSONBackend.draw_path` drops `has_curves` from the gate and
  instead picks `min_verts = CIRCLE_MIN_VERTS if has_curves else
  CIRCLE_MIN_VERTS_NOCURVE`, then runs the detector. Detection
  failure or `is_closed == False` continues to fall through to the
  existing `polyline` emit. Emit shape is unchanged.
- `JSONBackend.draw_filled_paths`'s single-path fast path applies
  the same dual-threshold logic, so a HATCH whose only boundary is
  an N-gon LWPOLYLINE (N ≥ 11) collapses to `{type:"circle",
  filled:true, ...}` instead of `filled_polygon`. Multi-path /
  multi-sub-path HATCH continues to fall through to `filled_polygon`.
- The lockstep comment on `CIRCLE_MIN_VERTS` / `CIRCLE_RADIAL_TOL`
  is updated to note that the no-curves case is server-only:
  `app/static/measure_core.js` retains its `CIRCLE_MIN_VERTS = 8`
  for client-side OSNAP detection on polylines that survive
  server-side conversion (i.e., the has-curves case where ezdxf
  flattened a real curve but the detector failed, plus pads with
  N < 11). No client change.

No persisted-data shape change. No API change. No frontend change.
The existing parsed-primitives file (`data/parsed/{file_id}.json`)
gains nothing new on its primitive shape — `circle` primitives
already carry `center`, `r`, optional `filled`, and the standard
properties. Older parsed JSONs continue to deserialise unchanged;
re-running preprocess against an existing DXF will simply produce
more `circle` and fewer `polyline` / `filled_polygon` entries.

## Capabilities

### Modified Capabilities
- `dxf-pipeline`: extend the circle-promotion rule to cover
  `has_curves == False` closed sub-paths (both stroked via
  `draw_path` and filled via `draw_filled_paths`), gated on a
  higher minimum vertex count (`CIRCLE_MIN_VERTS_NOCURVE = 11`).

## Impact

- **Backend (`app/dxf.py`)**: one new constant; one parameter
  added to `_detect_circle_subpath`; gate adjustment in
  `draw_path` and `draw_filled_paths`. ~20 lines of touched code,
  no new files.
- **Matching (`app/matching.py`)**: zero change. Newly-promoted
  circles flow into the existing circle fast path
  ([[add-circle-scan-fast-path]]) via `kind == "circle"` /
  `type == "circle"` dispatch, which already keys on the
  `EntityShape` synthesised from a `circle` primitive. Match
  results SHALL be equivalent to or better than today (more
  candidates take the O(N) radius-bucket path instead of the
  generic PCA / Chamfer path).
- **Render (`app/static/canvas.js`)**: zero change. Newly-promoted
  circles render via the existing `case "circle"` (with or
  without `filled`) plus the sub-pixel LOD batch
  ([[add-filled-circle-fast-render]] / [[add-highlight-zoom-lod]]).
- **Persistence**: re-running preprocess on a DXF with BGA balls
  authored as LWPOLYLINE will rewrite some `polyline` /
  `filled_polygon` entries in `data/parsed/{file_id}.json` to
  `circle`. The `data/match/{file_id}.json` shape is unchanged
  (still `[[handle, ...], ...]`); the per-match handle lists may
  shift because the matcher now sees these balls as `circle`
  primitives, but each handle continues to refer to the same
  source DXF entity (handles are author-stable). Saved-match
  invalidation on side-region edits already exists; no new
  invalidation path is needed.
- **Frontend interactions**: hit-test, OSNAP, and selection paths
  all already branch on `type === "circle"` (the existing
  `computePrimCircles` cache), so newly-promoted circles inherit
  centre / quadrant snap automatically.
- **Tests (`tests/test_dxf.py`)**: new positive cases (high-N
  circular LWPOLYLINE → circle; filled high-N circular LWPOLYLINE
  → filled circle) and new negative case (N=8 circular LWPOLYLINE
  stays polyline — the new N gate, not the radial test, blocks
  it). Existing `test_non_circular_closed_polyline_stays_polyline`
  remains green (N=8 octagon with alternating radii — fails both
  the new N gate AND the radial test).
- **Perf**: detector runs on more sub-paths (every closed sub-path
  rather than only `has_curves` ones). Each detector call is
  `O(N)` with N ≤ a few hundred; the added cost is dominated by
  the `draw_path` polyline build that already runs for the same
  sub-path. End-to-end preprocess walltime expected within noise;
  parsed JSON size SHRINKS on BGA-heavy files (one `circle`
  entry replaces an N-vertex `polyline` entry).
- **Risk**: misclassifying a deliberate high-N polygon pad
  (e.g., N=12 dodecagonal pad) as a circle. The radial-variance
  gate (≤ 2 %) still applies, so the pad would have to be both
  N ≥ 11 *and* geometrically near-circular to be promoted. A
  regular dodecagon is geometrically near-circular by
  construction, so this is a real risk for that specific shape;
  mitigation is "use a CIRCLE entity or a sub-radial-variance
  authoring choice for true polygon pads". Documented in the
  spec scenario list; no automated mitigation beyond the
  threshold itself.

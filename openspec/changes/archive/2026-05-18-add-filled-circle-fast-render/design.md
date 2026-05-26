# Design

## Why this lives in `draw_filled_paths`, not a post-process

Three plausible places to fold filled circles into the fast path:

1. **In `draw_filled_paths`** (chosen): catch the geometry while we
   still hold the original `NumpyPath2d` with its `is_closed` /
   `has_curves` hints.
2. **Post-process the primitive list** after `flatten_for_render`
   returns: re-detect circles by running `_detect_circle_subpath` on
   every single-ring `filled_polygon`.
3. **Frontend-side detection** in `computePrimCircles`: extend the
   client-side `detectCircle` to also drive the dot-batch path.

(2) loses the `has_curves` gate. A filled N-gon SMD pad with N ≥ 8 is
indistinguishable from a circle on vertex layout alone — the gate
matters. We'd either re-introduce false positives (octagonal pads
collapsing to circles) or have to encode the curve hint separately
through the JSON, which adds a field for no payoff over (1).

(3) helps the OSNAP center / quadrant snap on filled balls (which
already works because `computePrimCircles` runs `detectCircle` on
single-ring `filled_polygon`), but the LOD dot-batch decision is
made earlier in the render loop on `p.type === "circle"` for cheap
dispatch, and rewriting that to also dispatch on detected circles
would either re-run `detectCircle` every frame or thread a derived
type tag through every render code path. Worse: the matcher's
single-CIRCLE fast path keys on `kind == "circle"` derived from
`primitives[*].type` — fixing render alone leaves matching to keep
treating two visually identical balls inconsistently based on author
intent.

(1) is the smallest surgical change: one branch in
`draw_filled_paths`, one branch in `drawPrimitive`'s circle case,
zero change to every downstream consumer that already keys on
`type === "circle"`.

## The `has_curves` gate

`_detect_circle_subpath` uses only radial-variance / vertex-count
tests — it can't tell an 8-vertex flat-edged octagon from an 8-vertex
circle approximation when the radial samples happen to align.
`draw_path` already gates on `sub.is_closed and sub.has_curves` for
exactly this reason; copying the same gate into `draw_filled_paths`
keeps the false-positive frontier identical between the stroke and
fill code paths. A HATCH whose boundary is a CIRCLE edge will have
`has_curves == True`; a HATCH whose boundary is a polyline-only
N-gon path will have `has_curves == False` and stays a
`filled_polygon`.

## Why "exactly one sub-path"

A HATCH bounded by a circle with no holes → 1 path × 1 sub-path =
fast path. A HATCH bounded by an outer circle minus an inner circle
(annulus) → 1 path × 2 sub-paths or 2 paths × 1 sub-path each →
fallback to `filled_polygon` so the even-odd fill rule still cuts
out the hole. Detecting an annulus and emitting two circle primitives
(one fill, one stroke-as-hole) would require teaching every
downstream consumer about ring topology; the user's request and the
hot case both involve solid fills, so we deliberately keep the fast
path single-subpath only.

## The new `filled` field on the `circle` primitive

The existing `circle` primitive shape is
`{type, center, r, color, layer, lineweight, handle}` (the `_props`
fields plus the geometric two). Adding an optional `filled: bool`
field is preferred over introducing a separate `filled_circle` type
because:

- Every downstream consumer of `type === "circle"` (matcher kind
  derivation, OSNAP, hit-test, bbox, dot batching) is fill-agnostic
  and should continue to work without a `case` arm per new type.
- The JSON parsed-primitives file already round-trips unknown
  optional fields (it's a `dict` dump, no schema validation).
- `drawPrimitive` mirrors the existing `filled_polygon` pattern —
  fill with `p.color`, additionally stroke if a highlight pass
  passes a `stroke` — so the per-frame branch is one `if`.

Missing/`false` `filled` keeps the legacy stroke-only render. This
matches author intent for a `draw_path`-emitted CIRCLE (which is
always stroke-only in DXF semantics).

## Bbox tracking

When we promote a filled sub-path to a `circle` primitive, we MUST
NOT call `_track_points(pts)` on the flattened vertices first — the
flattened polyline's bounding extents drift by up to one
flattening-tolerance unit from the true circle bounds, which can
nudge the file-wide bbox by a sub-pixel amount in pathological
small-circle cases and break bbox-identity tests. We instead track
`(cx ± r, cy ± r)` directly, matching the `draw_path` circle branch.

## Matching pipeline impact

The matcher branches on `kind` (derived from `type`) via
[[add-circle-scan-fast-path]]. By emitting filled circles as
`type == "circle"`, any non-decorative filled circle's matcher
contract changes from "ring-of-N-points filled_polygon" to "circle
kind + synthesized 8–64 point cloud" — see `dxf-pipeline`
requirement "Matcher consumes circle primitives via synthetic vertex
sampling". This is a strict improvement (canonical representation,
single-CIRCLE fast path becomes reachable for filled balls), but
matters only if a non-decorative filled circle actually exists in
any DXF the user feeds in. The dominant source of filled circles in
practice — HATCH — is in `DECORATIVE_DXFTYPES`, so it doesn't reach
the matcher at all and the matching contract is effectively
unchanged. We don't introduce a fingerprint-stability concern
because no library template has been committed against a filled
HATCH circle (decorative entities are filtered before the handle
index is built).

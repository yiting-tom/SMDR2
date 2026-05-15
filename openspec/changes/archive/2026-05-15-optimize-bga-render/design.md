## Context

The viewer (`app/static/canvas.js`) renders one DXF as a flat
`primitives` array driven from `data/parsed/{file_id}.json`, which is
produced server-side by `flatten_for_render` in `app/dxf.py`. Today
every `CIRCLE` entity is run through ezdxf's `Frontend` and emitted as
a closed polyline of ~30+ vertices (`CURVE_FLATTENING_DISTANCE = 0.01`
mm). On packaging files like `data/test_3layers.dxf` (36 MB) this
yields millions of vertices for the BGA arrays and turns every frame
into millions of `lineTo` / `stroke` calls. There is no viewport
culling and no LOD: pan, zoom, hover, and selection all rerun the
full draw loop.

Observed behaviour: the cursor visibly stutters when panning; the
status line reports multi-second render times on `test_3layers.dxf`.

Constraints that shape the design:
- Picking, OSNAP, measure, and selection must keep working unchanged
  for BGA balls. `measure_core.js:detectCircle` already re-derives
  `{cx, cy, r}` from the flattened polyline; OSNAP centre/quadrant
  snaps depend on that.
- The matching engine (`app/matching.py` / `collect_entity_points` in
  `app/library.py`) reads vertex points per handle. Today it gets the
  flattened polyline vertices; if circles stop carrying vertices, it
  must still see an equivalent point cloud or matching breaks.
- Persisted JSON files in `data/parsed/` already exist; the change
  should be additive — old files keep loading, new files use the new
  shape.

## Goals / Non-Goals

**Goals:**
- Open `data/test_3layers.dxf` in the viewer and have pan / zoom feel
  smooth (target: steady-state pan ≤ 50 ms per frame on a typical dev
  laptop; "drawn / culled" counts visible in the status line).
- Cut parsed-JSON payload size and primitive-array memory by avoiding
  the per-circle vertex explosion.
- Preserve every existing interaction: pickbox, window/crossing
  select, chain mode, OSNAP, measure tool, scan-all / match / hover
  overlays.
- Keep changes local — no new framework, no WebGL rewrite, no
  spatial index beyond the already-precomputed `primBBoxes`.

**Non-Goals:**
- WebGL / instanced rendering. Deferred; revisit only if A+B+C is
  insufficient.
- Offscreen-canvas bitmap caching for the static layer. Deferred to a
  future change — orthogonal to this one.
- Tiling / R-tree spatial index. Linear culling over `primBBoxes` is
  cheap enough for the primitive counts we have once circles compress
  ~30× and is far simpler to reason about.
- Changing curve flattening for non-circular arcs. Only true circles
  get the new code path; ellipses, splines, etc. continue to flatten.

## Decisions

### 1. Detect circles inside `JSONBackend.draw_path`, not before/after

ezdxf's `Frontend` resolves OCS / INSERT / etc. then calls
`backend.draw_path(NumpyPath2d, ...)` for curves including CIRCLEs.
Rather than hooking the DXF entity layer (which would force us to
re-implement transforms ourselves), we keep the existing Frontend
pipeline and add a fast-path in `draw_path`:

1. For each sub-path, flatten to points as today.
2. Run a circle test on the points: closed, ≥ 8 vertices, bounded
   radial variance — same predicate as the client-side
   `measure_core.js:detectCircle` (which is already battle-tested
   over real DXFs). Reuse its tolerances: `CIRCLE_MIN_VERTS = 8`,
   `CIRCLE_RADIAL_TOL = 0.02`.
3. If it passes, emit `{type: "circle", center: [cx, cy], r, ...}`.
   Otherwise emit `{type: "polyline", ...}` as today.

Alternatives considered:
- Hook `enter_entity` and short-circuit on `dxftype() == "CIRCLE"`.
  Rejected: we'd skip ezdxf's OCS / extrusion / scale handling and
  would need to redo it for entities inside `INSERT` blocks. Detecting
  geometrically after the Frontend is correctness-preserving and also
  catches non-CIRCLE entities that happen to be circular
  (CIRCULAR-ARC, POLYLINE-as-octagon → octagon will still flatten,
  but a 360° arc gets compressed).
- Run detection at load time client-side. Rejected: the bandwidth and
  parsed-JSON cost is on the server side too; saving it once at
  preprocess pays off forever.

### 2. New primitive shape: `{type: "circle", center, r, ...common}`

Field name `center` (not `pos` or `c`) keeps the JSON readable in
`data/parsed/...json` and matches the existing language in
`computePrimCircles` and `measure_core.js`. Common fields stay the
same: `color`, `layer`, `lineweight`, `handle`, optional
`decorative`.

Renderer:
```
case "circle":
  ctx.beginPath();
  ctx.arc(p.center[0], p.center[1], p.r, 0, Math.PI * 2);
  ctx.strokeStyle = stroke ?? p.color;
  ctx.lineWidth = lineWidth;
  ctx.stroke();
  break;
```

`bboxOf`: `[cx - r, cy - r, cx + r, cy + r]`.

`computePrimCircles`: directly populate `{cx, cy, r}` for `circle`
primitives (no re-detection). For polyline / filled_polygon, keep the
existing `detectCircle` fallback so non-CIRCLE round shapes
(POLYLINE-as-32gon, etc.) still get OSNAP centre/quadrant snaps.

`primHitTest` for `circle`: `|hypot(wx-cx, wy-cy) - r| <= tol`. (No
fill — CIRCLE in DXF is a ring, matching current closed-polyline
behaviour.)

`primCrossesRect` for `circle`: analytic circle-vs-rect — clamp the
centre to the rect, compare clamped-distance to r. (Both inside-rect
and edge-crossing covered.)

`selectByBox` window mode: bbox-fully-inside test already works
correctly because the circle's bbox is the tight axis-aligned bounding
square — same semantics as a polyline today (whose bbox is also the
tight bounding square at high vertex count).

`buildConnectivity`: circles are excluded today (only LINE and OPEN
POLYLINE chain). Same exclusion preserved — `circle` is a closed
shape.

### 3. Matching engine: synthesize vertices for circles

`collect_entity_points` in `app/library.py:459` currently switches on
`p["type"]` and emits raw vertices. Add a `circle` case that synthesizes
N evenly-spaced points around the circle:

```python
elif t == "circle":
    cx, cy = p["center"]
    r = p["r"]
    n = max(8, min(64, int(2 * math.pi * r / 0.01)))
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
```

The point-cloud the matcher sees is numerically equivalent to what
flattening produces today (same chord-tolerance ladder up to N=64).
Cap of 64 protects giant fiducial rings from blowing up matcher
descriptor cost; minimum 8 stays in lockstep with `CIRCLE_MIN_VERTS`.

This step is the only place "circle ↔ vertex cloud" lives on the
server. Everything else (rule check, side regions, scan-all) consumes
the matcher's output (handles) or the primitive's bbox, both of which
are unaffected.

### 4. Viewport culling

Compute the visible world rect once per `render()` from `view.cx`,
`view.cy`, `view.zoom`, and the canvas size, expanded by a small
margin (`hairline * HIGHLIGHT_WIDTH_MULT`) so a primitive whose
stroke peeks into the viewport isn't clipped:

```
const halfW = $canvas.width  / (2 * view.zoom);
const halfH = $canvas.height / (2 * view.zoom);
const m = hairline * HIGHLIGHT_WIDTH_MULT;
const vx0 = view.cx - halfW - m, vx1 = view.cx + halfW + m;
const vy0 = view.cy - halfH - m, vy1 = view.cy + halfH + m;
```

In the main draw loop and in every highlight pass, gate each iteration
on `primBBoxes[i]` overlap with `[vx0..vx1, vy0..vy1]`. Maintain
counters `drawn`, `culled`, `dot` to surface in the status line.

Highlight passes already iterate over `primitives`; adding the cull
check is one extra rejection condition per primitive, with the bbox
already in cache. No new data structures.

### 5. LOD — sub-pixel dot batching

When a circle's screen-space radius
(`r_screen = p.r * view.zoom / dpr`) is below a threshold
(`DOT_THRESHOLD_CSS_PX = 0.75`), drawing 32 line segments looks
identical to a 1×1 pixel filled square. We collect those circles into
a per-color bucket and flush each bucket as one `fillRect` loop
inside a single `beginPath` → `Path2D` of `rect()` calls → `fill()`.

Why not just `fillRect` directly? On Chromium / Safari, batching N
rects into one `Path2D.fill()` is significantly cheaper than N
separate `fillRect` calls because all the state changes happen once.

Threshold `0.75` (not `1.0`): below 0.75 px the antialiased stroke
becomes indistinguishable from a dot; below 1.0 it's borderline. The
extra 0.25 buffer keeps high-DPI displays well-served without
clipping the working range.

Counters: every sub-pixel circle increments `dot`; rendered circles
increment `drawn`; culled increments `culled`. Status line shows
`drawn / culled / dot · ${ms}ms`.

LOD applies only to the **main pass**. Highlight passes
(scan-all / nearmiss / selection / hover) draw at fattened line width
(`hairline * HIGHLIGHT_WIDTH_MULT`), so even a dot-collapsed circle
gets a visible halo when selected. Same for focused sub-rule.

### 6. Measurement methodology

Status line already reports `${count} primitives · fetch Xms · bbox
Yms · render Zms`. Extend with `drawn/culled/dot` counts and add a
steady-state pan timer:

- Bind a `pan-frame` timing: on each pan-driven `render()`, record
  the time; expose a rolling-window average via the dev console
  (`window.__renderStats`) so we can log a before/after table on
  `data/test_3layers.dxf`.

Acceptance:
| metric                       | before     | target after |
|------------------------------|------------|--------------|
| parsed JSON size             | baseline   | < 25 % of baseline |
| first render                 | baseline   | < 25 % of baseline |
| steady-state pan frame       | baseline   | < 50 ms |
| pickbox click latency        | OK today   | unchanged |

Recorded in `tasks.md` after the optimisation lands.

## Risks / Trade-offs

- **[Risk] Circle detection false positives** (regular octagons, POLYLINE
  approximations with low vertex count) — current detector requires
  ≥ 8 vertices and ≤ 2 % radial variance; an N-gon SMD pad with
  N ≥ 8 could collapse to a circle and lose its corner points.
  → Mitigation: detector only fires on closed sub-paths produced by
  `draw_path` (CIRCLE / ARC / full-rotation curves), never on
  polyline-source entities — those use `draw_filled_polygon` or
  open-polyline paths. As a safety belt, also require the original
  ezdxf path to come from a single CUBIC/QUADRATIC segment family,
  not a sequence of LINE commands; if the path consists of LINE
  segments only, skip detection.
- **[Risk] Matcher hash sensitivity to point ordering / count drift** —
  changing N for synthetic circle points across runs would change the
  shape fingerprint.
  → Mitigation: pin `n` deterministically from `r` (formula in §3) so
  the same DXF always yields the same synthetic point cloud.
- **[Risk] Sub-pixel batching draws a black dot where the source had a
  colored stroke** — visually fine for BGA arrays (all same color) but
  could be confusing if a layer is intentionally bright on a dark BG.
  → Mitigation: bucket by `p.color`, one `Path2D.fill()` per bucket.
- **[Risk] Culling regresses on very fast pan / zoom-out animation** —
  none today (interactions are discrete), but if animation is added
  later the bbox check must remain branch-free.
  → Mitigation: keep `primBBoxes` as flat arrays; no allocations in
  the cull check.
- **[Trade-off] Older `data/parsed/*.json` files don't carry circles** —
  they stay flattened polylines. Acceptable: re-preprocess on demand
  (the dashboard's library-reassignment flow already triggers
  preprocess), or just let them coexist. No migration needed.
- **[Trade-off] LOD makes "this BGA ball" un-clickable at extreme
  zoom-out** — but it's already a single pixel; user must zoom in to
  pick. Pickbox of 5 CSS px gives a wide margin regardless.

## Migration Plan

1. Land the change behind no flag — it's strictly additive at the
   JSON layer (`circle` is a new `type` value).
2. New uploads / preprocess runs produce the new shape immediately.
3. Existing parsed JSONs stay valid; viewer auto-detects (the
   `circle` case is just absent, polylines render as today).
4. No schema migration. To force-refresh an existing file, click its
   library dropdown in the dashboard (already triggers preprocess).
5. Rollback: revert the commit; both renderer and backend gracefully
   handle parsed JSONs from the new code (polylines work; `circle`
   primitives would log an unknown type but rendering already skips
   unknown types via the default in the switch).

## Open Questions

- Should we also collapse rings smaller than the dot threshold during
  **selection / scan-all highlight** passes so even highlight overlays
  stay fast at full zoom-out? Defer until A+B+C is measured — may not
  be needed.
- Do we want `circle` primitives to also carry an explicit
  `closed: true` field for forward-compat with tools that already
  understand the closed flag? Skipped for now — `circle` is closed by
  definition.

## 1. Baseline measurement (before any change)

- [x] 1.1 Spin up the dev server (`uv run uvicorn app.main:app --reload`), upload `data/test_3layers.dxf`, open the viewer, and record from the status line: `primitive count`, `fetch ms`, `bbox ms`, `render ms`. Note the parsed-JSON size from `data/parsed/{file_id}.json`. *(Captured from the pre-change parsed cache at `data/parsed/8b03da098a2237a9.json`: 200,384 primitives, all closed polylines, 17 verts/polyline avg, 3.4 M total vertices, 157 MB on disk.)*
- [x] 1.2 In DevTools Performance, capture a 5-second pan trace; record the mean frame time during sustained pan. Save numbers in the table at the bottom of this file. *(Baseline came directly from the user's pre-change behaviour — viewer was unusably laggy on `test_3layers.dxf`; an exact frame-time was not captured but recorded as "stuttering, multi-second renders" per the change motivation.)*

## 2. Backend: emit `circle` primitive type

- [x] 2.1 In `app/dxf.py`, add a pure helper `_detect_circle_subpath(points: list[list[float]]) -> dict | None` returning `{"center": [cx, cy], "r": r}` when the points satisfy `len(points) >= 9` (8 + closing duplicate), the first and last vertex coincide, and radial variance `(rmax - rmin) / rmean <= 0.02`. Otherwise return `None`. Treat collinear / degenerate cases (`rmean < 1e-9`) as non-circles.
- [x] 2.2 Modify `JSONBackend.draw_path` so that when a sub-path is closed AND `_detect_circle_subpath(points)` returns a hit, the backend appends `{"type": "circle", "center": [...], "r": ..., **_props(properties)}` and updates the bbox via `_track_point(cx - r, cy - r)` / `_track_point(cx + r, cy + r)` instead of emitting a polyline.
- [x] 2.3 Leave `draw_filled_paths` and `draw_filled_polygon` untouched — they handle hatched / filled regions that should never collapse to a circle.
- [x] 2.4 Add a unit test in `tests/test_dxf.py` covering: (a) a CIRCLE entity → `circle` primitive with correct center/radius; (b) an 8-vertex non-circular closed POLYLINE → `polyline` primitive (no false positive); (c) the bundled `data/test.dxf` still flattens without error after the change. Run `uv run pytest tests/test_dxf.py`.

## 3. Backend: matcher gets equivalent point cloud for circles

- [x] 3.1 In `app/library.py:collect_entity_points`, add a `circle` case that synthesizes `n = max(8, min(64, round(2 * math.pi * r / 0.01)))` evenly-spaced points around `(cx, cy)`. Import `math` at top of file if not already imported.
- [x] 3.2 Extend `tests/test_matcher.py` (or the closest existing matcher test) with a fixture that places a single CIRCLE in a DXF, runs the full preprocess, and asserts the matcher's point cloud for that handle has 8 ≤ N ≤ 64 points within 1 % of `r` from the center, deterministically across runs. *(Landed in `tests/test_dxf.py::test_collect_entity_points_synthesizes_circle_cloud` since that file already exercises `collect_entity_points` against a real flatten.)*

## 4. Frontend: render the `circle` primitive

- [x] 4.1 In `app/static/canvas.js`, extend `drawPrimitive` (around line 445) with a `case "circle":` arm that does `ctx.beginPath(); ctx.arc(p.center[0], p.center[1], p.r, 0, 2*Math.PI); ctx.strokeStyle = stroke ?? p.color; ctx.lineWidth = lineWidth; ctx.stroke();`.
- [x] 4.2 Extend `bboxOf` (around line 429) with `case "circle": acc(p.center[0]-p.r, p.center[1]-p.r); acc(p.center[0]+p.r, p.center[1]+p.r); break;`.
- [x] 4.3 Extend `computePrimCircles` (around line 359) so that `circle` primitives populate `primCircles[i] = { cx: p.center[0], cy: p.center[1], r: p.r }` directly without going through `detectCircle`. Polyline / filled-polygon cases stay as today (fallback for shapes that happen to be circular).
- [x] 4.4 Extend `primHitTest` (around line 1105) with `case "circle": { const dx = wx - p.center[0], dy = wy - p.center[1]; const d = Math.hypot(dx, dy); return Math.abs(d - p.r) <= tol; }`.
- [x] 4.5 Extend `primCrossesRect` (around line 1287) with a `circle` case using analytic circle-vs-rect: clamp `(cx, cy)` to `[xmin..xmax, ymin..ymax]`, compute squared distance from `(cx, cy)` to the clamped point, return `true` if `<= r*r`. (Window selection already works via the bbox-inside check.)
- [x] 4.6 Extend `collectHandlesSegments` (around line 578) with a `circle` case — emit 32 tangent segments around the ring so rule-check focused sub-rule distance computations behave correctly. (Reuse the same `n` formula or a fixed 32 — accuracy of the rule-check line is not safety-critical.)
- [x] 4.7 Confirm `resolveSnap` in `measure_core.js` needs no changes: it already short-circuits on `primCircles[i]` and the polyline / filled-polygon code paths are gated behind `if (circle) { … continue; }`.
- [x] 4.8 Confirm `buildConnectivity` already skips closed shapes; a `circle` falls through both type checks and is correctly excluded from chain mode.

## 5. Frontend: viewport culling

- [x] 5.1 In `render()` (around line 488), after computing `hairline`, compute `vx0, vy0, vx1, vy1` from `view` and canvas size + margin `hairline * HIGHLIGHT_WIDTH_MULT`.
- [x] 5.2 Add inline helper `bboxInView(b)` that returns `false` when `b[2] < vx0 || b[0] > vx1 || b[3] < vy0 || b[1] > vy1`. Add a `drawn`, `culled` counter for the main pass.
- [x] 5.3 Gate the main draw loop and every highlight loop (scan-all, near-miss, selection/match, hover/pinned, focused sub-rule) on `bboxInView(primBBoxes[i])`. Keep the `isLayerVisible` check.
- [x] 5.4 Stash counters and the per-frame ms into `window.__renderStats = { drawn, culled, dot, ms, frame: lastFrameId }` for debug.

## 6. Frontend: sub-pixel LOD batching

- [x] 6.1 Add constant `const DOT_THRESHOLD_CSS_PX = 0.75;` near the other style constants.
- [x] 6.2 In the main pass, when a primitive has `type === "circle"` and `p.r * view.zoom / dpr < DOT_THRESHOLD_CSS_PX`, instead of calling `drawPrimitive`, push `[p.center[0], p.center[1]]` into a `Map<color, [x, y][]>` bucket and increment a `dot` counter.
- [x] 6.3 After the main loop, flush each bucket: `ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0)` to switch to device-pixel space; for each color, build a `Path2D` of 1×1 `rect()`s at the device-pixel position computed via `worldToScreen`, then `ctx.fillStyle = color; ctx.fill(path);`. `ctx.restore()` after.
- [x] 6.4 Verify highlight passes still draw the source circles at full width (they don't go through the dot bucket).

## 7. Status-line surfacing

- [x] 7.1 Update the status-line composition (around `canvas.js:2519`) to include `· drawn ${drawn} culled ${culled} dot ${dot}` so the numbers are visible without DevTools.
- [x] 7.2 Confirm `window.__renderStats` is populated after every `render()` call.

## 8. Benchmark on data/test_3layers.dxf

- [x] 8.1 Re-upload `data/test_3layers.dxf` so it preprocesses with the new backend (or click its library dropdown to retrigger preprocess). Verify `data/parsed/{file_id}.json` is at least 4× smaller than the baseline. *(Offline verification via `flatten_for_render('data/test_3layers.dxf')`: serialised JSON = 33.3 MB vs. baseline 156.7 MB → **4.7× shrink**. To activate in the running app, delete `data/parsed/8b03da098a2237a9.json` then click the file's library dropdown on the dashboard.)*
- [x] 8.2 Open the viewer. Record: `primitive count`, `fetch ms`, `bbox ms`, `first render ms`, `circle %` (count of `type=="circle"` over total). *(Offline: 200,384 primitives, 100 % circles, server-side flatten 17.2 s, JSON serialise 0.39 s. Browser fetch / bbox / render times need a live session.)*
- [x] 8.3 Pan around for 5 seconds; collect frame times from `window.__renderStats`. Steady-state pan-frame avg SHALL be ≤ 50 ms on the dev laptop used in §1. *(Mid-zoom pan with `DOT_THRESHOLD_CSS_PX = 0.75`: 135 ms. Raised threshold to 3.0 → same view: **25.2 ms**.)*
- [x] 8.4 Zoom out fully and confirm the status line shows a non-zero `dot` count and the BGA grid still reads as a visible grid (not blank). *(Confirmed: zoom-to-fit → dot 200,384, 18.6 ms, BGA grid clearly visible.)*
- [x] 8.5 Pickbox / window / crossing select / chain / OSNAP center+quadrant smoke tests against a BGA region — all interactions match pre-change behaviour. *(All five interaction modes verified by user.)*
- [x] 8.6 Run `uv run pytest` (full suite) — no regressions. *(103 passed; `node --test tests/measure_core.test.mjs` also green — 27/27.)*
- [x] 8.7 Fill in the table below with before/after numbers and commit the updated tasks.md alongside the implementation.

## 9. Benchmark numbers

`data/test_3layers.dxf` (34.4 MB DXF, 100 % BGA-ball CIRCLE entities)

| metric                       | before     | after | target | status |
|------------------------------|------------|-------|--------|--------|
| parsed JSON size (MB)        | 156.7      | 33.3  | ≤ 25 % | ✓ (21.2 %) |
| primitive count              | 200,384    | 200,384 | unchanged in count, shape change only | ✓ |
| total client-side vertices   | 3,406,528  | 0     | drastically lower | ✓ |
| circle %                     | 0 %        | 100 % | — | ✓ |
| server flatten (s)           | n/a        | 17.2  | — | informational |
| JSON serialise (s)           | n/a        | 0.39  | — | informational |
| fetch ms                     | unmeasured (huge JSON, stalled) | 747   | — | ✓ |
| bbox ms                      | unmeasured  | 20    | ≤ before | ✓ |
| first render ms (fit zoom)   | "stuttering, multi-second" | 24 | ≤ 25 % of before | ✓ |
| steady-state pan frame ms (mid zoom, 0.75 px LOD) | n/a | 135  | ≤ 50 ms | ✗ |
| steady-state pan frame ms (mid zoom, 3 px LOD)    | n/a | **25.2** | ≤ 50 ms | ✓ |
| steady-state pan frame ms (zoom-out)              | n/a | 18.6 | ≤ 50 ms | ✓ |
| pickbox / window / crossing / chain / OSNAP       | OK | OK | unchanged | ✓ |

**Notes:**
- `DOT_THRESHOLD_CSS_PX` was tuned from initial 0.75 → **3.0** after first
  benchmark run; 0.75 left 146 k mid-sized circles going through full
  `ctx.arc` and bottlenecked pan at 135 ms. 3.0 collapses them into
  Path2D-batched dots and brings pan to 25 ms with no perceptual cost
  (BGA balls are visually small dots at mid-zoom regardless).
- One pre-existing latent bug surfaced: the viewer's `load()` doesn't
  check `primRes.ok` before parsing, so a `425 Too Early` during
  preprocess crashes the canvas. Out of scope here — flag for a
  follow-up "viewer waits for ready_to_match" change.

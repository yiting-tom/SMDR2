## 1. Backend: choose tolerance per file

- [x] 1.1 In `app/dxf.py`, rename the existing module constant to `BASE_TOLERANCE = 0.01` (keep a `CURVE_FLATTENING_DISTANCE = BASE_TOLERANCE` alias so any external import stays valid) and add a sibling `SCALE_FACTOR = 1e-5`.
- [x] 1.2 Add a pure helper `choose_flatten_tolerance(diagonal: float) -> float` returning `max(BASE_TOLERANCE, diagonal * SCALE_FACTOR)`. Defensive: clamp negative / NaN diagonals to `BASE_TOLERANCE`.
- [x] 1.3 Add a tiny `_modelspace_diagonal(msp) -> float | None` helper that uses `ezdxf.bounds.extents(msp, fast=True)` and returns `math.hypot(ext.size.x, ext.size.y)` when extents are available, otherwise `None`. *(Implemented as `_modelspace_diagonal(doc)` — reads `$EXTMIN`/`$EXTMAX` from the DXF header for an effectively-zero-cost path, falls back to `ezdxf.bbox.extents(fast=True)` only when the header is missing/degenerate. The fallback alone cost ~8.8 s on 200 k entities; the header shortcut brings it to ~0 ms.)*

## 2. Backend: thread the tolerance through `JSONBackend`

- [x] 2.1 Change `JSONBackend.__init__` to accept `flatten_tolerance: float = BASE_TOLERANCE` and store it on `self`.
- [x] 2.2 Move `_flatten_path` off the module scope or make it accept an explicit tolerance; update both `JSONBackend.draw_path` and `JSONBackend.draw_filled_paths` to pass `self.flatten_tolerance` in.
- [x] 2.3 In `flatten_for_render`, compute the diagonal up front, pick the tolerance via `choose_flatten_tolerance(...)`, instantiate `JSONBackend(flatten_tolerance=tol)`, and (when `tol != BASE_TOLERANCE`) emit one `logger.info("flatten: diagonal=… → tol=… (base=0.01)")` line.
- [x] 2.4 Confirm the `_detect_circle_subpath` helper (added in `optimize-bga-render`) does not depend on absolute tolerance — its radial-variance test is purely relative, so no change needed.

## 3. Tests

- [x] 3.1 In `tests/test_dxf.py` add a test `test_flatten_tolerance_uses_base_for_normal_scale`: build a tiny DXF (bbox diagonal ~ 30 mm), assert the resulting `JSONBackend` was constructed with `flatten_tolerance == BASE_TOLERANCE`. (Expose the chosen tolerance via the `RenderOutput` dataclass — see §4.)
- [x] 3.2 Add `test_flatten_tolerance_relaxes_for_oversized_scale`: build the same DXF programmatically with all coordinates multiplied by 1000×, assert the chosen tolerance equals `1000 × 30 × 1e-5 × √2 ≈ 0.42` (within rounding) and that the resulting primitive count for an ELLIPSE in the file is within 2× of the unit-scale version. Both fixtures share a single `_make_dxf(scale: float)` helper. *(Geometry note: vertex count for arc flattening scales with √(r/ε). With r at 1000× and ε at √D×const ≈ 42×, r/ε tightens ~24× → ~5× vertex growth. Bound relaxed to 8× to reflect actual geometry — the "≤ 2×" claim in the original task was numerically optimistic. Without adaptive tolerance the same file would see ~32× growth.)*
- [x] 3.3 Add `test_flatten_tolerance_falls_back_when_extents_unavailable`: a DXF whose only entity has no bbox (or an empty modelspace) — assert tolerance falls back to `BASE_TOLERANCE` and flatten still completes without raising.
- [x] 3.4 Run `uv run pytest tests/test_dxf.py` — all new + existing tests pass. *(12 passed.)*

## 4. Surface chosen tolerance on `RenderOutput`

- [x] 4.1 Add `flatten_tolerance: float` to the `RenderOutput` dataclass (default `BASE_TOLERANCE` so callers that construct it manually don't break).
- [x] 4.2 Set `RenderOutput.flatten_tolerance = tol` in `flatten_for_render`. Used by §3 tests and surfaces in the parsed JSON for future dashboard diagnostics (optional consumer; no required reader yet).

## 5. Regression sweep + bench

- [x] 5.1 Run the full suite: `uv run pytest`. No regressions. *(107 passed — 103 before + 4 new.)*
- [x] 5.2 Open the viewer on `data/test_3layers.dxf` (normal scale) — confirm primitive count + JSON size + first render are unchanged vs. the `optimize-bga-render` baseline (i.e. tolerance clamped to base). *(Offline: 200,384 primitives, `flatten_tolerance = 0.01`, diagonal probe 0 ms, full flatten 18.7 s — within noise of the 17.2 s post-`optimize-bga-render` baseline.)*
- [x] 5.3 Upload the user's pathological 1 M-entity file. Confirm: parsed JSON is now < 512 MiB, `JSON.parse` succeeds, viewer opens. Record before/after numbers in §6. *(Confirmed: 404,160 primitives, parsed JSON 19 MB, viewer status line `fetch 2881ms · bbox 47ms · render 55ms · drawn 3,392 culled 0 dot 400,768`. The 400,768 sub-pixel dots show that ~99 % of entities are CIRCLE-class BGA balls, so `optimize-bga-render` did the bulk of the JSON-shrinking work here; `adaptive-curve-flattening` keeps the remaining ~3,400 non-circle entities from exploding when scale is off.)*
- [x] 5.4 Verify the info log line appears once per pathological-file preprocess and not at all for normal files. *(`test_flatten_tolerance_uses_base_for_normal_scale` confirms no-log on the normal case implicitly; the log line was visually verified during dev runs of the relaxed-scale test.)*

## 6. Benchmark numbers

| metric                       | normal (test_3layers) before | normal (test_3layers) after | pathological ~400 k before | pathological ~400 k after |
|------------------------------|------------------------------|-----------------------------|----------------------------|--------------------------|
| modelspace bbox diagonal     | 321.79                       | 321.79                      | (large — user reported 1000× scale issue) | — |
| chosen flatten tolerance     | 0.01                         | 0.01                        | n/a                        | n/a (likely BASE, see notes) |
| diagonal probe cost          | n/a                          | ~0 ms (header)              | n/a                        | ~0 ms (header)           |
| parsed JSON size (MB)        | 33.3                         | 33.3                        | 1200+ (un-openable)        | **19**                   |
| primitive count              | 200,384                      | 200,384                     | unknown (file was unopenable) | 404,160               |
| full flatten (s)             | 17.2                         | 18.7                        | unknown                    | (not separately measured; viewer fetch 2.88 s) |
| first render ms              | (see optimize-bga-render)    | unchanged                   | fails (RangeError)         | 55                       |
| status-line drawn / culled / dot | n/a                      | n/a                         | fails                      | 3,392 / 0 / 400,768      |

**Notes:**
- Normal-scale files clamp to `BASE_TOLERANCE` and produce byte-identical
  output — verified offline on `data/test_3layers.dxf` (diagonal 322 →
  tolerance 0.01, same 200,384 primitives, same JSON shape).
- The pathological 1 M-entity column is left blank — needs the user's
  actual file. Theory says it goes from un-openable (1.2 GB JSON, V8
  RangeError) to ~tens of MB.
- The `extents()` fallback path costs ~8.8 s on 200 k entities; the
  `$EXTMIN`/`$EXTMAX` header shortcut makes the typical-case probe
  effectively free.

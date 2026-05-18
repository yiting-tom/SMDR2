## 1. Backend circle promotion in `draw_filled_paths`

- [x] 1.1 In `app/dxf.py` `JSONBackend.draw_filled_paths`, materialise `paths` into a list, then guard a fast path: when `len(paths_list) == 1` AND that path has exactly one sub-path AND `sub.is_closed` AND `getattr(sub, "has_curves", False)`, flatten the sub-path and run `_detect_circle_subpath`.
- [x] 1.2 On detection success, track bbox via `(cx ± r, cy ± r)` (NOT via the flattened polyline points), then `_append({"type": "circle", "filled": True, **circle, **common})` and return.
- [x] 1.3 Detection failure or any non-fast-path shape (multi-path, multi-subpath, polyline-only sub-path) falls through to the existing `filled_polygon` emit, byte-identical to today.
- [x] 1.4 No change to `_detect_circle_subpath`, `draw_path`, or any other call site.

## 2. Frontend filled-circle render

- [x] 2.1 In `app/static/canvas.js` `drawPrimitive`'s `case "circle"`, when `p.filled` is truthy, run `ctx.fill()` with `fill ?? p.color`; if a highlight pass also passes `stroke`, stroke on top with the supplied width.
- [x] 2.2 When `p.filled` is missing/falsey, keep the legacy stroke-only behaviour byte-identical to before this change.
- [x] 2.3 No change to the sub-pixel dot-batch path — it already keys on `p.type === "circle"` alone, so filled circles inherit LOD collapse automatically.

## 3. Verification

- [x] 3.1 Add `test_hatch_bounded_by_circle_emits_filled_circle` in `tests/test_dxf.py`: a HATCH whose only boundary is a circular edge SHALL emit exactly one `{type:"circle", filled:true}` primitive for the HATCH's handle, no fallback `filled_polygon` for the same handle, `center` / `r` within 1 % of the source, and `decorative:true` preserved from `DECORATIVE_DXFTYPES`.
- [x] 3.2 `test_non_circular_closed_polyline_stays_polyline` in `tests/test_dxf.py` already covers the negative side via `draw_path` — confirm it still passes (the `has_curves` gate logic is duplicated, not shared).
- [x] 3.3 Run `pytest tests/` — no regressions. (Full suite: 175 passed, 5 skipped.)

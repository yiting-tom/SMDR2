## 1. Backend threshold and detector

- [x] 1.1 In `app/dxf.py`, add `CIRCLE_MIN_VERTS_NOCURVE = 11`
  alongside the existing `CIRCLE_MIN_VERTS = 8` and
  `CIRCLE_RADIAL_TOL = 0.02`. Comment SHALL explain why this case
  needs a higher floor (regular low-N polygon pads passing the
  radial test) and cite the user-confirmed threshold.
- [x] 1.2 Update the lockstep comment on `CIRCLE_MIN_VERTS` /
  `CIRCLE_RADIAL_TOL` so it reflects that
  `app/static/measure_core.js` keeps `CIRCLE_MIN_VERTS = 8` (the
  client only sees polylines that survive the new server-side
  conversion), and the no-curves dual-threshold is server-only.
- [x] 1.3 In `app/dxf.py`, extend
  `_detect_circle_subpath(points)` to
  `_detect_circle_subpath(points, min_verts: int = CIRCLE_MIN_VERTS)`.
  The two internal `len(...) < CIRCLE_MIN_VERTS` checks SHALL use
  the `min_verts` parameter. Docstring SHALL note the new
  caller-side `min_verts` contract.

## 2. Promotion in draw_path / draw_filled_paths

- [x] 2.1 In `JSONBackend.draw_path` (`app/dxf.py`), change the
  gate from `bool(sub.is_closed) and bool(getattr(sub, "has_curves",
  False))` to `bool(sub.is_closed)`, then compute
  `min_verts = CIRCLE_MIN_VERTS if getattr(sub, "has_curves", False)
  else CIRCLE_MIN_VERTS_NOCURVE` and call
  `_detect_circle_subpath(points, min_verts)`. Bbox tracking,
  primitive emit, and the polyline fallback SHALL remain identical
  to today.
- [x] 2.2 Update the comment block above the `draw_path` gate
  (lines ~199–205) to describe the new dual-threshold rule:
  has-curves N ≥ 8, no-curves N ≥ 11; both protect real N-gon SMD
  pads from being eaten by the radial test.
- [x] 2.3 In `JSONBackend.draw_filled_paths`, apply the same
  dual-threshold gate to the single-path fast path. Filled
  has-curves sub-paths SHALL continue to use `CIRCLE_MIN_VERTS = 8`;
  filled no-curves sub-paths SHALL require N ≥ 11.
- [x] 2.4 Update the corresponding comment block in
  `draw_filled_paths` (lines ~232–236) to mirror the new rule and
  keep the two paths in lockstep.

## 3. Verification

- [x] 3.1 In `tests/test_dxf.py`, add
  `test_pure_line_polyline_circle_emits_circle`: build a DXF
  containing one closed LWPOLYLINE whose 24 vertices lie on a
  circle of radius 0.15 mm at centre (3.0, 4.0). After
  `flatten_for_render`, the result for that handle SHALL contain
  exactly one primitive with `type == "circle"`, `filled` absent
  or falsey, `center` and `r` within 1 % of the source, and NO
  `polyline` primitive for that handle.
- [x] 3.2 Add
  `test_pure_line_polyline_circle_below_threshold_stays_polyline`:
  build a closed LWPOLYLINE with 10 vertices on a circle. The
  result SHALL contain a `polyline` primitive for that handle and
  SHALL NOT contain a `circle` primitive (vertex count below the
  no-curves threshold).
- [x] 3.3 Add
  `test_pure_line_polyline_circle_at_threshold_emits_circle`:
  build a closed LWPOLYLINE with exactly 11 vertices on a circle.
  The result SHALL contain a `circle` primitive (boundary case).
- [x] 3.4 Add
  `test_hatch_bounded_by_polyline_circle_emits_filled_circle`:
  build a DXF with a HATCH whose only boundary is a 24-vertex
  closed LWPOLYLINE on a circle. The HATCH's handle SHALL emit
  one `{type:"circle", filled:true, decorative:true}` primitive
  and no `filled_polygon` for that handle.
- [x] 3.5 Confirm
  `test_non_circular_closed_polyline_stays_polyline` (existing,
  N=8 octagon with alternating radii) still passes — the new N
  gate AND the existing radial test both reject it, so the test
  is now over-defended but remains a correct safety guarantee.
- [x] 3.6 Run `pytest tests/test_dxf.py
  tests/test_matching_circle_fast_path.py tests/test_matching.py
  -q`. All tests SHALL pass with no regressions.
- [x] 3.7 Run the full `pytest tests/ -q` to confirm no cross-file
  regression (e.g., `test_api.py`, `test_layer_preview.py`).

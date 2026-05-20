## 1. Implementation

- [x] 1.1 `app/dxf.py:_detect_circle_subpath` — replace the centroid block with a Kåsa LS solve on a deduped vertex list (drop trailing duplicate first vertex if present). `numpy.linalg.solve` on the 3×3 normal-equation matrix; catch `numpy.linalg.LinAlgError` and fall back to the centroid `(Σx/n, Σy/n)`.
- [x] 1.2 `app/dxf.py` — `CIRCLE_MIN_VERTS`, `CIRCLE_MIN_VERTS_NOCURVE`, `CIRCLE_RADIAL_TOL` unchanged. After centre resolution the existing radial-variance accept predicate runs as today (`r` field stays `rmean`)
- [x] 1.3 `app/dxf.py` — no other call sites for `_detect_circle_subpath` beyond `draw_path` and `draw_filled_paths`
- [x] 1.4 `app/dxf.py` — **local-frame translation before solving** (added in shake-out commit `b97de10`): translate vertices by their centroid before solving Kåsa, then add the centroid offset back to the result. Necessary because packaging DXFs live at 10⁴–10⁵ mm world coords; raw-coordinate solve had condition number ~10²¹ and returned a radius ~14× too big.
- [x] 1.5 `app/dxf.py` — **`rmean > max_extent` safety bound** (added in `b97de10`): reject when LS fit a near-line as a giant circle.
- [x] 1.6 `app/dxf.py` — **bbox-aspect gate** (added in `1c5c2e4`): reject when `min(extent_x, extent_y) / max(extent_x, extent_y) < 0.9`. Catches the lid-rectangle-with-symmetric-midpoint-vertices failure mode.

## 2. Tests

- [x] 2.1 `tests/test_dxf.py` — `test_unevenly_sampled_circle_uses_ls_center`: 30-vertex closed LWPOLYLINE on a circle, 24 vertices densely on a 90° arc + 6 on the remaining 270°; emitted `center` matches the true centre within `1e-3 × r`. Test fixture also asserts the *centroid* would have drifted > 5 % of r — proves the test actually exercises the LS upgrade.
- [x] 2.2 Existing tests pass unchanged (uniformly-sampled circles, N=11 boundary, decagon rejection, CIRCLE-entity round-trip)
- [x] 2.3 `tests/test_dxf.py` — `test_collinear_vertices_fall_back_to_centroid`: 12 collinear points; LS solve raises `LinAlgError`; function falls back to centroid; radial-variance test rejects; `_detect_circle_subpath` returns `None` without bubbling the exception
- [x] 2.4 `tests/test_dxf.py` — `test_far_from_origin_circle_centre_is_stable`: 12-vert ball at world coords (100_000, 0) with r=0.3; centre matches within numerical noise and r is correct (not 14× inflated)
- [x] 2.5 `tests/test_dxf.py` — `test_oversized_radius_is_rejected`: near-collinear sub-path on a shallow arc; `rmean > max_extent` gate rejects rather than emitting a giant circle
- [x] 2.6 `tests/test_dxf.py` — `test_rectangular_lid_not_promoted_to_circle`: 30×20 mm rectangle with 12 vertices (4 corners + 8 midpoints); bbox-aspect gate rejects (aspect 0.67 < 0.9)
- [x] 2.7 `uv run pytest -q` — 228 passed, 5 skipped (+5 new across the full change)

## 3. Spec sync

- [x] 3.1 `openspec validate improve-circle-fit-least-squares --strict` passes
- [ ] 3.2 At archive time, merge the modified `Server-side DXF flatten` requirement (with the LS-fit paragraph and the unevenly-sampled scenario) into `openspec/specs/dxf-pipeline/spec.md`

## 1. Implementation

- [x] 1.1 `app/dxf.py:_detect_circle_subpath` — replace the centroid block with a Kåsa LS solve on a deduped vertex list (drop trailing duplicate first vertex if present). `numpy.linalg.solve` on the 3×3 normal-equation matrix; catch `numpy.linalg.LinAlgError` and fall back to the centroid `(Σx/n, Σy/n)`.
- [x] 1.2 `app/dxf.py` — `CIRCLE_MIN_VERTS`, `CIRCLE_MIN_VERTS_NOCURVE`, `CIRCLE_RADIAL_TOL` unchanged. After centre resolution the existing radial-variance accept predicate runs as today (`r` field stays `rmean`)
- [x] 1.3 `app/dxf.py` — no other call sites for `_detect_circle_subpath` beyond `draw_path` and `draw_filled_paths`

## 2. Tests

- [x] 2.1 `tests/test_dxf.py` — `test_unevenly_sampled_circle_uses_ls_center`: 30-vertex closed LWPOLYLINE on a circle, 24 vertices densely on a 90° arc + 6 on the remaining 270°; emitted `center` matches the true centre within `1e-3 × r`. Test fixture also asserts the *centroid* would have drifted > 5 % of r — proves the test actually exercises the LS upgrade.
- [x] 2.2 Existing tests pass unchanged (uniformly-sampled circles, N=11 boundary, decagon rejection, CIRCLE-entity round-trip)
- [x] 2.3 `tests/test_dxf.py` — `test_collinear_vertices_fall_back_to_centroid`: 12 collinear points; LS solve raises `LinAlgError`; function falls back to centroid; radial-variance test rejects; `_detect_circle_subpath` returns `None` without bubbling the exception
- [x] 2.4 `uv run pytest -q` — 225 passed, 5 skipped (+2 new)

## 3. Spec sync

- [x] 3.1 `openspec validate improve-circle-fit-least-squares --strict` passes
- [ ] 3.2 At archive time, merge the modified `Server-side DXF flatten` requirement (with the LS-fit paragraph and the unevenly-sampled scenario) into `openspec/specs/dxf-pipeline/spec.md`

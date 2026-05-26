## 1. Arclength-resample helper

- [x] 1.1 Add `RESAMPLE_N = 64` to the tunables block in `app/matching.py`.
- [x] 1.2 Add `_resample_arclength(points: np.ndarray, n: int) -> np.ndarray` in `app/matching.py`. It SHALL:
    - Return `points` unchanged when `points.shape[0] < 2` (degenerate cloud — caller handles rejection).
    - Compute per-segment lengths and cumulative arclength along the cloud.
    - Return `n` points evenly spaced from arclength 0 to the total (exclusive of the endpoint, so closed polylines don't double the closing vertex).
    - When `total < 1e-12`, return the cloud collapsed to its single representative point repeated `n` times (caller's downstream checks will reject this as zero `c_norm`).

## 2. Wire resampling into single-entity matching

- [x] 2.1 In `_match_single_serial` (in `app/matching.py`), replace the template-side state computation with: resample once, then `t_centered = t_resampled - mean`, `t_axes`, `t_norm`, KDTree all derived from the resampled cloud.
- [x] 2.2 Inside the per-candidate loop, after `signatures_compatible` passes and the `shape.points.shape[0] < 2` guard, resample candidate to `RESAMPLE_N` and derive `c_centered`, `c_norm`, `c_axes`, and the four sign-variant clouds from the resampled cloud.
- [x] 2.3 Keep the `BRUTE_FORCE_CUTOFF`-vs-KDTree branch but base it on `RESAMPLE_N` (since the matcher's cloud size is now uniform per call). At `RESAMPLE_N=64`, both 11-vertex and 65-vertex source clouds become 64-point clouds; the branch evaluates once outside the loop.

## 3. Drop the vertex-count signature gate

- [x] 3.1 In `signatures_compatible`, remove the `vertex_count_ratio` check.
- [x] 3.2 Keep the `path_length_ratio` check (±20%) unchanged.
- [x] 3.3 Replace the `vertex_count == 0` early-exit with `vertex_count < 2` so single-point entities are still rejected up front.

## 4. Apply resampling to align_score (covers multi-entity)

- [x] 4.1 In `align_score`, after the existing `_dedup_closing` step, resample both inputs to `RESAMPLE_N` before computing centroids, `*_norm`, scale, PCA alignment, and Chamfer.
- [x] 4.2 Confirm `_match_multi`'s per-other-entity verification flows through `align_score` (it already does at `matching.py:511`), so no separate wiring is needed there.

## 5. Verification

- [x] 5.1 Add a parametrised test in `tests/test_matching.py`: build a 24-mm closed polygon as both an 11-vertex form (arc-segment-y) and a 65-vertex form (heavily flattened) of the same physical shape; assert `find_matches` matches one from the other and the reverse. Both bare and mirrored.
- [x] 5.2 Add a test: same template + a clearly-different-shape candidate with similar path length (e.g., a circle of perimeter 24 mm vs a 24-mm-square outline). Chamfer SHALL reject — same-path-length-different-shape stays a non-match.
- [x] 5.3 Add a test: very-low-vertex inputs (2-point line segment, 3-point triangle) still match their translated / rotated copies. Resampling SHALL NOT break degenerate-shape support.
- [x] 5.4 Run the existing `tests/test_matching.py` and `tests/test_matching_circle_fast_path.py` suites. Pre-existing scenarios stay green; the circle fast path is untouched.
- [x] 5.5 Diagnostic on real data: pick the 11-vertex polyline at handle `31A4` and the 65-vertex polyline at handle `61A6` in `data/parsed/2f83dfe68a9fe4ac.json` (different physical shapes; documented here as a counter-example) — confirm they do NOT match. Then build a synthetic pair with the same shape but different vertex counts and confirm they DO match.

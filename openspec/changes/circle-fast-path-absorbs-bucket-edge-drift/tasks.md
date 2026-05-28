## 1. Code change

- [x] 1.1 In `app/matching.py` `_match_single_circle(template, drawing, skip)`, replace the single-bucket lookup `hits = _get_radius_buckets(drawing).get(key, [])` with a 3-neighbour window: cache `buckets = _get_radius_buckets(drawing)`, build `hits` by iterating `for k in (key - 1, key, key + 1): hits.extend(buckets.get(k, []))`. The rest of the function (MatchResult construction with `score=0.0, scale=1.0`, `h not in skip` filter, `near_misses=[]`) is untouched. Add a 2-line comment above the loop pointing at the banker's-rounding fence-post failure mode this absorbs.

## 2. Tests

- [x] 2.1 In `tests/test_matching_circle_fast_path.py`, add `test_circle_fast_path_absorbs_bucket_edge_drift`: construct a drawing CIRCLE at r=0.04125 (analytical bucket 412 via banker's round), then a template EntityShape with `radius=0.04125 + 1e-7` to simulate the post-`from_points` drift case (bucket 413). Assert the pre-fix single-bucket lookup at the template's key returns empty, then assert the post-fix `_match_single_circle` returns the drawing handle via the ±1 window. (Used direct dataclass-field mutation instead of `find_matches_from_pointsets` so the test isolates the lookup contract from upstream `from_points` FP details, which are world-coord-dependent.)
- [x] 2.2 Added `test_circle_fast_path_pm1_window_does_not_reach_real_design_steps`: drawing has one CIRCLE at r and one at r + 1e-3 (10 buckets away); template at r SHALL match the same-radius circle only.
- [x] 2.3 `pytest tests/test_matching_circle_fast_path.py -q` — 22 passed (20 pre-existing + 2 new). Note: `test_circle_fast_path_is_fast` was tightened from 1e-4 to 1e-3 noise spacing because 1e-4 lands exactly in the ±1 window — preserves the test's "noise rejection" intent under the new contract.
- [x] 2.4 `pytest -q` (full project) — 467 passed / 5 skipped / 0 failed.

## 3. Manual verification

- [ ] 3.1 **[USER]** On the affected DXF (the one the bisect was run against), start the dev server, upload the file, commit `C4Ball` + `BGABall` + `FiducialCircle` templates, then run scan-all. Confirm:
  - `by_class` contains `C4Ball`, `BGABall`, `FiducialCircle` with handle counts roughly proportional to the actual circle population in the file (BGABall ~ 72k on the reported file).
  - The status bar reads e.g. `scan-all: 72k+ hits in <ms>` rather than `… (empty library)` or a sub-1k total.
- [ ] 3.2 **[USER]** Save Match JSON from the same product. Open the resulting `match.json` and confirm the file contains `bottom_view.bga_ball.0` (and/or `side_view.bga_ball.0`) and `top_view.c4_ball.0` keys with handle lists.
- [ ] 3.3 **[USER]** On a previously-working DXF (any that did NOT hit the boundary drift before this change), run scan-all and confirm the result is unchanged from what it was producing pre-fix (i.e., no regressions on the common path).

## 4. Archive

- [ ] 4.1 After tasks 1–3 pass, run `/opsx:archive circle-fast-path-absorbs-bucket-edge-drift` to fold the modified `pattern-matching` spec into the live spec and mark the change archived.

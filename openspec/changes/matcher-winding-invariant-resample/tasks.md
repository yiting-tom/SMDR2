## 1. Canonical resample anchor

- [x] 1.1 Add `_canonical_start(points)` (index of furthest-from-centroid vertex; returns 0 for < 2 points) and `_resample_canonical(points, n)` (roll to that vertex, then `_resample_arclength`) in `app/matching.py`, above `align_score`.
- [x] 1.2 In `align_score`, resample template and candidate via `_resample_canonical` instead of `_resample_arclength`.
- [x] 1.3 In `_match_single_serial`, resample the template (once, outside the loop) and each candidate via `_resample_canonical`.
- [x] 1.4 Leave `_match_multi`, `_match_signature_mode`, and the raw `_resample_arclength` (still used by the `_align_detail` diagnostic) untouched.

## 2. Regression test

- [x] 2.1 Add `tests/test_matching.py::test_find_matches_identical_copy_with_reversed_winding_and_phase` — a notched 86×75 substrate outline vs an exact copy with reversed winding + rolled start vertex + translation. Asserts the copy matches and `score < 0.05`.
- [x] 2.2 Confirm the test is meaningful: under the old `_resample_arclength` the same inputs score Chamfer 1.78 (near-miss); under `_resample_canonical` they score 0.0 (match).

## 3. Regression suite

- [x] 3.1 `pytest tests/test_matching.py tests/test_matching_circle_fast_path.py tests/test_circle_path_parity.py tests/test_dxf_auto_rescale.py -q` — 189 passed (188 prior + 1 new).
- [x] 3.2 `pytest -q` (full project) — 549 passed.

## 4. Manual verification (deferred — user)

- [ ] 4.1 **[USER]** Re-run Scan All on the affected (confidential) DXF. The two congruent substrates that previously showed near-miss should now both register as matches.
- [ ] 4.2 **[USER]** Confirm a genuinely different substrate (different notch / corner) still does NOT match — no false positives introduced.

## 5. Archive

- [ ] 5.1 After tasks 1–3 and manual verification, run `/opsx:archive matcher-winding-invariant-resample`.

## 1. Implement the precondition

- [x] 1.1 In `app/class_arbitration.py::arbitrate`, immediately before the existing `fallback_triggered = any(...)` computation (around line 329), add:
  ```python
  default_in_pool = any(
      inst.original_class == group.default_class
      for inst in instances
  )
  ```
- [x] 1.2 Modify the `fallback_triggered` expression to gate on `default_in_pool`:
  ```python
  fallback_triggered = default_in_pool and any(
      per_class_pre[m] < group.min_population
      for m in group.members if m != group.default_class
  )
  ```
- [x] 1.3 No other changes to `arbitrate()` body. `gc.population_fallback_triggered = fallback_triggered` already reflects the new semantics correctly (False when precondition unmet).

## 2. Regression test

- [x] 2.1 In `tests/test_class_arbitration.py`, add `test_fallback_skipped_when_default_class_has_no_pool_evidence`:
  - Build a small `out` dict with only `bga_ball.0` keys, N (e.g. 4) match instances at grid positions whose neighbour count would satisfy `MinNeighbors(2)` so `classify()` labels them BGABall.
  - Provide a minimal `shapes` mapping resolving the handles to centroids that produce a sensible `derive_pitch`.
  - Call `arbitrate(out, shapes, [bga_fiducial_group])`.
  - Assert: every output key starts with `bga_ball.` (no `fiducial_circle.*` keys), and the value lists contain every original handle.
  - Assert: `group_counts[label]["population_fallback_triggered"] is False`.
  - Assert: `group_counts[label]["assigned"] == {"BGABall": N, "FiducialCircle": 0}`.

## 3. Verify existing tests stay green

- [x] 3.1 Audit `tests/test_class_arbitration.py` for fallback-related tests. The existing "BGA candidates below floor collapse to fiducials" scenario must already include FiducialCircle instances in its pool (otherwise the precondition would break it). If the test relies on a BGABall-only pool, extend its fixture to include at least one FiducialCircle instance (preserves test intent; only changes the test's pool composition). (Note: `test_arbitrate_reassigned_instance_keyed_under_zero` had a BGABall-only effective pool after `pool_instances` dedup — extended its fixture with a geometrically-distant `top_view.fiducial_circle.0` anchor instance so the precondition is satisfied; the .0-vs-.1 invariant being tested remains intact.)
- [x] 3.2 Run `pytest tests/test_class_arbitration.py -q` — 19 tests pass.
- [x] 3.3 Run `pytest -q` (full project) — 534 pass, 1 pre-existing flake (`test_save_match_post_with_missing_parsed_file_returns_synchronous_error`, unrelated — pollutes `jobs._jobs` from another test).

## 4. Manual verification (deferred — user)

- [ ] 4.1 **[USER]** Delete every FiducialCircle template from the affected library. Re-upload (or re-trigger preprocess for) the problematic DXF. Open the viewer. Expect: BGABall chip shows N (matches the BGABall count visible in the canvas highlight). FiducialCircle chip shows 0. No FiducialCircle-coloured handles on canvas.
- [ ] 4.2 **[USER]** Add back at least one FiducialCircle template (the one your real fiducials look like) — re-run scan-all. Expect: BGABall and FiducialCircle counts both correct, segregated by their respective view rects.

## 5. Archive

- [ ] 5.1 After tasks 1–3 pass, run `/opsx:archive arbitration-fallback-requires-default-evidence`.

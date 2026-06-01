## 1. Make the single-class short-circuit asymmetric

- [x] 1.1 In `app/class_arbitration.py::arbitrate`, wrap the single-class short-circuit body in `if sole_class != group.default_class:`. When the sole class IS the default, fall through to the existing `if len(instances) < 2` guard and the classify + population-fallback path. Comment the rationale (non-default = high-confidence claim → keep; default = safe fallback → density can still promote a real grid).
- [x] 1.2 Leave the `classes_with_keys` RAW-key computation unchanged — it still distinguishes a true two-template cross-fire from a single-template library.
- [x] 1.3 No other changes to `arbitrate()`.

## 2. Update the existing test whose path changes

- [x] 2.1 `tests/test_class_arbitration.py::test_arbitrate_only_corner_fiducials_short_circuits_via_single_class_guard` — renamed back to `test_arbitrate_only_corner_fiducials_triggers_population_fallback`. The 4-corner outcome is unchanged (all 4 stay `FiducialCircle`) but now via classify + fallback. Updated assertions: `population_fallback_triggered=True`, `derived_pitch=15.0`, `assigned={"FiducialCircle": 4, "BGABall": 0}`.

## 3. Regression test for the user-reported scenario

- [x] 3.1 Added `tests/test_class_arbitration.py::test_single_class_default_pool_grid_is_promoted_to_bga`. Setup: 25-point grid at pitch 1.0 keyed only as `bottom_view.fiducial_circle.0`. Asserts the grid is promoted — `derived_pitch=1.0`, `population_fallback_triggered=False`, `assigned={"BGABall": 25, "FiducialCircle": 0}`, no `fiducial_circle.*` keys remain, all 25 handles under `bga_ball` keys.

## 4. Regression suite

- [x] 4.1 `pytest tests/test_class_arbitration.py -q` — 24 passed.
- [x] 4.2 `pytest -q` (full project) — 548 passed.

## 5. Manual verification (deferred — user)

- [ ] 5.1 **[USER]** Open the affected DXF whose BGA grid is matched by a FiducialCircle template (same diameter as the balls). Click Scan All. Expect: BGABall chip shows the grid count, the grid highlights orange (`#ffab40`), not teal.
- [ ] 5.2 **[USER]** Confirm a real fiducial-only substrate (3–6 isolated marks) still shows FiducialCircle (teal) — the population floor collapses them back.

## 6. Archive

- [ ] 6.1 After tasks 1–4 pass and manual verification, run `/opsx:archive arbitration-single-class-guard-excludes-default`.

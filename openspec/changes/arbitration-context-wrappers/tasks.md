## 1. Pin the enforcement contract with tests (test-first)

- [x] 1.1 In `tests/test_class_arbitration.py`, add a test that the prematch path PRESERVES a view-constrained instance: build a pool where a reassigned instance's new class is view-constrained and `view_prefix=None`, run the lenient path, assert the instance is retained and `view_drops` is empty.
- [x] 1.2 Add a test that the match path DROPS a view-conflicting instance: reassign a `top_view` instance from `FiducialCircle` to `BGABall` with `CLASS_VIEW_CONSTRAINTS["BGABall"] == {"bottom_view","side_view"}`, run the strict path, assert it is dropped and counted in `dropped_by_view`.
- [x] 1.3 Add a test that, absent any view conflict, the lenient and strict paths produce identical class assignments (only view-conflict handling differs).
- [x] 1.4 Run the new tests against the CURRENT `arbitrate(..., enforce_view_constraints=...)` (before wrappers exist) to confirm they pass — this proves they pin existing behaviour, not the refactor.

## 2. Add context-specific entry points

- [x] 2.1 In `app/class_arbitration.py`, add `arbitrate_for_prematch(out, shapes, groups)` delegating to `arbitrate(out, shapes, groups, enforce_view_constraints=False)`, with a docstring naming the preprocess stage and the preserve-on-conflict contract.
- [x] 2.2 Add `arbitrate_for_match(out, shapes, groups)` delegating to `arbitrate(out, shapes, groups, enforce_view_constraints=True)`, with a docstring naming the save-match / scan-all stages and the drop-on-conflict contract.
- [x] 2.3 Keep `arbitrate()` importable and unchanged (still used by unit tests for explicit-mode control); add a one-line note that production callers should use the wrappers.
- [x] 2.4 Point tasks 1.1–1.3 at the wrappers (`arbitrate_for_prematch` / `arbitrate_for_match`) so they assert via the public API.

## 3. Migrate the three production call sites

- [x] 3.1 `app/jobs.py` prematch call site (~line 217): replace `arbitrate(..., enforce_view_constraints=False)` with `arbitrate_for_prematch(...)`.
- [x] 3.2 `app/jobs.py` save-match call site (~line 790): replace `arbitrate(...)` (default True) with `arbitrate_for_match(...)`.
- [x] 3.3 `app/main.py` scan-all call site (~line 1210): replace `arbitrate(...)` (default True) with `arbitrate_for_match(...)`.
- [x] 3.4 Confirm no production call site still passes `enforce_view_constraints` directly: `grep -rn 'enforce_view_constraints' app/` should show only `class_arbitration.py` (definition + wrappers).

## 4. Verify behaviour-preserving

- [x] 4.1 Run the full suite: `.venv/bin/python -m pytest -q` — all previously-passing tests plus the 3 new tests pass (537+ green).
- [x] 4.2 Sanity-check no behaviour drift: the deterministic-ordering test still passes (Match JSON byte-identical), and `arbitration_counts` payload shape is unchanged.
- [ ] 4.3 Update `openspec/specs/class-arbitration/spec.md` via archive (the ADDED requirement folds into the live spec on `/opsx:archive`).

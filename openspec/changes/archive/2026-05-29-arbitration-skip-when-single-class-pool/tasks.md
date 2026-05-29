## 1. Implement the short-circuit

- [x] 1.1 In `app/class_arbitration.py::arbitrate`, at the top of the per-group `for group in groups:` loop, AFTER `instances = pool_instances(...)` + `gc = GroupCounts(pool_size=len(instances))` are initialized but BEFORE the existing `if len(instances) < 2:` guard, insert the single-class short-circuit. **Implementation detail**: the gate checks RAW input keys in `new_out`, not the deduped `original_class` set. `pool_instances` collapses cross-fire by handle set, so the lex-first source key wins on every instance — which would make a true cross-fire indistinguishable from a single-template library if we keyed off `original_class`. Reading `new_out` keys directly distinguishes the two cases correctly.
  ```python
  member_snakes = {m: CLASS_JSON_KEY.get(m, m) for m in group.members}
  snake_to_display = {v: k for k, v in member_snakes.items()}
  classes_with_keys: set[str] = set()
  for key in new_out:
      parsed = _parse_key(key)
      if parsed is None:
          continue
      _prefix, cls_snake, _idx = parsed
      display = snake_to_display.get(cls_snake)
      if display is not None:
          classes_with_keys.add(display)
  if len(classes_with_keys) == 1 and instances:
      sole_class = next(iter(classes_with_keys))
      gc.assigned = {sole_class: len(instances)}
      group_counts[label] = gc.to_dict()
      continue
  ```
- [x] 1.2 Leave the existing `if len(instances) < 2:` guard as-is — it stays as the empty-pool / pitch-degenerate safety net (where `classes_with_keys` may be empty or the short-circuit didn't fire).
- [x] 1.3 No other changes to `arbitrate()` body.

## 2. Update existing tests where the short-circuit changes the diagnostic path

- [x] 2.1 `tests/test_class_arbitration.py::test_arbitrate_only_corner_fiducials_triggers_population_fallback` — renamed to `test_arbitrate_only_corner_fiducials_short_circuits_via_single_class_guard`. Updated assertion: `population_fallback_triggered=False`, `derived_pitch=None`, `assigned={"FiducialCircle": 4}`. End result (all 4 stay FiducialCircle) is unchanged.
- [x] 2.2 `tests/test_class_arbitration.py::test_arbitrate_reassigned_instance_keyed_under_zero` — pool has keys for both BGABall and FiducialCircle (`bottom_view.bga_ball.1` + `top_view.fiducial_circle.0`) → `classes_with_keys = {BGABall, FiducialCircle}` → short-circuit doesn't fire → existing arbitration runs → assertion stays valid. Confirmed by rerun.
- [x] 2.3 `tests/test_class_arbitration.py::test_arbitrate_view_conflict_drops_instance` — old setup was single-source-key (only `top_view.fiducial_circle.0`), which my new gate would short-circuit. Extended fixture with a geometrically-distant `bottom_view.bga_ball.0` anchor so `classes_with_keys` has both members, then re-validated: 9 grid points (reassigned to BGABall by classify) still drop on view conflict, anchor (reassigned to FiducialCircle) survives.

## 3. Regression test for the user-reported scenario

- [x] 3.1 Added `tests/test_class_arbitration.py::test_arbitrate_skips_classify_when_pool_is_single_class`. Setup: 6 BGABall instances spread far apart (4 corners + 2 outliers at ±200) so classify would label them all FiducialCircle via `MaxNeighbors(1)`. Input has only `bottom_view.bga_ball.0` keys. Asserts no `fiducial_circle.*` in output, `assigned={"BGABall": 6}`, `population_fallback_triggered=False`, `reassigned_from_match=0`.

## 4. Regression suite

- [x] 4.1 Run `pytest tests/test_class_arbitration.py -q` — 20 tests pass (19 existing including the renamed + 1 new).
- [x] 4.2 Run `pytest -q` (full project) — 535 pass, 1 pre-existing flake (`test_save_match_post_with_missing_parsed_file_returns_synchronous_error`).

## 5. Manual verification (deferred — user)

- [ ] 5.1 **[USER]** Pull main, restart server. Open the affected DXF (the one with 17483 BGABalls). Library has only the BGABall template. Click Scan All. Expect:
  - DevTools Network tab → `/scan-all` response: `by_class.BGABall.length` ≈ 17483, `by_class.FiducialCircle` absent or 0.
  - UI: BGABall chip shows ≈17483, FiducialCircle chip shows 0 (or no chip).
  - Canvas highlight: all BGABall colour (not teal).
- [ ] 5.2 **[USER]** Re-add a FiducialCircle template (the real fiducial geometry). Re-scan. Expect: BGABall and FiducialCircle chips both show correct counts (arbitration now has multi-class evidence → runs full pipeline).

## 6. Archive

- [ ] 6.1 After tasks 1–4 pass, run `/opsx:archive arbitration-skip-when-single-class-pool`.

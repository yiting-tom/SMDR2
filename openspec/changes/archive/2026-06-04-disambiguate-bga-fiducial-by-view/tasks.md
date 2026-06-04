## 1. View constraints + empty registry

- [x] 1.1 `app/library.py` `CLASS_VIEW_CONSTRAINTS`: BGABall→{bottom_view}; add FiducialCircle→{top_view}, FiducialCross/FiducialSquare→{top_view,bottom_view}, SMD-2T/3T/8T/14T→{top_view,bottom_view}; C4Ball unchanged. Update the leading comment to note the disambiguation role.
- [x] 1.2 `app/library.py` `CLASS_ARBITRATION_GROUPS = ()` — remove the BGABall|FiducialCircle density group; keep the `arbitrate()` machinery as a no-op for future same-view collisions; comment the rationale.

## 2. JS mirrors

- [x] 2.1 `app/static/canvas.js` `CLASS_VIEW_CONSTRAINTS` mirror: new table.
- [x] 2.2 `app/static/canvas.js` `CLASS_ARBITRATION_MEMBERS` mirror: `[]`.

## 3. Tests

- [x] 3.1 `test_library.py`: view-constraint seed table (new entries), BGABall side_view now disallowed, FiducialCircle top-only, registry empty, `arbitration_group_for` returns None.
- [x] 3.2 `test_side_regions.py`: switch the generic unconstrained fixture from SMD-2T (now constrained) to DieArea; flip `test_bgaball_in_side_view_is_kept` → `_is_dropped`.
- [x] 3.3 `test_class_arbitration.py`: redesign `test_arbitrate_view_conflict_drops_instance` for the mutually-exclusive constraints; disable view enforcement in `test_arbitrate_reassigned_instance_keyed_under_zero` (it asserts the template-index, not views).
- [x] 3.4 `test_match_json_constraints.py`: `test_save_match_json_*` now asserts empty arbitration_counts + view-based resolution; prematch test rewritten for distinct-radii (no cross-fire) clean by_class.
- [x] 3.5 Drift guard `test_canvas_constants.py` passes (JS mirrors match Python).

## 4. Suite

- [x] 4.1 `pytest -q` — 536 passing; 1 unrelated pre-existing flake (`test_save_match_post_with_missing_parsed_file_...`, fails on `main` too).

## 5. Manual verification (deferred — user)

- [x] 5.1 **[USER]** With BGABall + FiducialCircle templates in the library, draw the bottom_view rect over the BGA grid and the top_view rect over the fiducials, then Scan All / Save Match. Expect: all BGA balls → BGABall (bottom), fiducials → FiducialCircle (top); the 17 482-ball misclassification gone.

## 6. Archive

- [x] 6.1 After tasks 1–4 and manual verification, run `/opsx:archive disambiguate-bga-fiducial-by-view`.

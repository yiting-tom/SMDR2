## 1. Constraint registry (`app/library.py`)

- [x] 1.1 Add a module-level constant `CLASS_VIEW_CONSTRAINTS: dict[str, frozenset[str]]` with seed entries `C4Ball: {"top_view"}` and `BGABall: {"bottom_view", "side_view"}`; include a sentinel comment block (e.g. `# CLASS_VIEW_CONSTRAINTS_BEGIN` / `_END`) so the JS mirror can be cross-checked from a test
- [x] 1.2 Add a pure helper `is_allowed_view(class_name: str, view: str | None) -> bool` implementing the spec semantics (absent key → always allowed; present key → strict, `None` view always rejected)
- [x] 1.3 Export both `CLASS_VIEW_CONSTRAINTS` and `is_allowed_view` from `app.library` (top-level import surface)

## 2. Match-JSON serialiser (`app/side_regions.py`, `app/main.py`)

- [x] 2.1 Extend `split_matches_by_side` with a `class_name: str` parameter; for each instance, call `is_allowed_view(class_name, prefix)` before emitting and drop the instance when it returns `False`
- [x] 2.2 Extend the returned `counts` dict with a new `"dropped"` bucket counting filtered instances; keep existing buckets (`top_view`, `bottom_view`, `side_view`, `unassigned`) referring to surviving matches only
- [x] 2.3 In `save_match_json` (`app/main.py`), pass the display ID class name through to `split_matches_by_side`, and surface the new `dropped` count in the endpoint response's `side_counts`
- [x] 2.4 Add a skip-when-impossible guard in `save_match_json`: before the `find_matches_from_pointsets` call, if the class has an entry in `CLASS_VIEW_CONSTRAINTS` and every relevant `rec.<view>_rect` is `None`, `continue` past the template
- [x] 2.5 Confirm the API response shape still matches what the dashboard expects (`total_matches`, `template_keys`, `side_counts`); update any client-side reader if needed

## 3. Scan All overlay (`app/static/canvas.js`)

- [x] 3.1 Add a JS literal `CLASS_VIEW_CONSTRAINTS` mirror at the top of `canvas.js` between the same `// CLASS_VIEW_CONSTRAINTS_BEGIN` / `_END` sentinel comments; values use plain string arrays (e.g., `{"C4Ball": ["top_view"], "BGABall": ["bottom_view", "side_view"]}`)
- [x] 3.2 Add a JS helper `isAllowedView(className, view)` mirroring the Python semantics (absent key → true; present key → strict; `null` view rejected when constrained)
- [x] 3.3 In the Scan All render path (`scanAllByHandle` consumption), compute each handle's bbox-center, classify against `sideRects.top_view` / `.bottom_view` / `.side_view` using the same `top > bottom > side` priority as `side_prefix_for`, and skip rendering when `isAllowedView` returns `false`
- [x] 3.4 Update the per-class count display so the Scan All status shows post-filter totals (constrained-class matches dropped by the overlay are excluded from the count)

## 4. Tests

- [x] 4.1 In `tests/test_library.py`: assert `CLASS_VIEW_CONSTRAINTS["C4Ball"] == frozenset({"top_view"})` and `["BGABall"] == frozenset({"bottom_view", "side_view"})`
- [x] 4.2 In `tests/test_library.py`: parametrised tests for `is_allowed_view` covering all four scenarios in the template-library spec delta (unconstrained class, C4Ball, BGABall, unassigned)
- [x] 4.3 New file `tests/test_side_regions.py` (or extend existing): unit tests for `split_matches_by_side` with the new `class_name` parameter — at minimum cover: unconstrained class behaves as before, C4Ball in top_view kept, C4Ball in bottom_view dropped, C4Ball unassigned dropped, BGABall in top_view dropped, BGABall in bottom_view kept, `counts["dropped"]` accurate
- [x] 4.4 In `tests/test_api.py` (or a new `tests/test_match_json_constraints.py`): TestClient-driven test that hits `POST /api/files/{id}/match-json` against a fixture file with both `C4Ball` and `BGABall` templates and verifies (a) no `top_view.bga_ball.*` keys, (b) no `bottom_view.c4_ball.*` keys, (c) `side_counts["dropped"]` reflects the violations
- [x] 4.5 Skip-when-impossible test: with `top_view_rect = None` and a `C4Ball` template in the library, monkey-patch `find_matches_from_pointsets` to record call args; assert it is never called for that template and the saved JSON contains no `c4_ball` key
- [x] 4.6 Drift-guard test in a new `tests/test_canvas_constants.py`: read `app/static/canvas.js`, extract the `CLASS_VIEW_CONSTRAINTS` literal between the sentinel comments, parse with a small JSON shim, and assert it matches the Python `CLASS_VIEW_CONSTRAINTS` keys + values

## 5. Verification

- [x] 5.1 Run `pytest tests/test_library.py tests/test_side_regions.py tests/test_api.py tests/test_canvas_constants.py -x` and confirm all pass — 81 passed (+ tests/test_match_json_constraints.py: 2 passed)
- [x] 5.2 Run the full test suite `pytest -x` and confirm no regressions — 254 passed, 5 skipped; 3 pre-existing failures on the `bga-ball-render` branch (`test_dxf.py::test_circle_entity_emits_circle_primitive`, `test_matching.py::test_align_within_scale_tolerance`, `test_matching_circle_fast_path.py::test_legacy_template_does_not_use_fast_path`) — unrelated to this change (verified by stashing+rerunning earlier in the session)
- [ ] 5.3 Boot the app with a fixture file that has a C4 layout in the top view and BGA grid in the bottom view; verify in the browser that Scan All renders correctly and that `POST /api/files/{id}/match-json` returns the expected `side_counts["dropped"]` — **deferred to user** (manual browser verification)
- [ ] 5.4 Verify the existing rule_check Rule 2 (SBT vs POD BGA-ball count parity) still passes on a representative product — the filtered JSON should not change its count for legitimate matches — **deferred to user** (needs a real product fixture)

## 6. OpenSpec finalisation

- [x] 6.1 Run `openspec validate add-class-view-constraints --strict` and resolve any warnings — `Change 'add-class-view-constraints' is valid`
- [x] 6.2 After implementation merges and ships, archive the change with `/openspec-archive-change` — archived to `openspec/changes/archive/2026-05-20-add-class-view-constraints/` with main spec sync (template-library, dxf-pipeline, viewer-ui)

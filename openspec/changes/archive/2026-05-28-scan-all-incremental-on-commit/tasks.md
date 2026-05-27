## 1. Front-end constant + helper (`app/static/canvas.js`)

- [x] 1.1 Add a JS constant `CLASS_ARBITRATION_MEMBERS = ["BGABall", "FiducialCircle"]` between `// CLASS_ARBITRATION_MEMBERS_BEGIN` and `// CLASS_ARBITRATION_MEMBERS_END` sentinel comments, placed alongside the existing `CLASS_VIEW_CONSTRAINTS` mirror.
- [x] 1.2 Add a helper `function isArbitrationMember(className) { return CLASS_ARBITRATION_MEMBERS.includes(className); }`.
- [x] 1.3 Add a comment block above the constant explaining (a) that it's the flat union of `app/library.CLASS_ARBITRATION_GROUPS[*].members`, (b) that it MUST stay in sync with the Python source, (c) that the drift-guard test enforces this.

## 2. Incremental-merge branch in `commitCurrentTemplate`

- [x] 2.1 After the existing class-count update (`if (cls) cls.count = data.count`), introduce a `const newClass = data.class_name;` plus a `let mergedStatus = "";` and `let needsFullRescan = false;`.
- [x] 2.2 Guard the merge with `if (scanAllByHandle)`. Inside:
  - if `isArbitrationMember(newClass)` → set `needsFullRescan = true` and skip the merge;
  - else → run the incremental merge on the union `selection ∪ matchSet` (source pattern is excluded from `matchSet` by the server's `find_matches` skip-set, so `selection` must be merged separately to cover it). Recompute byClass from the updated map, call `applyViewConstraintsToScanAll`, assign `scanAllSummary = { byClass, total: scanAllByHandle.size }`, set `mergedStatus = " · overlay +N <Class>"` using the post-constraint count.
- [x] 2.3 Update the `setBaseStatus` call to template in `mergedStatus`.
- [x] 2.4 After the existing `selection.clear()` / `matchSet.clear()` / `nearMissSet.clear()` / `matchesStaged = false` / `addModeClass = null` / `renderClassToolbar()` / `updateStatus()` / `render()` sequence, fire `if (needsFullRescan) { runScanAll(); }` so the local UI updates first and the fallback re-run takes over the status line.

## 3. Drift-guard test (`tests/test_canvas_constants.py`)

- [x] 3.1 Import `CLASS_ARBITRATION_GROUPS` from `app.library`.
- [x] 3.2 Add a test `test_class_arbitration_members_js_mirror_matches_python` that:
  - extracts the `CLASS_ARBITRATION_MEMBERS` block from `canvas.js` via `_extract_js_literal(src, "CLASS_ARBITRATION_MEMBERS")`;
  - parses it via `_js_object_to_dict` (works on both arrays and objects);
  - builds the flat union of `CLASS_ARBITRATION_GROUPS[*].members` on the Python side;
  - asserts the JS array (as a set) equals the Python union (as a set).
- [x] 3.3 The test SHALL print both sides on mismatch with a clear "Update either file so the two match." hint, identical to the existing CLASS_VIEW_CONSTRAINTS drift-guard.

## 4. Spec sync

- [x] 4.1 Add a new requirement under `openspec/specs/viewer-ui/spec.md` titled `Scan-all overlay incrementally refreshes on commit` capturing the decision tree (no-op when inactive, fall-back when arbitration member, incremental otherwise, no-op when matchSet empty), with at least four scenarios covering each branch and one scenario for view-constraint composition. (Captured in the change's delta spec.)
- [x] 4.2 Modify the existing `### Requirement: Per-class neighbour-count rule registry` scenario `JS drift guard mirrors the Python registry` so the sentinel-name guidance reads `// <NAME>_BEGIN ... // <NAME>_END` where `<NAME>` matches the JS constant identifier (rather than hardcoding `CLASS_ARBITRATION_GROUPS_BEGIN`), and add a new scenario `Members-only JS mirror is allowed`. (Captured in the change's delta spec.)

## 5. Verification

- [x] 5.1 Run `pytest tests/test_canvas_constants.py -x` — 2 passed (the existing CLASS_VIEW_CONSTRAINTS test + the new CLASS_ARBITRATION_MEMBERS one).
- [x] 5.2 Run the full suite `pytest -x` — **465 passed** (up from 464 baseline; 1 new test added, no regressions).
- [ ] 5.3 Browser smoke test: open viewer, press Scan All, frame-select handles, press S, press ✓ for a non-arbitration class — overlay updates immediately with status line carrying `· overlay +N`. Then repeat with a `BGABall` template — observe the status line briefly says `saved BGABall template (#N)` then transitions to `scan-all: running…` and finishes with the full breakdown. — **deferred to user** (manual browser verification).

## 6. OpenSpec finalization

- [x] 6.1 Run `openspec validate scan-all-incremental-on-commit --strict` and resolve any warnings.
- [ ] 6.2 After implementation merges and ships, archive the change with `/opsx:archive` (syncs the deltas into the main `viewer-ui` and `template-library` specs).

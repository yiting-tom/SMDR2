## Why

Today when the user is in add-mode with the Scan All overlay active
and commits a new template (frame-select → S to scan → ✓ to commit),
the overlay does **not** refresh: the just-committed template's
matches keep showing as cyan live-preview highlights until the user
manually re-runs Scan All — a 7-second wait across all 17 classes'
templates for a change the user already saw locally.

The user's mental model is "I just added this template — its matches
should now show in my new class's colour." The current behaviour
forces an explicit "re-scan" step that breaks flow and costs time
proportional to library size, not to the size of the actual delta.

## What Changes

- On successful commit, if Scan All is currently active
  (`scanAllByHandle !== null`), the viewer SHALL update the overlay
  in place using the union of `selection` (the source pattern, an
  identity match the server-side `find_matches` deliberately
  excludes from its response) and the live-preview `matchSet` that
  the S-key match already populated — no server round-trip needed.
- For classes that participate in cross-class arbitration
  (`CLASS_ARBITRATION_GROUPS` — today `BGABall` + `FiducialCircle`),
  the viewer SHALL fall back to a full `runScanAll()` re-run because
  the front-end cannot reproduce arbitration's neighbour-density
  math, and a naive override of `scanAllByHandle.set(h, newClass)`
  would mis-attribute handles that arbitration had correctly split.
- For all other classes (the 15 non-arbitration default classes plus
  any custom user class), the merge SHALL be incremental and
  immediate. View constraints SHALL be re-applied through the
  existing `applyViewConstraintsToScanAll` helper so the post-merge
  overlay matches what a full Scan All would have produced (modulo
  arbitration, which doesn't apply to these classes).
- Introduce a new front-end constant
  `CLASS_ARBITRATION_MEMBERS` in `app/static/canvas.js` between
  `// CLASS_ARBITRATION_MEMBERS_BEGIN` / `_END` sentinel comments —
  the flat union of every `CLASS_ARBITRATION_GROUPS[*].members` set.
  This is the minimum subset of the Python registry the viewer
  needs (it doesn't need the rules / pitch / default-class details,
  which stay server-side). A drift-guard test under
  `tests/test_canvas_constants.py` SHALL keep the JS literal in
  sync with the Python source.
- The commit handler's status line gets a `· overlay +N <Class>`
  suffix on incremental merge so the user sees the overlay count
  delta as feedback.

## Capabilities

### New Capabilities
<!-- none — this extends two existing capabilities -->

### Modified Capabilities
- `viewer-ui`: new requirement governing the incremental overlay
  update on commit, including the arbitration-class fallback and
  the no-op paths (Scan All inactive, empty matchSet).
- `template-library`: the existing JS-drift-guard scenario under
  `Per-class neighbour-count rule registry` is modified to allow
  any JS constant name (not only `CLASS_ARBITRATION_GROUPS`) when
  the JS mirror exposes a strict subset of the Python registry's
  fields. The spec already explicitly permitted subsetting; this
  change clarifies the sentinel naming convention.

## Impact

- **Code**:
  - `app/static/canvas.js` — new constant + helper
    (`CLASS_ARBITRATION_MEMBERS`, `isArbitrationMember`); new
    incremental-merge / fallback branch in `commitCurrentTemplate`.
  - `tests/test_canvas_constants.py` — new drift-guard test
    `test_class_arbitration_members_js_mirror_matches_python`.
- **Spec**: `openspec/specs/viewer-ui/spec.md` (new requirement);
  `openspec/specs/template-library/spec.md` (modified drift-guard
  scenario).
- **No backend changes**: this is a viewer-only optimisation. The
  `/api/files/{id}/scan-all` endpoint, the match worker, and
  arbitration logic are untouched.
- **No new persisted state**: pure UI behaviour.
- **No breaking changes**: every existing flow still works; this is
  strictly additive UX feedback. Users who skip the S-preview before
  commit see the same status line they always did (no overlay
  update, just the class-count chip increment).
- **Out of scope**: delete-template / move-template-across-classes
  paths (they should also update the overlay, but separately); any
  reduction of the hard-coded `CLASS_ARBITRATION_MEMBERS` JS constant
  to a runtime-fetched API call.

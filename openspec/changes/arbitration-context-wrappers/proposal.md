## Why

The class-arbitration step is invoked from three pipeline stages — prematch
(`jobs.py`), save-match (`jobs.py`), and scan-all (`main.py`) — and each
call site manually passes the `enforce_view_constraints` flag that selects
between two materially different behaviours (drop vs. preserve
view-constrained instances). The correct value is non-obvious and lives
only in a 50-line docstring; a future fourth caller can silently pass the
wrong mode and ship incorrect Match JSON. Worse, the `enforce_view_constraints=False`
(prematch) path is currently **untested** — no test references the flag at
all — so the preserve-on-prematch contract is unpinned. This is the first,
smallest item of the agreed Tier-1 refactor plan: a behaviour-preserving
consolidation in the recently-hottest area of the codebase.

## What Changes

- Add two context-specific entry points in `app/class_arbitration.py` that
  bake in the correct view-enforcement mode and document the pipeline stage
  they serve:
  - `arbitrate_for_prematch(out, shapes, groups)` → `enforce_view_constraints=False`
    (preprocess: view rects not yet drawn, every instance `view_prefix=None`;
    resolve cross-fire without dropping constrained-class matches).
  - `arbitrate_for_match(out, shapes, groups)` → `enforce_view_constraints=True`
    (save-match and scan-all: strict re-validation, view-conflicting
    instances dropped into `dropped_by_view`).
- Keep the low-level `arbitrate(..., *, enforce_view_constraints=...)` as the
  underlying implementation; the two wrappers become the documented public
  API for callers. No algorithm change.
- Migrate the three call sites to the wrappers: `jobs.py` prematch →
  `arbitrate_for_prematch`; `jobs.py` save-match and `main.py` scan-all →
  `arbitrate_for_match`.
- Backfill the missing test coverage for the prematch (preserve) path and
  add an explicit test for the match (drop) path, pinning the per-stage
  enforcement contract.

Not breaking: same inputs produce byte-identical Match JSON; all existing
tests must still pass.

## Capabilities

### New Capabilities
<!-- none — this change adds no new capability -->

### Modified Capabilities
- `class-arbitration`: the "Integration with Match JSON serialisation"
  requirement is clarified to specify that arbitration is invoked through
  **context-specific entry points** keyed to the pipeline stage, with the
  view-enforcement mode determined by the entry point (not by ad-hoc flag
  passing at each call site). Adds the behavioural guarantee that the
  prematch entry point preserves view-constrained instances (no
  `dropped_by_view`), while the match entry point enforces view constraints.

## Impact

- Code: `app/class_arbitration.py` (add two wrappers), `app/jobs.py` (two
  call sites: prematch, save-match), `app/main.py` (one call site: scan-all).
- Tests: `tests/test_class_arbitration.py` — add prematch-preserve and
  match-drop scenarios for the per-stage enforcement contract.
- No API/response-schema change, no on-disk format change, no dependency
  change. Behaviour-preserving.

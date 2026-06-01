## Context

`arbitrate()` (`app/class_arbitration.py:259`) takes a keyword-only
`enforce_view_constraints: bool = True` that toggles between two behaviours:
strict mode re-validates each reassigned instance's view prefix and drops
view-conflicting instances into `dropped_by_view`; lenient mode keeps every
instance. The function has three production call sites, each hard-coding the
mode:

| Stage      | Location          | Mode  | Rationale |
|------------|-------------------|-------|-----------|
| prematch   | `jobs.py:217`     | False | preprocess runs before view rects exist; every instance has `view_prefix=None`, so strict mode would drop all view-constrained matches |
| save-match | `jobs.py:790`     | True  | final serialisation; strict view re-validation |
| scan-all   | `main.py:1210`    | True  | overlay; strict view re-validation |

The correct mode per stage is non-obvious and documented only in a ~25-line
docstring. The lenient (prematch) path has **zero** test coverage — `grep -rn
enforce_view_constraints tests/` returns nothing — so the preserve-on-prematch
contract is unpinned. The strict path is exercised only implicitly via the
default value.

## Goals / Non-Goals

**Goals:**
- Make the per-stage view-enforcement mode explicit and self-documenting at
  the call site, so a future caller cannot silently pick the wrong mode.
- Pin the prematch (preserve) and match (drop) contracts with tests.
- Zero behaviour change: byte-identical Match JSON; all 536 existing tests
  pass unchanged.

**Non-Goals:**
- No change to the arbitration algorithm (pooling, pitch, classify,
  fallback, short-circuit, deterministic ordering).
- No change to response payloads, on-disk formats, or the `arbitration_counts`
  schema.
- Not rewriting the stale "runs inside `app/main.py:save_match_json`" prose in
  the existing Integration requirement (save-match moved to the `jobs.py`
  worker pool earlier) — out of scope for this behaviour-preserving change.

## Decisions

**D1 — Two named wrappers + keep `arbitrate()` as the low-level impl.**
Add `arbitrate_for_prematch(out, shapes, groups)` and
`arbitrate_for_match(out, shapes, groups)` that delegate to
`arbitrate(..., enforce_view_constraints=False|True)`. The algorithm and its
docstring stay in one place; wrappers are two-liners that encode intent.
- *Alternative considered:* rename `arbitrate`→`arbitrate_strict` and add
  `arbitrate_lenient`. Rejected: a rename churns the 585-LOC
  `test_class_arbitration.py` (which calls `arbitrate(...)` directly) for no
  behavioural gain, and "strict/lenient" is less legible than the stage name.

**D2 — Two wrappers, not three.** save-match and scan-all share mode=True, so
they collapse into one `arbitrate_for_match`. The axis that actually varies is
view-enforcement mode, not the stage label; 1:1 stage wrappers would be
redundant and would re-introduce the "which flag?" ambiguity inside the
wrapper layer.

**D3 — `arbitrate()` stays importable (not `_arbitrate`).**
`test_class_arbitration.py` calls it directly with explicit modes to unit-test
the algorithm; keeping it public avoids churning those tests. Convention:
production code uses the wrappers, tests may use either.

**D4 — Tests land with the refactor in the same change.** The new
prematch-preserve and match-drop tests are written against the wrappers so the
enforcement contract is pinned by the change that introduces the wrappers,
making the "behaviour-preserving" claim verifiable rather than asserted.

## Risks / Trade-offs

- **A call site is missed or mapped to the wrong wrapper** → `grep -rn
  'arbitrate('` confirms exactly three production call sites; each is mapped
  explicitly in the table above; the full suite plus the new per-stage tests
  guard the mapping.
- **Wrappers drift from the underlying signature** → wrappers forward the same
  positional args and return the same 3-tuple `(new_out, group_counts,
  view_drops)`; type checking covers the contract.
- **A behaviour change sneaks in under the refactor** → acceptance bar is
  byte-identical Match JSON, guarded by the existing
  deterministic-ordering test plus the 536-test suite.

## Migration Plan

In-process refactor only — no data, schema, or format migration. Deploy is a
normal merge; rollback is `git revert` of the single commit.

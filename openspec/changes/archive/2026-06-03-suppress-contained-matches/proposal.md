## Why

A product mixes two template variants of the **same class** — e.g. an
SMD-mask-only pattern (two rectangles) and an SMD-mask-plus-body pattern
(the same two rectangles **plus** the centre body). On a location that
has the body (BD / POD, and some SBT), scan-all / save-match fire **both**
templates, so one physical SMD is recorded **twice**: once under the
mask-only template key and once under the mask+body key. The persisted
Match JSON — which the rule-check pipeline consumes **per match
instance** — therefore double-counts that feature. The operator wants the
more-complete match (mask+body) to win and the contained one dropped.

This is not SMD-specific: any class whose library holds both a partial and
a fuller template of the same feature exhibits it, across every component
role (SBT / BD / POD / RING / LID).

## What Changes

- Add a post-match resolution step `suppress_contained_matches(out)` that,
  **within each class**, drops any match instance whose consumed-handle
  set is a **proper subset** of another instance's handle set. The
  superset (more handles) wins. Exact-duplicate handle sets collapse to a
  single instance (tie-break: more handles, then earliest template index —
  per the operator's "handle 數量" rule).
- Wire it into the persisted Match JSON builder (`_save_match_worker`),
  after the per-class matching loop + view split (`split_matches_by_side`)
  have produced `out`, and **before** the dict is written to disk. The
  worker response's `total_matches` and `side_counts` are recomputed from
  the post-suppression `out`, plus a new `suppressed_count` field.
- The rule is **class-agnostic and role-agnostic by construction** (it
  compares raw DXF-handle sets, never class identity), so it applies
  uniformly to every class across every role. It is **scoped to same-class
  comparisons only** — it never re-keys or drops across classes
  (cross-class disambiguation stays the job of the mutually-exclusive view
  constraints in `split_matches_by_side`). This deliberately avoids
  suppressing, say, a `FiducialCircle` whose handle happens to sit inside
  some unrelated multi-entity pattern's handle set.
- Default-on, with a module-level `CONTAINED_SUPPRESSION_ENABLED` flag read
  live at call time (an in-process attribute set takes effect immediately).
  This is a source-level constant — it is NOT registered in the
  developer-override store, so changing it in a running deployment requires a
  restart; a live dev-panel toggle is a deferred follow-up.
- **No change to `scan_all` or `_preprocess_worker`.** Both collapse
  matches to per-class handle **unions**, which are provably invariant
  under same-class subset suppression (every handle of the dropped subset
  instance already belongs to the surviving superset instance of the same
  class). A regression test locks this invariant.

## Capabilities

### New Capabilities
- `contained-match-suppression`: drop a match instance whose consumed
  DXF-handle set is contained in another same-class instance's handle set,
  so the most-complete template variant wins and the persisted Match JSON
  does not double-count a single physical feature.

### Modified Capabilities
<!-- None. The pattern-matching geometric core is unchanged; the scan-all
     and prematch preview paths are provably unaffected (per-class union),
     so no existing requirement changes. -->

## Impact

- **Code**: `app/side_regions.py` — new `suppress_contained_matches()`
  function + a live-read `CONTAINED_SUPPRESSION_ENABLED` flag.
  `app/jobs.py:_save_match_worker` — call the function after the loop and
  recompute the response counts. No change to `app/main.py:scan_all` or
  `app/jobs.py:_preprocess_worker`.
- **Output contract**: `data/match/{file_id}.json` may carry fewer
  instances under the partial-template key; the save-match job response
  gains `suppressed_count` and recomputed `total_matches` / `side_counts`.
- **Downstream**: the rule-check pipeline (`app/rule_check.py` →
  `materialise_bundle` → external checker) sees each physical feature once.
- **Tests**: new `tests/test_contained_match_suppression.py` (unit) plus a
  `_save_match_worker` integration test and a scan-all union-invariance
  regression test, matching the fixtures in
  `tests/test_match_json_constraints.py` and `tests/test_side_regions.py`.

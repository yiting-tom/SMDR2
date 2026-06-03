## Context

`scan_all` (`app/main.py`), `_save_match_worker` (`app/jobs.py`), and
`_preprocess_worker` (`app/jobs.py`) each run every class's every template
through `find_matches_from_pointsets`, then `split_matches_by_side`,
accumulating a prefixed dict `out = {"<view>.<snake>.<idx>": [[handles], ...]}`.
`_save_match_worker` writes `out` verbatim to `data/match/{file_id}.json`,
which the rule-check pipeline consumes **per match instance** (one
`(file_id, class, handles)` tuple per handle-list).

`MatchResult` records the exact DXF handles each match consumed, and
`split_matches_by_side` preserves them as per-instance handle-lists. A
single class can hold several templates (`idx` 0, 1, …). When the library
has both a **partial** template (SMD mask-only: two rectangles) and a
**fuller** template (SMD mask+body: the two rectangles plus the centre
body), a location that has the body satisfies **both** — the partial
instance's handles are a proper subset of the fuller instance's handles —
so the feature is recorded twice and the rule-check double-counts it.

The earlier density-based arbitration subsystem was **removed** (the
comments at `app/main.py:1225` and `app/jobs.py:819` state "no
post-match arbitration step"); `BGABall`/`FiducialCircle` are now
disambiguated purely by mutually-exclusive view constraints in
`split_matches_by_side`. So a containment-suppression pass would be the
**only** post-match resolution step operating on `out`.

## Goals / Non-Goals

**Goals:**
- The persisted Match JSON records each physical feature **once** when a
  fuller same-class template subsumes a partial one.
- Generic across **all classes and all roles** (SBT / BD / POD / RING /
  LID) — no SMD/SBT special-casing.
- Deterministic, default-on, with a source-level toggle.

**Non-Goals:**
- Cross-class suppression or re-keying (cross-class disambiguation stays
  the job of view constraints).
- Any change to the geometric matcher (`pattern-matching` core).
- Retroactive repair of already-saved Match JSON — the next save-match
  regenerates it with suppression applied.
- The interactive single-template `match` endpoint (`app/main.py:1070`),
  which produces no aggregated `out` and must not get cross-template
  suppression.

## Decisions

**D1 — Entity-set containment, not bbox/IoU.**
Each `MatchResult` already carries the consumed handles, so exact
set-subset is precise and threshold-free. *Alternative (bbox / IoU
overlap)* rejected: needs a tolerance to tune and would risk false
positives on features that are spatially nested but geometrically
distinct.

**D2 — Proper subset (⊊) only, plus exact-duplicate collapse.**
Drop X only when `X.handles ⊊ Y.handles`; partial overlap and disjoint
sets keep both instances. Tie-break for **equal** handle sets follows the
operator's rule: more handles wins (a tie here), then the earliest
template `idx` (cosmetic — same class, same handles). Rationale: suppress
only when one match's geometry is *fully* explained by another.

**D3 — Same-class scope, pooled across view prefixes.**
Group instances by snake class (`parse_match_key`), **ignoring** the view
prefix. Cross-prefix pooling is safe because two *different* physical
instances have disjoint handles and can never be subsets; it is also
*necessary* because a feature near a view-region boundary could have its
partial and fuller matches land under different prefixes (their bbox
centres differ slightly) and otherwise escape suppression. Cross-**class**
comparison is excluded so a `FiducialCircle` whose single handle happens
to sit inside an unrelated multi-entity pattern is never suppressed.
*Alternative (group by `(prefix, class)`)* rejected for the
boundary-straddle gap.

**D4 — Apply in `_save_match_worker` only; previews are provably immune.**
`scan_all` and `_preprocess_worker` collapse `out` to per-class handle
**unions**. For any dropped X with `X.handles ⊊ Y.handles` and Y the same
class, every handle of X already belongs to Y, so the class's union is
unchanged. Therefore the scan-all / prematch responses are invariant under
suppression and need no code change. *Alternative (call the function in all
three producers)* rejected as no-op churn; instead a regression test locks
the union-invariance.

**D5 — Non-iterative evaluation over the full representative set.**
After collapsing exact duplicates, drop X iff there exists another
representative Y with `X.handles ⊊ Y.handles`, evaluated against the
original handle sets of the whole representative set. This handles
transitive chains (`X ⊊ Y ⊊ Z` drops X and Y, keeps Z) and is
order-independent, hence deterministic.

**D6 — Recompute response counts.**
After suppression, `total_matches` and the `top_view/bottom_view/side_view/
unassigned` parts of `side_counts` are recomputed from the surviving `out`;
the `dropped` count (view-constraint drops from the split phase) is
retained as-is; a new `suppressed_count` field reports how many instances
were removed. Keeps the reported numbers consistent with the written file.

**D7 — Home: `app/side_regions.py`, with a source-level flag.**
It already owns `parse_match_key`, `split_matches_by_side`, and the
key/instance structure, and both `app/jobs.py` and `app/main.py` import
from it. A module-level `CONTAINED_SUPPRESSION_ENABLED = True` is read via a
bare global lookup on each call, so an in-process attribute set takes effect
immediately (tests rely on this). It is deliberately NOT wired into the
developer-override store: that store's allow-list only models numeric
`app.matching`/`app.dxf` tunables (no bool coercion), and `_save_match_worker`
runs in a process-pool worker that takes no override snapshot — so a true
no-restart dev-panel toggle would require extending the override store and
threading a snapshot into the worker. That is out of scope for "keep a config
field"; the flag is a source-level constant (change + restart, or in-process
`setattr`). *Alternative (register in the override store)* deferred as a
follow-up if a live rollback lever is ever needed.

## Risks / Trade-offs

- **False suppression of a legitimately distinct same-class feature** →
  Within one class, `X ⊊ Y` only arises when the matcher consumed X's
  exact entities as part of Y — i.e. genuine redundancy. Same-class +
  proper-subset scope keeps this conservative; cross-class is never
  touched.
- **Performance: O(M²) per class group** → M is the instance count of a
  single class; `frozenset` subset checks are cheap and groups are small.
  No measurable impact on the ~7 s / 51-template scan.
- **Response-count drift** → An integration test asserts
  `total_matches` / `side_counts` / `suppressed_count` agree with the
  written file.
- **Operator may later want partial + full counted separately** →
  Default-on but flag-toggleable; a future per-class opt-out can extend the
  flag without reworking the algorithm.
- **Already-saved Match JSON is not repaired** → Documented non-goal; the
  next save-match regenerates it.

## Migration Plan

Additive behavior taking effect on the next save-match build; no data
migration. Rollback is reverting the change (or flipping the source constant
`CONTAINED_SUPPRESSION_ENABLED` to `False`) and restarting, then re-saving —
this is a source-level switch, not a live dev-panel toggle.

## Open Questions

None blocking. Cross-class suppression is intentionally out of scope and
should be revisited only if a real cross-class redundancy case appears.

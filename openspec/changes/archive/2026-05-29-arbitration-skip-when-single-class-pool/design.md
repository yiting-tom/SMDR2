## Context

`arbitrate()`'s purpose is to resolve cross-fire — the matcher's class-agnostic geometry-only pipeline returns the same handles under both `BGABall.0` and `FiducialCircle.0` whenever those templates share a circle radius bucket. Arbitration disambiguates by neighbour density: dense grids → BGABall, isolated points → FiducialCircle.

This design only makes sense when there ARE multiple competing templates in the library. If the library has ONLY BGABall templates (no FiducialCircle templates), the matcher never produces `fiducial_circle.*` keys; every pool instance carries `original_class="BGABall"` from `pool_instances`. Running `classify()` on this single-class pool can only:

1. Confirm BGABall labels (when neighbour count satisfies `MinNeighbors(2)`)
2. Mis-label some BGABall instances as FiducialCircle (when neighbour count is ≤ 1)

Case 2 is exactly the user-reported bug. The matcher's BGABall template cross-fired on isolated same-radius circles (vias, drill holes, decorative dots) in addition to the main BGA grid. Those isolated circles have 0 neighbours → classify gives them FiducialCircle → re-emit as `fiducial_circle.0` → response carries phantom FiducialCircle entries even though no FiducialCircle template exists.

The previous change (`arbitration-fallback-requires-default-evidence`) addressed the `apply_population_fallback` path. The classify path is upstream of fallback and produces its mis-labels independently.

## Goals / Non-Goals

**Goals:**
- Skip the entire classify → fallback → re-emit pipeline when the pool is single-class.
- Preserve existing behaviour exactly when the pool has multiple `original_class` members (multi-template cross-fire is the only case where arbitration adds value).
- Subsume the existing "empty / singleton pool" early-return — single-class is a strict superset.

**Non-Goals:**
- No change to `classify()` rules.
- No change to `derive_pitch`, `count_neighbors`, `apply_population_fallback`.
- No change to `min_population` constant.
- No change to the public `arbitrate()` signature or return shape.
- No retroactive cleanup of stale prematch JSON files. Same recovery path as the previous change: next prematch run (re-upload, or live `/scan-all` click) regenerates with the fix.

## Decisions

### D1. Short-circuit on `len({inst.original_class for inst in instances}) == 1`

```python
unique_original_classes = {inst.original_class for inst in instances}
if len(unique_original_classes) == 1:
    sole_class = next(iter(unique_original_classes))
    gc.assigned = {sole_class: len(instances)}
    group_counts[label] = gc.to_dict()
    continue
```

This goes BEFORE the existing `if len(instances) < 2` guard. Single-class with `len(instances) < 2` is correctly handled by the new guard alone — the old guard becomes dead code for the singleton case but stays as a safety net for the literal empty-pool case (where `set()` over no instances would yield `len() == 0`, falling through to the old `< 2` guard). To keep the diff minimal and the guard order intuitive, we leave the `< 2` guard in place.

**Why not check `len(unique_original_classes) <= 1`?** Empty pool means `unique_original_classes == set()` — `next(iter(...))` would raise `StopIteration`. The `< 2` guard below handles empty cleanly. Strict `== 1` keeps the new guard semantically tight.

**Why `original_class` (pre-classify) and not `per_class_pre` (post-classify)?** `per_class_pre` requires running classify first, which is exactly what we want to skip. `original_class` is recorded by `pool_instances` from the source key in `out` — available immediately without any kdtree work.

### D2. Diagnostics: keep `assigned`, drop `derived_pitch`

`GroupCounts.assigned` is the operator-facing record of "what each class got in this pool". For single-class, that's `{sole_class: N}`. We populate it.

`GroupCounts.derived_pitch` is the median NN distance used to size the neighbour-count radius. For single-class we don't compute it; it stays `None`. This matches the existing "degenerate pitch" branch (line 308-315) which also leaves it None and continues with original labels.

`GroupCounts.population_fallback_triggered` and `reassigned_from_match` stay at their default `False` / `0`. No reassignment happened.

`GroupCounts.dropped_by_view` stays `0`. The single-class short-circuit doesn't re-validate view constraints. **This is intentional:** the source keys (e.g. `top_view.bga_ball.0`) were already view-validated by `split_matches_by_side` upstream — anything in the pool already survived its class's view constraint. The view re-validation in arbitrate's re-emit loop exists to catch instances whose RESOLVED class differs from the SOURCE class (a reassigned BGABall → FiducialCircle whose centroid sits in top_view, allowed for FiducialCircle but BGABall's already-passed top_view filter doesn't transfer). For single-class no reassignment happens, no re-validation needed.

### D3. Leave the keys in `new_out` untouched

The existing arbitration body deletes member-class keys from `new_out` and re-emits resolved keys. For single-class, the source keys ARE the correct keys — no delete, no re-emit. `new_out = dict(out)` (the shallow copy at the top of `arbitrate`) already contains them.

### D4. Spec change: new ADDED requirement instead of MODIFIED

The single-class short-circuit is a NEW invariant — arbitration becoming a no-op under a specific condition that didn't have a documented requirement before. Adding it as a separate requirement keeps the existing "Population fallback" requirement intact (still describes the multi-class behaviour correctly) and gives the new invariant its own anchor for future references.

## Risks / Trade-offs

- **[Risk]** A future arbitration group with > 2 members where the matcher legitimately produces single-class pools on a regular basis (e.g., a group with 3 classes where the library typically has only one template at a time). → **Mitigation:** today the only group is BGABall+FiducialCircle. If a 3-member group is added later, the same short-circuit logic still applies correctly — single-class pool is intrinsically unambiguous regardless of group size.
- **[Risk]** The 4-corner-fiducial degenerate scenario the original fallback was designed for. → **Mitigation:** in that scenario, the source keys are `top_view.fiducial_circle.0` for all 4 corners. All have `original_class=FiducialCircle`. New guard fires → all stay FiducialCircle. Same end result as today's "classify mis-labels as BGABall → fallback collapses back to FiducialCircle" path. Test `test_arbitrate_only_corner_fiducials_triggers_population_fallback` will need its `population_fallback_triggered` assertion updated from `True` to `False` (the result is the same but the path is now the short-circuit, not the fallback).
- **[Trade-off]** Diagnostic field `population_fallback_triggered` no longer fires for single-class pools, including the 4-corner case. Downstream consumers (operator-facing telemetry, if any) lose the distinction between "fallback fired" and "no arbitration needed". Acceptable — both outcomes mean "no reassignment happened", which is the operator-actionable signal.

## Migration Plan

No DB / on-disk migration. Same recovery as the previous change: next prematch run regenerates with correct labels.

## Open Questions

None.

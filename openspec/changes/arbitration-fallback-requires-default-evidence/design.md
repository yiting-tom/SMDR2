## Context

`apply_population_fallback` was added to guard against a degenerate failure mode of neighbour-density classification: if a DXF has 4 corner fiducials and no BGA grid, the 4 fiducials are close enough together (relative to derived pitch) to register as a tiny BGA grid and get mis-labeled `BGABall`. The fallback says: "if there aren't enough BGABalls to look like a real grid, call them all FiducialCircle — that's the safe direction because BGABall is the harder-to-believe class."

The implementation in `app/class_arbitration.py` (line 329-337) checks whether any non-default member's classified count is below `min_population`. The fallback is then applied unconditionally if any such member exists. This is the bug: the safe direction (`default_class`) is only meaningful as a target when `default_class` is genuinely a candidate — i.e. its templates produced at least one match in the pool. When only `BGABall` templates exist in the library, the pool contains only BGABall instances, and falling back to FiducialCircle creates labels that have no library template behind them. Downstream consumers (the viewer's chip count, the canvas highlight, the save-match JSON) treat those phantom labels as real, producing the user-visible bug.

## Goals / Non-Goals

**Goals:**
- Stop the fallback from inventing labels when the default class has no instances in the pool.
- Preserve every other branch of the fallback exactly as today: when both classes are present and the non-default class is below floor, still collapse to default (the original intent).
- Keep `arbitrate()`'s signature, return shape, and diagnostics unchanged.

**Non-Goals:**
- No change to `min_population` value (still 8). User flagged this as a potential follow-up if real-world data has legitimately small BGA grids that get caught by the floor even with the precondition.
- No change to `classify()` neighbor-density rules (`MinNeighbors`, `MaxNeighbors`).
- No change to `pitch_multiplier` / `derive_pitch`.
- No change to the prematch worker's `enforce_view_constraints=False`. That's correct at preprocess time; view rects don't exist yet.
- No retroactive cleanup of existing prematch JSON files on disk. The next prematch run (e.g. after a re-upload, or a Scan All click) regenerates with the fixed labels.

## Decisions

### D1. Gate `fallback_triggered` on `default_in_pool`

```python
default_in_pool = any(
    inst.original_class == group.default_class
    for inst in instances
)
fallback_triggered = default_in_pool and any(
    per_class_pre[m] < group.min_population
    for m in group.members if m != group.default_class
)
```

`instances` (from `pool_instances`) has each `_Instance` tagged with `original_class` — the class whose template originally produced the match (read off the match-JSON key prefix). `default_in_pool=True` means "the default class actually appeared as a matcher result in this DXF", which is exactly the evidence we need before we trust the fallback's target.

**Why `original_class` (pre-classify) instead of `per_class_pre[default_class]` (post-classify)?**

`per_class_pre` reflects what `classify()` labeled each instance based on neighbour density. It can be > 0 for the default class even when the default class's templates contributed zero matches — `classify()` re-labels based on geometry, not provenance. We want to know whether the default-class TEMPLATE produced any match-result, which is the `original_class` (set by `pool_instances` from the source key in `out`). This makes the precondition robust to the very degenerate case the original fallback was designed to catch (4 corner fiducials registering as a tiny BGA): in that scenario `original_class == "FiducialCircle"` for all 4, so `default_in_pool=True`, fallback still triggers, behavior unchanged.

**Why a precondition rather than a separate "skip" branch?**

Folding it into the `fallback_triggered` boolean preserves the existing control-flow shape (one `if fallback_triggered` branch downstream), keeps `gc.population_fallback_triggered` semantically correct (it records whether fallback actually fired, which is now also influenced by the precondition), and minimises the diff.

### D2. Diagnostics: no new fields

The existing `GroupCounts.population_fallback_triggered` bool stays. When `default_in_pool=False` it'll be `False` whether or not the count test would have fired. That's the right semantics — fallback didn't trigger, regardless of the reason. Adding a separate "skipped because default absent" diagnostic would clutter the contract without any caller using it. If we ever need to debug a specific case, the existing `assigned` counts in `GroupCounts` are sufficient to reconstruct.

### D3. Test placement

Add the new regression case to the existing `tests/test_class_arbitration.py` rather than a new file. The test suite already exercises the fallback path; the new test belongs next to its peers for discoverability.

## Risks / Trade-offs

- **[Risk]** Edge case: a DXF has 1 legitimate FiducialCircle template that matched 1 instance, plus N < 8 BGABalls. `default_in_pool=True` → fallback still triggers → all N+1 collapse to FiducialCircle. → **Trade-off, by design:** matches existing behavior. The user's reported scenario (zero FiducialCircle matches) is the clean separation point.
- **[Risk]** A DXF has only FiducialCircle templates + zero BGABall instances. `default_in_pool=True` (FiducialCircle is the default), `per_class_pre[BGABall] = 0 < 8` → fallback triggers → all instances become FiducialCircle (which they already were, since FiducialCircle is the only class with templates). → **No-op, behavior unchanged.**
- **[Trade-off]** The fix does NOT address the orthogonal concern that `min_population=8` may be too aggressive for legitimately small BGA grids (4-6 balls). User flagged this; we'll address in a follow-up change if real-world data demands it.

## Migration Plan

No DB / on-disk migration. Stale prematch JSON files written under the buggy logic still have phantom FiducialCircle labels; they regenerate on next prematch run (re-upload, or a Scan All click which uses the live scan-all endpoint).

## Open Questions

None.

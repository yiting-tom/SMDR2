## Context

`arbitrate()` resolves matcher cross-fire: the geometry-only matcher returns the same handles under every class whose template shares a circle radius bucket. Arbitration disambiguates by neighbour density — dense grids → `BGABall`, isolated points → `FiducialCircle`.

`ce8b9fb` added a single-class short-circuit: if `out` carries keys for exactly one member class, skip classify/fallback/re-emit and leave the source keys untouched. Its rationale: with one class's templates in the library, there is no cross-class ambiguity and `classify()` could only *mis-label*, never resolve. That reasoning is sound for the **non-default** class but breaks for the **default** class, and the implementation gated on neither — it short-circuits for any single sole class.

### Why the default class is different

The arbitration group is asymmetric by construction:

- `BGABall` (non-default) carries `MinNeighbors(2)` — it is the *claim*: "this is a grid." The matcher only fires the BGABall template on geometry that already passed BGABall's gate. A single-class BGABall pool is already the answer; classify can only demote isolated members to phantom fiducials. **Short-circuit is correct.**
- `FiducialCircle` (default) carries `MaxNeighbors(1)` and is the *safe fallback* — what an instance gets when nothing else fits. A single-class FiducialCircle pool is **not** an answer: the fiducial template may have been the only one that matched a real BGA grid (its diameter coincides with the ball diameter; no BGABall template fired). Density still disambiguates **upward** — promote the grid to `BGABall`. **Short-circuit strands the grid under the fiducial label.**

This is the BGA-highlighted-as-FiducialCircle regression. At `612a31e` (pre-`ce8b9fb`) the default-class pool ran classify and promoted correctly.

## Goals / Non-Goals

**Goals:**
- Restore upward promotion for a single-class pool whose sole class is the default, while keeping `ce8b9fb`'s fix for a single-class non-default pool.
- Keep the 4-corner-fiducial outcome (all stay `FiducialCircle`) — now reached via classify + population fallback rather than the short-circuit.

**Non-Goals:**
- No change to `classify()` rules, `derive_pitch`, `count_neighbors`, `apply_population_fallback`, or `min_population`.
- No change to the `default_in_pool` fallback precondition from `arbitration-fallback-requires-default-evidence`.
- No change to `arbitrate()`'s signature or return shape.
- No retroactive cleanup of stale prematch JSON — regenerates on next run.

## Decisions

### D1. Gate the short-circuit on `sole_class != group.default_class`

```python
if len(classes_with_keys) == 1 and instances:
    sole_class = next(iter(classes_with_keys))
    if sole_class != group.default_class:
        gc.assigned = {sole_class: len(instances)}
        group_counts[label] = gc.to_dict()
        continue
    # else: fall through to classify + population fallback
```

The `classes_with_keys` computation (RAW input keys, not the deduped `original_class`) is unchanged — it still correctly distinguishes a true two-template cross-fire (both keys present → not single-class) from a single-template library. Only the *consequence* of a single sole class is now conditional on whether that class is the default.

**Why gate on the sole class, not on a density pre-check?** The whole point of the fall-through is to let the existing classify + fallback machinery decide. Re-deriving density here to decide whether to fall through would duplicate that machinery. Falling through is cheaper and reuses the already-tested path.

### D2. The fall-through is fully handled by existing code

For a default-class single-class pool that falls through:
- `if len(instances) < 2` still guards the empty/singleton case (a lone fiducial stays a fiducial — no promotion possible without neighbours).
- classify labels grid points `BGABall`; the `default_in_pool` precondition is satisfied (the default class *is* in the pool), so the population floor applies: `< min_population` non-default candidates collapse back to the default; `≥ min_population` survive as `BGABall`.
- The re-emit loop re-validates view constraints of the resolved class as usual.

No new code path is introduced; D1 only removes a premature exit.

### D3. Diagnostics follow the path actually taken

A promoted grid reports `derived_pitch` (computed), `population_fallback_triggered=False`, `assigned={BGABall: N, FiducialCircle: 0}`. A collapsed 4-corner set reports `derived_pitch` (computed), `population_fallback_triggered=True`, `assigned={FiducialCircle: 4, BGABall: 0}`. The `ce8b9fb`-era short-circuit diagnostics (`derived_pitch=None`) no longer apply to default-class pools — they were never operator-actionable for this case anyway.

## Risks / Trade-offs

- **[Risk]** A genuine fiducial-only DXF whose fiducial count ≥ `min_population` and whose fiducials happen to cluster densely enough to read as a grid would now be promoted to `BGABall`. → **Mitigation:** this is the fundamental same-diameter ambiguity the system cannot resolve from geometry alone; `min_population=8` plus the `1.5×pitch` radius is the chosen threshold, identical to the pre-`ce8b9fb` behaviour. Real fiducial sets are 3–6 isolated marks, far below the floor, and collapse correctly.
- **[Trade-off]** Default-class single-class pools pay the kdtree cost again. Negligible at the pool sizes involved.
- **[Risk]** A future arbitration group with multiple non-default members. → **Mitigation:** the gate `sole_class != group.default_class` generalises: every non-default sole class short-circuits, only the single default class falls through. No N-member assumption.

## Migration Plan

No DB / on-disk migration. Same recovery as the prior arbitration changes: the next prematch run (re-upload, or live Scan All) regenerates labels with the fix.

## Open Questions

None.

## Why

The previous fix (`arbitration-fallback-requires-default-evidence`) addressed the `apply_population_fallback` path that collapses BGABall pools to FiducialCircle when the floor isn't met. It missed the OTHER mis-label path: `classify()` itself assigns each pool instance a class based on its in-radius neighbour count, BEFORE the fallback check runs. For the user's 17483-ball BGA at pitch 0.9, classify gives the main grid `BGABall` correctly, but if the BGABall template happens to cross-fire on a few isolated same-radius circles elsewhere in the DXF (vias, drill holes, decorative dots), those isolated circles have 0 neighbours within `1.5 × pitch` → `MinNeighbors(2)` rule fails → `MaxNeighbors(1)` (FiducialCircle) wins → re-emitted as `fiducial_circle.0` keys.

Empirically the user's reported case has `/scan-all` returning `by_class.FiducialCircle: [...17483 handles...]` — the precondition correctly suppresses the fallback collapse, but classify ran on every instance individually, and the cross-fire-on-isolated-circles path produced enough FiducialCircle re-emissions to surface as the dominant label.

The underlying invariant: **arbitration only makes sense when the matcher cross-fired across MULTIPLE group members.** When only one member's templates are in the library, every pool instance carries the same `original_class` — there's no cross-class ambiguity to resolve, and `classify()` can only mis-label, not disambiguate. The cleanest fix is to short-circuit `arbitrate()` for any group whose pool is single-class.

## What Changes

- Modify `app/class_arbitration.py::arbitrate` so each per-group iteration short-circuits at the top when `{inst.original_class for inst in instances}` has exactly one element. The short-circuit:
  - Records `gc.assigned = {sole_class: len(instances)}` for diagnostics.
  - Leaves `gc.derived_pitch` as `None` (not computed, irrelevant).
  - Leaves `gc.population_fallback_triggered` as `False`.
  - Does NOT delete any keys from `new_out` or re-emit any keys — the pre-arbitration keys (e.g. `bottom_view.bga_ball.0`) are already correct.
- Place the new early-return BEFORE the existing `if len(instances) < 2` guard. The new guard subsumes that case (singleton pool is trivially single-class) but adds the broader N-instance same-class case.
- Add a regression test `tests/test_class_arbitration.py::test_arbitrate_skips_classify_when_pool_is_single_class` covering the user's scenario: many BGABall instances at LOW density (so classify WOULD label them FiducialCircle) → assert every instance stays BGABall, no `fiducial_circle.*` keys emitted.
- Add a new requirement to the `class-arbitration` spec: "Single-class pool short-circuits arbitration".

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `class-arbitration`: ADDS a new requirement "Single-class pool short-circuits arbitration" describing the early-return invariant. The existing "Population fallback" requirement stays — the new short-circuit is more aggressive and runs earlier, but the fallback semantics still apply when the pool has multiple `original_class` members.

## Impact

- **Code**: One block of ~7 lines inserted at the top of the per-group loop in `app/class_arbitration.py::arbitrate`.
- **Tests**: One new test in `tests/test_class_arbitration.py`. The existing 19 tests stay green because none of them rely on classify-based re-labeling within a single-class pool (the 4-corner fiducial degenerate test has `original_class=FiducialCircle` for all 4, which is single-class → new guard short-circuits → same end result as today's "fallback fires → all FiducialCircle").
- **API**: No change to `arbitrate()` signature or return shape. `GroupCounts.assigned` populated for single-class case via the short-circuit. `derived_pitch` is None for single-class case (was previously computed even when its only effect was to drive a no-op).
- **Specs**: `openspec/specs/class-arbitration/spec.md` ADDS one requirement.
- **Data**: No DB migration. Existing stale prematch JSON files regenerate on next prematch run (re-upload, or Scan All click which uses the live endpoint).
- **Performance**: Tiny win — single-class pools skip `derive_pitch` (kdtree) + `count_neighbors` (kdtree) + `classify` (N rule checks).

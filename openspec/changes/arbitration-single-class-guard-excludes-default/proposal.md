## Why

The previous change (`arbitration-skip-when-single-class-pool`, commit `ce8b9fb`) added a single-class short-circuit to `arbitrate()`: when only one of a group's member classes contributed keys to `out`, classify/fallback/re-emit are all skipped and the source keys stay untouched. It fixed the case where a **BGABall-only** library cross-fired its template onto a few isolated same-radius circles (vias / drill holes) and `classify()` demoted those isolated balls to phantom `FiducialCircle` keys.

But the short-circuit was written **symmetrically** — it fires for *any* single sole class — while the safety argument only holds for the **non-default** member. The default class (`FiducialCircle`) is the low-confidence safe fallback; a single-class pool whose sole class is the default can still be a real BGA grid that happened to be matched only by the fiducial template (e.g. the fiducial template's diameter coincides with the ball diameter, and no BGABall template fired). Density evidence should **promote** that grid to `BGABall`, but the symmetric short-circuit strands it under the fiducial label.

This is the user-reported regression: **a real BGA grid is highlighted as FiducialCircle (teal) instead of BGABall (orange).** Empirically, a 25-point grid at pitch 1.0 keyed only as `fiducial_circle` returns `assigned = {FiducialCircle: 25}` at HEAD even though every point has 3–8 neighbours within `1.5 × pitch` and `classify()` would label all 25 `BGABall`. At `612a31e` (before `ce8b9fb`) the same input ran classify and correctly promoted to `BGABall`.

## What Changes

- Make the single-class short-circuit in `app/class_arbitration.py::arbitrate` **asymmetric**: it fires only when the sole class with keys is a **non-default** member (`sole_class != group.default_class`).
  - Sole class is non-default (e.g. `BGABall`) → short-circuit as before: high-confidence label, no cross-class competition, classify could only mis-label. Keep source keys untouched. (`ce8b9fb`'s fix preserved.)
  - Sole class **is** the default (`FiducialCircle`) → fall through to classify + population fallback. A genuine grid (≥ `min_population`) is promoted to `BGABall`; a handful of true corner fiducials (< `min_population`) is collapsed back to the default by the floor. (Restores the `612a31e` behaviour for this case.)
- Update the existing test `test_arbitrate_only_corner_fiducials_short_circuits_via_single_class_guard` → renamed back to `test_arbitrate_only_corner_fiducials_triggers_population_fallback`; the 4-corner outcome is unchanged (all 4 stay `FiducialCircle`) but the diagnostic path is classify-then-fallback again (`population_fallback_triggered=True`, `derived_pitch=15.0`).
- Add a regression test `test_single_class_default_pool_grid_is_promoted_to_bga` covering the user scenario: a 25-point grid keyed only as `fiducial_circle` is promoted to `BGABall`.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `class-arbitration`: MODIFIES the "Single-class pool short-circuits arbitration" requirement so the short-circuit is gated on a non-default sole class. A single-class pool whose sole class is the default class runs the full classify + population-fallback pipeline.

## Impact

- **Code**: One condition + comment in `app/class_arbitration.py::arbitrate` (`if sole_class != group.default_class:` wrapping the existing short-circuit body).
- **Tests**: One existing test's assertions restored to the fallback path; one new regression test. Full suite 548 passing.
- **API**: No change to `arbitrate()` signature or return shape.
- **Specs**: `class-arbitration` MODIFIES one requirement (delta in this change).
- **Data**: No DB / on-disk migration. Stale prematch JSON regenerates on the next prematch run (re-upload, or a live Scan All click).
- **Performance**: Default-class single-class pools now pay `derive_pitch` + `count_neighbors` + `classify` again (kdtree over the pool). Negligible — the only single-class default pools in practice are small fiducial sets or full grids, both already handled by the live endpoints.

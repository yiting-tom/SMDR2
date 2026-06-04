## Why

`BGABall` and `FiducialCircle` share circle geometry, so the matcher cross-fires both templates on the same circles. They were split by a **neighbour-density** heuristic (`CLASS_ARBITRATION_GROUPS`): dense grid → BGABall, isolated → FiducialCircle, with a `min_population` floor.

That heuristic proved fragile in production. With a `FiducialCircle` template present, a real BGA grid (reported: 17 482 balls) was getting reassigned wholesale to `FiducialCircle` — the match JSON came out as `bottom_view.fiducial_circle.0`. The failure was not reproducible in any synthetic case (the density path computes correctly for a clean grid), which points to a data-specific `derive_pitch` / population-floor edge case. Rather than chase an unreproducible heuristic bug, this change replaces density disambiguation with a **physical, deterministic** rule the operator already understands: BGA balls face the package **bottom**, fiducials sit on the **top**.

## What Changes

- `CLASS_VIEW_CONSTRAINTS` becomes the disambiguator. New constraints:
  - `BGABall` → `{bottom_view}` (was `{bottom_view, side_view}` — side_view retired)
  - `FiducialCircle` → `{top_view}` (was unconstrained)
  - `FiducialCross`, `FiducialSquare` → `{top_view, bottom_view}` (were unconstrained)
  - `SMD-2T` / `SMD-3T` / `SMD-8T` / `SMD-14T` → `{top_view, bottom_view}` (were unconstrained)
  - `C4Ball` → `{top_view}` (unchanged)
- `CLASS_ARBITRATION_GROUPS` is now **empty** `()`. The BGABall|FiducialCircle density group is removed. `arbitrate()` is retained (a no-op over an empty registry) so a future *same-view* same-geometry collision can be handled by adding a group.
- Since `BGABall` (bottom) and `FiducialCircle` (top) are mutually exclusive views, a circle is classified by the view it sits in. Same-view cross-fire is resolved by the view-constraint drop in `split_matches_by_side` (a bottom-view circle matched by both keeps the BGABall key; the FiducialCircle match is dropped because FiducialCircle is top-only).
- `canvas.js` JS mirrors updated: `CLASS_VIEW_CONSTRAINTS` (new table) and `CLASS_ARBITRATION_MEMBERS` (now `[]`). Drift-guard tests enforce parity.

## Capabilities

### Modified Capabilities

- `template-library`: MODIFIES "Per-class view constraint registry" — new constraint table; documents that mutually-exclusive views disambiguate same-geometry classes.
- `class-arbitration`: MODIFIES "Arbitration group declaration" — the default registry is now empty (density disambiguation retired); the `arbitrate()` machinery and `ArbitrationGroup` contract remain for future same-view collisions.

## Impact

- **Code**: `app/library.py` (`CLASS_VIEW_CONSTRAINTS`, `CLASS_ARBITRATION_GROUPS`), `app/static/canvas.js` (two JS mirrors).
- **Tests**: updated across `test_library.py`, `test_side_regions.py`, `test_class_arbitration.py`, `test_match_json_constraints.py` (view-constraint + registry expectations). Drift-guard `test_canvas_constants.py` passes. Full suite 536 passing; 1 unrelated pre-existing flake (`test_save_match_post_with_missing_parsed_file_...`, global `_jobs`-state leak, fails on `main` too).
- **Behaviour**: BGABall/FiducialCircle are now disambiguated by view at save-match / scan-all (once side regions are drawn). A real BGA grid in bottom_view stays BGABall regardless of any FiducialCircle template — the 17 482-ball misclassification is gone.
- **Known limitation**: at *prematch* (before any side rect is drawn), view constraints aren't enforced and density is gone, so *same-radius* cross-fire is no longer auto-resolved there — the class-toolbar counts can be inflated until the operator draws rects and runs scan-all/save-match. With distinct BGA/fiducial radii (no cross-fire) prematch is naturally clean. Documented in design.md.
- **Data**: none. Stale prematch/match JSON regenerates on next run.

## Context

The matcher is geometry-only: a `FiducialCircle` template and a `BGABall` template of the same radius both match every circle of that radius. The class-arbitration subsystem (`CLASS_ARBITRATION_GROUPS` + `app/class_arbitration.py`) split them by neighbour density: each circle's neighbour count within `pitch_multiplier × derived_pitch` decided BGABall (`MinNeighbors(2)`) vs FiducialCircle (`MaxNeighbors(1)`), with a `min_population=8` floor that collapsed the whole pool to the default (FiducialCircle) when too few BGABall candidates survived.

In production, a 17 482-ball BGA grid was being reassigned wholesale to FiducialCircle whenever a FiducialCircle template was present — independent of the fiducial template's radius or entity count. A full end-to-end trace and synthetic reproductions (up to 17 424 balls + fiducials, single-point and `from_circle` shapes) all classified **correctly**, so the live failure is a data-specific `derive_pitch` / floor edge case that resists reproduction. Chasing it further is poor leverage when a deterministic alternative exists.

## Decisions

### D1. Disambiguate by mutually-exclusive view, not density

BGA balls are physically on the package bottom; circle fiducials on the top. Encoding that as `BGABall → {bottom_view}` and `FiducialCircle → {top_view}` makes the two mutually exclusive: a circle is classified by which view rectangle covers it. This is deterministic, operator-legible, and immune to the density edge case.

### D2. Retire the density arbitration group

`CLASS_ARBITRATION_GROUPS = ()`. Keeping the group *alongside* the new view constraints would be actively harmful: if the density path still misfired and reassigned a bottom-view ball to FiducialCircle, the re-emit's view re-validation (`is_allowed_view(FiducialCircle, bottom_view)` → False) would then **drop** the ball entirely. So the group must go for the view rule to stand. The `arbitrate()` function and the `ArbitrationGroup` contract are retained (no-op over the empty registry) for a future *same-view* same-geometry collision.

### D3. Same-view cross-fire resolved by the view-constraint drop

When both templates match a bottom-view circle, `split_matches_by_side` keeps `bottom_view.bga_ball.*` and drops the `fiducial_circle` match (FiducialCircle is top-only). No density needed. The symmetric case resolves on top_view.

### D4. Constraint table

`BGABall` drops `side_view` (operator's rule: bottom only). `FiducialCross`/`FiducialSquare` are `{top, bottom}` (they can appear either face). `SMD-*` are `{top, bottom}`. `C4Ball` stays top-only.

## Risks / Trade-offs

- **[Trade-off] Prematch same-radius cross-fire.** Prematch runs before side rects exist, so view constraints aren't enforced and (with density gone) same-radius cross-fire is no longer auto-resolved there — the class-toolbar counts can be inflated until the operator draws rects and runs scan-all/save-match (where the view split resolves it). With **distinct** BGA/fiducial radii — the deployment's actual configuration — there is no cross-fire and prematch is naturally clean. Acceptable; the authoritative match JSON (save-match) is always correct.
- **[Trade-off] BGABall loses side_view.** A BGA ball seen in a cross-section/side view is now dropped at save-match. Per the operator, BGA balls are matched bottom-only; reinstate `side_view` in the constraint set if cross-section matching is needed.
- **[Risk] A genuine fiducial on the bottom face.** Would be dropped (FiducialCircle is top-only). The operator asserts fiducials are top-only; `FiducialCross`/`FiducialSquare` remain `{top, bottom}` for fiducials that can appear on either face.
- **[Risk] A future same-view same-geometry collision.** Re-add an `ArbitrationGroup` — the machinery is retained.

## Migration Plan

No schema/data migration. Stale prematch/match JSON regenerates on the next prematch / Scan All / Save Match.

## Open Questions

None.

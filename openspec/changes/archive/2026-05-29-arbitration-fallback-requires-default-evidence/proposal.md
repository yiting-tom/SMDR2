## Why

The population-fallback safety net in `apply_population_fallback` collapses an entire arbitration pool to `default_class` when the non-default member's classified count is below `min_population`. The check ignores whether `default_class` actually has any matched instances in the pool — so when the library has no template for the default class (or its template matched nothing in this DXF), fallback STILL fires and re-emits BGABall matches as `fiducial_circle.0` keys with no backing template.

User-reported symptom: with only BGABall templates in the library, scan-all (and the auto-loaded prematch overlay) shows every BGABall match labeled FiducialCircle — wrong colour on canvas, wrong count in the toolbar chip. Deletion experiment confirmed: removing all FiducialCircle templates does not stop the FiducialCircle phantom labels, because the fallback creates them from BGABall matches.

The fallback's documented intent is "we don't trust a thin BGA grid; the safe direction is FiducialCircle." That direction is only safe when FiducialCircle is a real alternative the matcher confirmed exists. When the pool contains zero FiducialCircle instances, there's no evidence the safe direction is in play, and the fallback is inventing labels.

## What Changes

- Modify `app/class_arbitration.py::arbitrate` so the population-fallback trigger requires a NEW precondition: `default_class` must have at least one instance in the pool (i.e. at least one match-result came from a `default_class` template). When the precondition fails, fallback is skipped and per-instance classifications from `classify()` are preserved as-is.
- Update the `class-arbitration` spec's "Population fallback" requirement to document the precondition and the new "default class absent → no fallback" scenario.
- Add a regression test: `tests/test_class_arbitration.py::test_fallback_skipped_when_default_class_has_no_pool_evidence` — pool of N BGABall instances (all with `original_class="BGABall"`), N < `min_population`, no FiducialCircle instances → assert every instance stays BGABall, no `fiducial_circle.*` keys emitted.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `class-arbitration`: Modifies the "Population fallback" requirement to add the `default_class` evidence precondition. Existing scenarios stay (BGABall below floor with FiducialCircle present → still collapse), one new scenario is added (BGABall below floor with FiducialCircle absent → no collapse).

## Impact

- **Code**: `app/class_arbitration.py::arbitrate` — one bool precondition added to the `fallback_triggered` expression.
- **Tests**: One new test in `tests/test_class_arbitration.py`. Existing tests stay green (all current "below floor → collapse" scenarios already have FiducialCircle instances in the pool).
- **API**: No change to `arbitrate()` signature / return shape. Diagnostics dict adds nothing.
- **Specs**: `openspec/specs/class-arbitration/spec.md` "Population fallback" requirement is MODIFIED.
- **Data**: No DB migration. Existing prematch JSON on disk for affected DXFs has wrong labels; running `prematch` again after the fix (or just clicking Scan All) will regenerate with correct labels.

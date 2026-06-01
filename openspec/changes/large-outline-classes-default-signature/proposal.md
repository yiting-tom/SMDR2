## Why

`Substrate`, `LidOuter`, and `LidInner` are large rigid-outline boundary classes. PCA-chamfer scoring of such a big sharp-cornered loop is sensitive to the stored winding / start vertex of the polyline: an exact CAD copy stored with opposite winding or a different first vertex can score chamfer above `TOLERANCE_ABS` and register as a false `reason="shape"` near-miss (observed: 0.286 mm vs 0.2 mm tol on two geometrically identical substrates).

Signature matching keys only on perimeter + max-radius + principal-axis aspect — it never computes chamfer, so it sidesteps the winding/start-vertex sensitivity entirely. For these classes that is the right model: their outline is fully characterised by size + aspect, and same-product substrates of the same size are the match target. Operators confirmed signature mode "perfectly catches" the substrates. This change makes signature the seeded default for these three classes (with a tight `bbox_ratio = 0.0001` size tolerance) instead of requiring a manual per-class toggle in every library.

## What Changes

- Add `CLASS_DEFAULT_MATCH_CONFIG` in `app/library.py`: `Substrate` / `LidOuter` / `LidInner` → `('signature', 0.0001)`. All other classes remain `('chamfer', None)`.
- `LibraryRegistry.add_class` seeds new classes with their registry default (and persists it to the DB row).
- A boot migration converts existing libraries' rows for these classes **only when they are still in the pristine `chamfer` / NULL state**; an explicit signature `bbox_ratio` set in the UI is preserved.
- Tests: updated default-strategy, migration-column, and API class-listing tests; added a signature-default test and an override-preservation test.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `template-library`: ADDS a requirement that the built-in large-outline classes (`Substrate` / `LidOuter` / `LidInner`) seed with a signature match-strategy default. The existing "Default class seeding" requirement (which classes exist and their order) is unaffected.

## Impact

- **Code**: `app/library.py` — one registry constant + `add_class` default lookup + one boot-migration `UPDATE`.
- **Tests**: `tests/test_library.py` (2 updated, 2 added), `tests/test_api.py` (1 updated). Full suite 537 passing; 1 unrelated pre-existing flake (`test_save_match_post_with_missing_parsed_file_returns_synchronous_error`, global `_jobs`-state leak, fails on `main` too).
- **API**: no schema change. `GET /api/libraries/{id}/classes` now reports `signature` / `0.0001` for these three classes.
- **Data**: existing libraries' `classes` rows for these three classes are converted on next boot (only if still `chamfer`/NULL). Idempotent.
- **Behaviour**: prematch / Scan All for these classes now uses signature (size+aspect) matching. Operators who want chamfer or a different `bbox_ratio` set it per class in the UI; a non-default signature config is preserved across reboots.

## Why

The `disambiguate-bga-fiducial-by-view` change retired the neighbour-density class-arbitration heuristic in favour of mutually exclusive view constraints, leaving `CLASS_ARBITRATION_GROUPS` empty and `arbitrate()` a no-op pass-through. That left the entire arbitration subsystem as dead code: the `app/class_arbitration.py` module, the registry/dataclasses in `app/library.py`, the three no-op `arbitrate()` call sites, the `canvas.js` membership mirror, and a full test module — all reachable but inert. This change removes it. There is **no behaviour change**: every call site was already a pass-through over an empty registry.

## What Changes

- Delete `app/class_arbitration.py` (arbitrate / pool_instances / derive_pitch / count_neighbors / classify / population fallback / the `arbitrate_for_*` entry points).
- Remove from `app/library.py`: `ArbitrationGroup`, `MinNeighbors`, `MaxNeighbors`, `NeighborRule`, `CLASS_ARBITRATION_GROUPS`, `_build_arbitration_index`, `_ARBITRATION_INDEX`, `arbitration_group_for`.
- Remove the three no-op `arbitrate()` call sites (`jobs.py` prematch + save-match, `main.py` scan-all) and the `arbitration_counts` field from the save-match worker's return payload. The `out` dict now flows straight from `split_matches_by_side` to persistence/collapse.
- Relocate `_parse_key` (the only still-needed helper — used by the by-class collapse) to `app/side_regions.py` as the public `parse_match_key`, the natural inverse of the keys `split_matches_by_side` emits.
- Remove `canvas.js` `CLASS_ARBITRATION_MEMBERS` + `isArbitrationMember()` + the commit-time "full scan-all re-run for arbitration members" branch (view constraints are reproducible client-side via `applyViewConstraintsToScanAll`, so the incremental merge is correct).
- Delete `tests/test_class_arbitration.py`; remove arbitration tests/asserts from `test_library.py`, `test_match_json_constraints.py`, and the arbitration-members drift test from `test_canvas_constants.py`.
- Retire the `class-arbitration` capability spec.

## Capabilities

### Removed Capabilities

- `class-arbitration`: the neighbour-density disambiguation subsystem is removed. Same-geometry classes (BGABall/FiducialCircle) are disambiguated by view constraints (`template-library`), not density.

## Impact

- **Code**: `app/class_arbitration.py` deleted; `app/library.py`, `app/jobs.py`, `app/main.py`, `app/side_regions.py`, `app/static/canvas.js` edited.
- **Tests**: `tests/test_class_arbitration.py` deleted; `test_library.py` / `test_match_json_constraints.py` / `test_canvas_constants.py` updated. Full suite 508 passing; 1 unrelated pre-existing flake (`test_save_match_post_with_missing_parsed_file_...`, fails on `main` too).
- **API**: the async save-match job result no longer includes `arbitration_counts` (it was always `{}` after the registry was emptied). No other contract change.
- **Behaviour**: none — the removed code was already a no-op over the empty registry.
- **Specs**: `class-arbitration` capability removed; `template-library` already documents the view-based disambiguation (in `disambiguate-bga-fiducial-by-view`).

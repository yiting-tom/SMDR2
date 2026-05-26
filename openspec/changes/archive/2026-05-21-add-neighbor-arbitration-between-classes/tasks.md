## 1. Registry & data model

- [x] 1.1 Add `ArbitrationGroup` dataclass, `NeighborRule` sum type
       (`MinNeighbors` / `MaxNeighbors`) to `app/library.py` with
       `__post_init__` validation: every member has a rule;
       `default_class ∈ members`; `pitch_multiplier > 0`;
       `min_population ≥ 0`.
- [x] 1.2 Add the module-level `CLASS_ARBITRATION_GROUPS: tuple[ArbitrationGroup, ...]`
       constant seeded with the BGA/Fiducial group from the spec.
- [x] 1.3 Add import-time validation that no class appears in two
       groups (raise `ValueError` naming the conflict).
- [x] 1.4 Add `library.arbitration_group_for(class_name) -> ArbitrationGroup | None`
       helper backed by a precomputed dict.
- [x] 1.5 ~~Add `FiducialCircle` to the default-class seed list~~ —
       already in `DEFAULT_CLASSES` (no-op; kept for spec traceability).

## 2. Arbitration algorithm

- [x] 2.1 Create `app/class_arbitration.py` with `arbitrate(out, shapes,
       groups, view_constraints) -> tuple[dict[str, list[list[str]]], dict[str, dict]]`
       returning the rewritten `out` and a per-group counts dict matching
       the response schema in the spec.
- [x] 2.2 Implement `pool_centroids(out, group, shapes)` — collects
       (view_prefix, original_class, instance_idx, centroid, handles)
       tuples for every member-class instance across every view prefix
       (including unprefixed).
- [x] 2.3 Implement `derive_pitch(centroids) -> float | None` using
       `scipy.spatial.cKDTree.query(k=2)` and `np.median`; return `None`
       when `len(centroids) < 2`.
- [x] 2.4 Implement `count_neighbors(centroids, radius) -> np.ndarray[int]`
       via `cKDTree.query_ball_tree` or `query_ball_point(... count_only=True)`,
       remembering to subtract 1 (self).
- [x] 2.5 Implement `classify(counts, group) -> list[str]` returning
       per-instance target class; apply rules in registry order,
       falling back to `group.default_class` if no rule matches.
- [x] 2.6 Implement `apply_population_fallback(targets, group)` that
       reassigns the whole pool to `default_class` when any member
       class would receive fewer than `group.min_population` instances.
- [x] 2.7 Implement re-keying: for each instance, build the new key
       `f"{prefix}.{snake_case_class}.{idx}"` (or unprefixed if no
       prefix). Re-validate via `library.is_allowed_view(new_class, prefix)`;
       drop on conflict and increment `dropped_by_view`.
- [x] 2.8 Re-number instance indices per `(prefix, new_class)` group
       deterministically — sort by the (view_prefix, original_class,
       original_instance_idx) tuple before assigning 0, 1, 2, …

## 3. Match-JSON integration

- [x] 3.1 In `app/main.py:save_match_json`, after the per-class loop and
       before `json.dump`, call `class_arbitration.arbitrate(out, shapes,
       CLASS_ARBITRATION_GROUPS, CLASS_VIEW_CONSTRAINTS)`.
- [x] 3.2 Add `"arbitration_counts": counts` to the response dict
       returned by `save_match_json`; default to `{}` when no groups
       were applicable.
- [x] 3.3 For each view-conflict drop produced by arbitration:
       decrement `side_counts[<original_prefix>]` (or `unassigned`) by 1
       and increment `side_counts["dropped"]` by 1. `total_matches`
       stays at the raw matcher-output count (it never reflected
       view-filter survivors). Clamp the bucket at 0 defensively.

## 4. Tests

- [x] 4.1 `tests/test_class_arbitration.py`: unit-test
       `derive_pitch` with a hand-crafted point set; assert median
       behaviour under a 4-corner outlier configuration.
- [x] 4.2 Unit-test `count_neighbors` with a 3×3 grid: expect 8/5/3
       neighbour counts at centre/edge/corner with a 1.5× radius.
- [x] 4.3 Unit-test `classify`: cover MinNeighbors-matching, MaxNeighbors-matching,
       and default-class fallback.
- [x] 4.4 Unit-test `apply_population_fallback`: trigger and non-trigger
       cases with `min_population = 8`.
- [x] 4.5 Integration test: build a synthetic `out` + `shapes` with a
       10×10 BGA grid in `bottom_view` plus 4 corner fiducials in
       `top_view`; assert `arbitrate` returns 100 BGA + 4 fiducials
       with no handle overlap.
- [x] 4.6 Integration test: file with only 4 corner fiducials (no BGA
       grid) → all 4 land in `FiducialCircle` after population fallback.
- [x] 4.7 Integration test: view-conflict reassignment (instance moves
       to a class whose view rule disallows its prefix) — assert the
       instance is dropped and counted in `dropped_by_view`.
- [x] 4.8 Determinism test: run `arbitrate` twice with shuffled input
       order; assert outputs are equal.
- [x] 4.9 `tests/test_library.py`: seed-presence assertion mirroring
       `test_class_view_constraints_seed_entries` — verify the BGA/Fiducial
       group exists with the expected rules.
- [x] 4.10 `tests/test_library.py`: assert constructing an
       `ArbitrationGroup` with a missing rule or out-of-set
       `default_class` raises `ValueError`.
- [x] 4.11 `tests/test_library.py`: assert two groups sharing a member
       fails at registry-build time.
- [x] 4.12 End-to-end test in `tests/test_match_json_constraints.py` (or
       new sibling) wiring through `save_match_json`: assert the response
       includes `arbitration_counts` with the documented shape.

## 5. Hygiene & docs

- [x] 5.1 No UI surface consumes `CLASS_ARBITRATION_GROUPS` in v1 — the
       arbitration step runs purely server-side inside
       `save_match_json`. JS drift-mirror block deferred until a UI
       affordance is added; design.md Open Questions already notes this.
- [x] 5.2 README.md Match-JSON endpoint table updated to call out the
       `arbitration_counts` field in the `POST /match-json` response.
- [x] 5.3 Full test suite green minus 3 pre-existing failures on this
       branch (unrelated to arbitration: `test_circle_entity_emits_circle_primitive`,
       `test_align_within_scale_tolerance`,
       `test_legacy_template_does_not_use_fast_path` — confirmed via
       `git stash`). Ruff: 0 new violations; the 6 existing warnings
       predate this change.

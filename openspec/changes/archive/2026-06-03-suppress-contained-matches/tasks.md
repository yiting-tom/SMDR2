## 1. Suppression core (`app/side_regions.py`)

- [x] 1.1 Add module-level `CONTAINED_SUPPRESSION_ENABLED = True`, read live (bare global lookup) inside the function so an in-process attribute set takes effect on the next call. Source-level constant — NOT wired into the developer-override store (changing it in a running deployment needs a restart); a true no-restart dev-panel toggle is a deferred follow-up.
- [x] 1.2 Implement `suppress_contained_matches(out: dict[str, list[list[str]]]) -> dict[str, list[list[str]]]`: when the flag is `False`, return `out` unchanged; otherwise pool instances by snake class via `parse_match_key` (ignore view prefix; leave unparseable keys untouched), collapse exact-duplicate handle sets to one representative (tie-break: more handles, then earliest template `idx`, then deterministic key/pos order), then drop any representative whose handle set is a proper subset of another representative's (evaluated non-iteratively over the full representative set).
- [x] 1.3 Rebuild and return the dict preserving original key order and within-key instance order minus removed instances; drop keys that become empty. Skip empty (no-handle) instances defensively.

## 2. Wire into persisted Match JSON build (`app/jobs.py:_save_match_worker`)

- [x] 2.1 After the per-class matching loop + `split_matches_by_side` populate `out` (replacing the "no post-match arbitration step" comment block), call `out = suppress_contained_matches(out)`.
- [x] 2.2 Recompute the response from the post-suppression `out`: keep `total_matches` as the raw matches found; recompute `side_counts` top/bottom/side/unassigned by parsing each surviving key's prefix while retaining the `dropped` count accumulated during the split; add `suppressed_count = pre_suppress_instances - post_suppress_instances`. `template_keys` already derives from `out`. (Invariant: `total_matches == survivors + dropped + suppressed_count`.)

## 3. Tests

- [x] 3.1 Create `tests/test_contained_match_suppression.py` (pure unit) covering every spec scenario: proper subset dropped; mask-only-only survives; disjoint kept; partial-overlap kept; identical sets collapse to earliest idx; containment chain `A⊊B⊊C`; cross-view-prefix same-class suppressed with superset keeping its key; cross-class never suppressed; disabled-flag pass-through; default flag `True`; determinism; per-class union invariance; unparseable/empty pass-through. (14 tests)
- [x] 3.2 Add a `_save_match_worker` integration test (mirror `tests/test_match_json_constraints.py` fakes): a library with a mask-only + mask+body same-class template on a mask+body DXF → written `match/{id}.json` has the superset once and not the subset; assert `suppressed_count`, `total_matches`, and `side_counts` agree with the written file.
- [x] 3.3 Add a scan-all union-invariance regression test: assert `scan_all`'s `by_class` is identical with the subset instance present vs absent (locks the D4 proof that previews need no change).

## 4. Verify

- [x] 4.1 Run the full backend test suite (`pytest`) and confirm green, including a deterministic-order run to guard against the known `pytest-randomly` ordering sensitivity. (536 passed deterministic + 536 passed randomized.)
- [x] 4.2 `openspec validate suppress-contained-matches` passes. (Manual sanity check on a real BD/POD + SBT DXF is left for the operator.)

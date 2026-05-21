## 1. Confirm external module boundary

- [x] 1.1 Confirm with the external team: exact in-tree module path (e.g., `app.external_rule_check`) + function name + signature (expected `(product_id: str, bundle_dir: str) -> dict`). **Decision**: `app.external_rule_check.check_rules(product_id, bundle_dir) -> dict`.
- [x] 1.2 If the team hasn't committed their module yet, land a stub at the agreed path that raises `NotImplementedError("external rule module not yet committed")` so the adapter import resolves and CI is green. Remove the stub when their real module lands.
- [x] 1.3 Confirm whether their module needs any new dev-only test deps (it shouldn't — they ship inside our package — but check `pyproject.toml` after they merge). N/A until they merge.

## 2. Bundle materialisation helper

- [x] 2.1 Extract bundle-on-disk assembly from `app/drc_bundle.py:build_bundle` into a sibling `build_bundle_dir(product, files, dst_dir)` that writes `manifest.json` + `dxfs/<file_id>.dxf` + `match/<file_id>.json` to a directory (no zip). Reuse `build_manifest` unchanged.
- [x] 2.2 Add a `with materialise_bundle(product, files) as bundle_dir:` context manager that creates a `tempfile.TemporaryDirectory`, calls `build_bundle_dir`, yields the path, and cleans up on exit.
- [x] 2.3 Unit-test `build_bundle_dir`: directory contents match `build_bundle`'s zip contents (same manifest JSON, same file bytes, same paths).

## 3. Adapter rewrite (`app/rule_check.py`)

- [x] 3.1 Delete the three mock rules (Rule1, Rule2, Rule3) and the helpers used only by them: `_collect_segments`, `_point_to_segment_dist`, `_shortest_distance`, `_first_match_handles`, `_all_match_groups`, `_all_handles_for_prefix`, `_count_for_prefix`, `_iter_class_groups`, `_resolve_file_id`, `_strip_handle_prefixes`, `_origin_label`, `_parse_key`, `_split_handle_prefix`, `_HANDLE_PREFIX_RE`, the `_VIEW_PREFIXES` constant, and the `SUBSTRATE_TO_SMD_MIN_DIST` / `SMD_TO_SUBSTRATE_MAX_DIST` thresholds.
- [x] 3.2 Replace `check_rules` with a thin adapter: import the external function from the in-tree module agreed in 1.1, call it with `(product_id, str(bundle_dir))`, return its result verbatim after envelope validation.
- [x] 3.3 Implement `_validate_envelope(result)` that raises a clear `ValueError` (or a dedicated `RuleCheckOutputError`) for each invariant in the spec: handle without file_id; from-and-tol both null; to without from; empty text on a present sub-rule.
- [x] 3.4 Update the module docstring to describe the adapter role and link to the spec's "External rule function contract" requirement.
- [x] 3.5 Update the `RuleResult` / `SubRule` type aliases to reflect the new shape (single-handle from/to + tol/tol_text).

## 4. Worker rewrite (`app/jobs.py`)

- [x] 4.1 Rewrite `_rule_check_worker(product_id, role_specs, dst, ...)` to materialise the bundle via the new context manager and call `check_rules(product_id, bundle_dir)`. Delete the per-role merge loop entirely (no more `<file_id[:8]>:` prefixing inside the worker).
- [x] 4.2 Adjust `submit_rule_check` / the `_rule_check_worker` call site: the worker now needs the product + role-attached file records (or paths) — not the merged `role_specs` shape. Either change the spec shape or have the worker re-fetch records from disk; pick whichever keeps the parent process light. **Decision**: worker re-fetches from `PRODUCT_STORE` / `FILE_STORE` given product_id + file_ids; parent only passes ids.
- [x] 4.3 Drop `dev_overrides_snapshot` from `_rule_check_worker` and `submit_rule_check`. Dev parameter overrides are not relevant to externalised rules.
- [x] 4.4 Ensure the temp bundle directory is removed even when `check_rules` raises (rely on the context manager).
- [x] 4.5 `pass_count` / `fail_count` accounting stays unchanged — `pass` is still a bool on each rule.

## 5. Endpoint adjustment (`app/main.py`)

- [x] 5.1 Update `run_product_rule_check` to call `submit_rule_check` with the new arguments (whatever shape 4.2 settles on). Drop any code path that built the old `role_specs` payload.
- [x] 5.2 Smoke-check the docstrings / comments in `app/main.py` and `app/jobs.py` that still mention "merge", "handle namespacing", or "in-memory bundle" — rewrite or delete them.

## 6. Viewer rewrite (`app/static/canvas.js`)

- [x] 6.1 Change `focusedSubRule` initialisation in `focusSubRule` to store `from`, `to`, `tol` as single handle strings (or null) and `tol_text` as a string-or-null. Drop the array fallbacks (`sub.from || []`).
- [x] 6.2 Rewrite `drawFocusedSubRule` to collect highlight handles from whichever of `from`/`to`/`tol` are non-null, then draw a dashed segment between `from` and `to` only when both are present. Endpoints come from a single-handle variant of `shortestSegmentBetween` (vertex-vs-edge perpendicular-foot search) so the line stays pinned to the closest edges, not the bbox centres.
- [x] 6.3 Rewrite `drawFocusedLabel` to render `text` at the from↔to midpoint when both are present, adjacent to `from` when only `from` is present, and to render `tol_text` adjacent to `tol` (independently) when set.
- [x] 6.4 Delete `shortestSegmentBetween`, `_collect_segments`, `_point_to_segment_dist` (or their JS equivalents) and any helpers in `canvas.js` that exist only to serve the old multi-handle from/to. **Revised**: kept the shortest-segment search (single-handle variant) — perpendicular-foot endpoints land on the visually closest edges, bbox centres put the line through entity interiors. `closestPointOnSegment` import restored.
- [x] 6.5 Update the rule-panel hover behaviour in `dashboard.js` / `canvas.js` to highlight the union of `from`/`to`/`tol` (whichever are set) instead of the old `[...from, ...to]` spread. (No-op: in the current build `hoverSet`/`pinnedSet` are not populated from sub-rules; the click→`focusSubRule` path already uses the new shape.)

## 7. Tests

- [x] 7.1 In `tests/test_rule_check.py`, delete the Rule1 / Rule2 / Rule3 scenario tests (`test_rule1_*`, `test_rule2_*`, `test_rule3_*`, and the geometry helpers that fed them).
- [x] 7.2 Add `_check_envelope(result)` to validate the new shape (single from/to, optional tol/tol_text, file_id-required-when-handle-set invariant, at-least-one-of-from-or-tol invariant).
- [x] 7.3 Add `test_adapter_forwards_bundle_path`: monkey-patch the external function, call `check_rules("p", "/tmp/fake-bundle")`, assert the external function got that exact path and the adapter returned its result verbatim.
- [x] 7.4 Add `test_adapter_rejects_handle_without_file_id` / `test_adapter_rejects_no_from_or_tol` / `test_adapter_rejects_to_without_from` / `test_adapter_rejects_empty_text_when_sub_rules_present` — each monkey-patches the external function to return a malformed envelope and asserts the adapter raises.
- [x] 7.5 In `tests/test_rule_check_job.py`, monkey-patch both the external function and the bundle materialiser so the worker path runs hermetically. Assert: bundle directory is materialised, external function is called with the bundle path, result is persisted to `rule_check_path(product_id)`, bundle directory is removed after the call (success and failure paths). (Direct-worker tests use monkey-patched `app.rule_check._external_check_rules`; HTTP-level tests verify the stub-raise → job error path until the external module ships.)
- [x] 7.6 Add `tests/test_drc_bundle.py::test_build_bundle_dir_matches_zip_contents` — read both back, compare.
- [x] 7.7 Run `pytest -x` and fix the broken tests that reach into deleted helpers. (37/37 touched tests pass; 3 pre-existing failures in `test_dxf.py` / `test_matching*.py` confirmed unrelated by stash-and-rerun.)

## 8. Spec & docs

- [ ] 8.1 Apply the spec delta in `openspec/changes/externalize-rule-check/specs/design-rule-checking/spec.md` to `openspec/specs/design-rule-checking/spec.md` (handled at archive time by `openspec archive`). Pending — happens at `/opsx:archive`.
- [x] 8.2 Update `openspec/specs/design-rule-checking/INTEGRATION.md` if it references the old `check_rules(product_id, dxfs_by_role)` signature or the in-tree mock rules. Section 6 + Section 10 rewritten to point at the new contract and the new RuleChecking JSON shape.
- [x] 8.3 Update or remove any code comment in `app/jobs.py` / `app/main.py` / `app/rule_check.py` that mentions the merge step or the `<file_id[:8]>:` prefix in the rule-check path.

## 9. Deploy hygiene

- [x] 9.1 Add a one-time deploy step (release notes line + a `data/rule_check/*.json` wipe in the deploy script if one exists) so stale rule_check artefacts in the old shape are not served by the dashboard after the cutover. → `openspec/changes/externalize-rule-check/DEPLOY_NOTES.md`.
- [x] 9.2 Verify the dashboard handles "no rule check yet" gracefully when a product previously had a result but the file is now missing (this is the post-wipe state). Already handled — `app/main.py` computes `rule_check_available = rule_check_path(p.id).exists()` per request, and the dashboard's button text + `GET /api/products/{pid}/rule-check` 404 path treat absent files as "not yet run".

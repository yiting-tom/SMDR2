## 1. Spec the merge contract

- [x] 1.1 Add a new requirement "Per-role bundle merging and handle prefix" to `openspec/specs/design-rule-checking/spec.md` capturing: the `RoleBundle` field set (`file_id`, `dxf_path`, `file_ids`, `dxf_paths`, `match_json`, `entity_shapes`), the merge rules for `match_json` (concatenate matches under the same key) and `entity_shapes` (union of dicts), and the prefix invariant — handles get a `{file_id[:8]}:` prefix exactly when `len(role_files) > 1`. Include scenarios for: single-file role (no prefix), multi-file role (every handle prefixed), opaque-handle invariant (rule helpers never inspect the prefix).

## 2. Add the prefix-split helper

- [x] 2.1 Add `_split_handle_prefix(h: str) -> tuple[str | None, str]` to `app/rule_check.py`, alongside the existing handle helpers. Behaviour: if `h` matches `r"^[0-9a-f]{8}:(.+)$"` return `(prefix, raw)`, else return `(None, h)`. Pure, no I/O, docstring explains when prefix is `None`.
- [x] 2.2 Add a unit test in `tests/test_rule_check.py`: `_split_handle_prefix("a3f12b9c:7AF") == ("a3f12b9c", "7AF")` and `_split_handle_prefix("7AF") == (None, "7AF")`. One more for an 8-char hex-only input (`"a3f12b9c"` with no colon) to confirm it's returned as `(None, "a3f12b9c")` since there's no separator.

## 3. Extend the test bundle helpers

- [x] 3.1 In `tests/test_rule_check.py`, extend `_bundle(match_json, shapes)` to accept optional `file_ids` and `dxf_paths` (each defaults to `["unit_test"]` / `["unit_test.dxf"]`); the returned dict gains those keys plus the singular fallbacks (`file_id = file_ids[0]`, `dxf_path = dxf_paths[0]`).
- [x] 3.2 Add `_multi_bundle(per_file: list[tuple[match_json, shapes_dict]]) -> dict` that takes one `(mj, shapes)` pair per file and returns a merged bundle with `{file_id[:8]}:` prefixes on every handle, matching the production merge in `run_product_rule_check`. Use synthetic 8-char lowercase-hex file_ids (`aaaa0001`, `aaaa0002`, …) — the regex in `_split_handle_prefix` is strict-hex, so non-hex synthetic ids like `"file0001"` would silently fail to round-trip.
- [x] 3.3 Add a smoke test exercising `_multi_bundle`: build a 2-file BD bundle with one substrate handle from each file, confirm `check_rules` runs on it (envelope test) and that every handle in the merged `match_json` carries an 8-hex-char prefix + colon.

## 4. Update the skill doc

- [x] 4.1 In `skill/add-rule/SKILL.md` "Input shape: `dxfs_by_role`" section, update the Python example to include `"file_ids": ["abc12345", "def67890"]` and `"dxf_paths": [...]`. Add a comment that `file_id` / `dxf_path` (singular) are first-file fallbacks for backwards-compat.
- [x] 4.2 Add a new section "## Multi-DXF per role" between "Reusable helpers" and "Steps". Cover: when a role has ≥ 2 DXFs (additive uploads, top/bottom siblings), the handle-prefix convention `{file_id[:8]}:{raw_handle}`, why rules normally don't need to care (helpers treat handles as opaque), and the `_split_handle_prefix` escape hatch. Include a worked example: "cross-file count comparison rule" — iterate `bundle["file_ids"]`, group handles by `_split_handle_prefix(h)[0]`, compare counts.
- [x] 4.3 In the "Common pitfalls" section, add: "Parsing the prefix yourself — use `_split_handle_prefix`. The prefix scheme is documented in the `design-rule-checking` capability spec; do not inline a regex."
- [x] 4.4 Update the "Required inputs from the human" section's role-handling note (currently item 3) to mention the multi-DXF-per-role case — most rules don't change, but ones that need to fan out per file should ask the human "should this rule run per-file, or aggregate across the role's siblings?"

## 5. Verification

- [x] 5.1 `uv run pytest tests/test_rule_check.py -v` — green, including the new prefix-split and multi-bundle smoke tests.
- [x] 5.2 `uv run pytest tests/` — full suite still green; no regression in existing rules.
- [x] 5.3 `openspec validate document-multi-dxf-rule-bundle --strict` — passes.
- [x] 5.4 Manual read-through: the updated `skill/add-rule/SKILL.md` is still followable end-to-end for someone authoring a single-file rule (the new section is opt-in reading).

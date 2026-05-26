## Why

Commit `c01a923 Product files: allow multiple DXFs per (product, role)`
plus the merge logic in `run_product_rule_check` (`app/main.py:917-954`)
changed the shape that `check_rules` actually receives: each
`dxfs_by_role[role]` dict now carries `file_ids: list[str]` and
`dxf_paths: list[str]` alongside the legacy singular `file_id` /
`dxf_path`, and when a role holds ≥ 2 DXFs every handle is prefixed
with `{file_id[:8]}:` so handles from different files can't collide
inside the merged `match_json` / `entity_shapes`.

None of this is documented:

- `skill/add-rule/SKILL.md` (the contract new rule authors read) still
  shows the pre-`c01a923` single-file bundle and treats `dxf_path` as
  the path to "the" DXF — true for single-file roles, misleading for
  multi-file roles.
- `openspec/specs/design-rule-checking/spec.md` has no requirement
  capturing the per-role merge contract, so the prefix convention
  exists only in `main.py` and silently in `app/rule_check.py`'s
  "handles are opaque strings" implementation.
- `tests/test_rule_check.py`'s `_bundle(match_json, shapes)` helper
  builds bundles that won't catch any regression introduced by the
  prefix scheme — every existing rule test is single-file.

Existing rules still pass tests because `_first_match_handles` /
`_all_match_groups` / `_shortest_distance` treat handles as opaque
strings, so prefix flows through transparently. The risk is **future**
rules that need to know which DXF a handle came from (e.g., "BGA count
on BD's top sibling must equal BGA count on BD's bottom sibling") —
without a helper or docs, the author either reinvents the prefix
parser or assumes prefixes never happen.

## What Changes

- **ADDED spec requirement** in `design-rule-checking`: "Per-role
  bundle merging and handle prefix" — captures the exact merge rules
  in `run_product_rule_check`, the prefix convention, the invariant
  that prefix is only applied when `len(role_files) > 1`, and the
  invariant that all rule helpers treat handles as opaque strings.
- **NEW helper** `_split_handle_prefix(h) -> tuple[str | None, str]`
  in `app/rule_check.py` so rules that need file-of-origin can ask
  for it through a documented API rather than parsing the prefix
  themselves. Single-file roles' handles return `(None, h)`.
- **Updated `skill/add-rule/SKILL.md`**:
  - Input-shape table gains `file_ids` and `dxf_paths` rows with the
    "first-file fallback" note for the singular fields.
  - New "Multi-DXF per role" section showing the prefix convention,
    the new `_split_handle_prefix` helper, and a worked example for
    "compare counts across this role's sibling DXFs".
  - Common-pitfalls section gains an entry: "don't parse the prefix
    yourself — use `_split_handle_prefix`".
- **Updated `tests/test_rule_check.py`**: extend `_bundle` to accept
  an optional `file_ids` / `dxf_paths` plus a small `_multi_bundle`
  helper that builds a merged bundle with two prefixed file_ids, so
  future rule tests can exercise the cross-file case without
  hand-rolling the prefix machinery. Add one round-trip test:
  `_split_handle_prefix(prefix + h) == (file_id[:8], h)` for both
  single- and multi-file shapes.

## Capabilities

### Modified Capabilities
- `design-rule-checking`: add a requirement documenting the
  per-role bundle merge + handle-prefix contract that ships in
  `run_product_rule_check`.

## Impact

- **Docs (`skill/add-rule/SKILL.md`)**: input-shape table updated,
  new "Multi-DXF per role" section + pitfall entry. Existing rule
  authors who only care about the single-file shape keep their
  current mental model — the section is additive guidance for the
  multi-file case.
- **Code (`app/rule_check.py`)**: one new module-private helper
  (`_split_handle_prefix`). No change to `check_rules`, no change to
  any existing rule. Zero behaviour change.
- **Tests (`tests/test_rule_check.py`)**: `_bundle` signature gains
  optional kwargs (default behaviour unchanged); new `_multi_bundle`
  helper; one new test for the prefix split.
- **Spec (`openspec/specs/design-rule-checking/spec.md`)**: one
  ADDED requirement after archive.
- **No backend behaviour change.** The merge contract already ships;
  this change documents it and provides a typed escape hatch for
  rules that need to look through the prefix.

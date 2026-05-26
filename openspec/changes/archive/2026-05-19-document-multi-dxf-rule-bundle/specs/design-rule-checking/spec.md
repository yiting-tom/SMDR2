## ADDED Requirements

### Requirement: Per-role bundle merging and handle prefix

`run_product_rule_check` SHALL build the `dxfs_by_role: dict[str, dict]`
argument passed to `check_rules` by walking every `FileRecord`
attached to the product and merging per role. For each role, the
resulting bundle dict SHALL carry these keys:

| Key | Type | Meaning |
|---|---|---|
| `file_id` | `str` | First file_id in `file_ids` (first-file fallback for single-file callers) |
| `dxf_path` | `str` | First dxf_path in `dxf_paths` (first-file fallback) |
| `file_ids` | `list[str]` | Every file_id in this role, in `_group_files_by_role` order (multi → top → bottom → side) |
| `dxf_paths` | `list[str]` | Parallel to `file_ids` — the on-disk DXF path for each |
| `match_json` | `dict[str, list[list[str]]]` | Concatenation of every per-file Match JSON read from `data/match/{file_id}.json`, merged under the same `<class>.<index>` keys |
| `entity_shapes` | `dict[str, EntityShape]` | Union of every per-file `entity_shapes` dict |

The handle-prefix invariant SHALL hold:

- When the role has exactly one file (`len(file_ids) == 1`), every
  handle in `match_json` and every key in `entity_shapes` SHALL be
  the raw DXF handle exactly as emitted by the parser. No prefix.
- When the role has ≥ 2 files (`len(file_ids) > 1`), every handle in
  `match_json` and every key in `entity_shapes` SHALL be prefixed
  with `f"{file_id[:8]}:"` — the first 8 hex characters of the
  source file's `file_id` followed by a colon. The prefix SHALL be
  applied in lockstep across `match_json` and `entity_shapes` so the
  two stay consistent (a prefixed handle in `match_json` always
  resolves in `entity_shapes`).

Rule logic and the helper functions in `app/rule_check.py`
(`_first_match_handles`, `_all_match_groups`, `_all_handles_for_prefix`,
`_count_for_prefix`, `_shortest_distance`) SHALL treat handles as
opaque strings — they SHALL NOT inspect, parse, or branch on the
prefix. Rules that genuinely need file-of-origin SHALL go through the
documented helper `_split_handle_prefix(h) -> tuple[str | None, str]`
(returns `(prefix_8_chars, raw_handle)` for prefixed handles,
`(None, h)` for unprefixed).

`_split_handle_prefix` SHALL recognise a prefix only when the leading
characters match `^[0-9a-f]{8}:` exactly. Strings that look like a
hex prefix but lack the colon separator SHALL return `(None, input)`.

#### Scenario: Single-file role exposes raw handles unprefixed
- **WHEN** a product has exactly one DXF under role `BD`
- **AND** `run_product_rule_check` builds the bundle for `BD`
- **THEN** `dxfs_by_role["BD"]["file_ids"]` has length 1
- **AND** every key in `dxfs_by_role["BD"]["entity_shapes"]` is a raw DXF handle (no `:` prefix)
- **AND** every handle in every match group of `dxfs_by_role["BD"]["match_json"]` is likewise unprefixed

#### Scenario: Multi-file role prefixes every handle
- **WHEN** a product has two DXFs under role `BD` with file_ids `a3f12b9c…` and `d4e5f678…`
- **AND** `run_product_rule_check` builds the bundle for `BD`
- **THEN** `dxfs_by_role["BD"]["file_ids"] == ["a3f12b9c…", "d4e5f678…"]` (length 2, in `_group_files_by_role` order)
- **AND** every key in `dxfs_by_role["BD"]["entity_shapes"]` starts with either `"a3f12b9c:"` or `"d4e5f678:"`
- **AND** every handle in every match group of `dxfs_by_role["BD"]["match_json"]` starts with the same set of prefixes
- **AND** for every prefixed handle `h` in `match_json`, `h` is also a key in `entity_shapes`

#### Scenario: First-file fallback fields point at the first file
- **WHEN** a product has two DXFs under role `BD` ordered as `[A, B]`
- **THEN** `dxfs_by_role["BD"]["file_id"] == "A"` (the singular field carries the first file_id)
- **AND** `dxfs_by_role["BD"]["dxf_path"]` is the on-disk path of file `A`
- **AND** `dxfs_by_role["BD"]["file_ids"]` and `dxfs_by_role["BD"]["dxf_paths"]` carry both files in order

#### Scenario: Rule helpers treat handles as opaque
- **WHEN** a rule calls `_first_match_handles(bundle["match_json"], "substrate")` on a multi-file bundle whose substrate handle is `"a3f12b9c:7AF"`
- **THEN** the helper returns `["a3f12b9c:7AF"]` exactly as stored
- **AND** the rule may pass that string straight into `_shortest_distance(bundle["entity_shapes"], ["a3f12b9c:7AF"], …)` and the lookup succeeds (the shape dict uses the same prefixed key)
- **AND** no helper anywhere in `app/rule_check.py` calls a regex or string-split against the handle

#### Scenario: `_split_handle_prefix` round-trips prefixed and bare handles
- **WHEN** `_split_handle_prefix("a3f12b9c:7AF")` is called
- **THEN** the return value is the tuple `("a3f12b9c", "7AF")`
- **WHEN** `_split_handle_prefix("7AF")` is called (a raw, unprefixed handle)
- **THEN** the return value is `(None, "7AF")`
- **WHEN** `_split_handle_prefix("a3f12b9c")` is called (8 hex chars but no colon)
- **THEN** the return value is `(None, "a3f12b9c")` — the colon separator is required for prefix recognition

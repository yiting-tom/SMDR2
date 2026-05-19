# design-rule-checking Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
### Requirement: RuleChecking JSON output shape

The function `check_rules(product_id, dxfs_by_role)` SHALL return a
dict keyed by rule name. Each value SHALL be a dict with the keys
`pass` (bool), `text` (string, the overall rule description / failure
reason), and `rules` (list of zero or more sub-rules).

Each sub-rule SHALL carry `part` (`"SBT"` | `"BD"` | `"POD"` |
`"RING"`), `from` (list of source DXF handles, raw / unprefixed),
`to` (list of target handles, raw / unprefixed), and `text`
(per-sub-rule message). Origin-scoped rules (every rule that can name
one source DXF — i.e. all built-in mock rules today) SHALL also
emit `file_id` carrying the full id of the DXF the sub-rule's
geometry lives in. The viewer and dashboard SHALL route on
`file_id` to pick which DXF to open / focus; `from` and `to` SHALL
resolve directly against that DXF's raw handle index, no prefix
parsing required by the consumer. Each sub-rule SHALL be a dict with the keys `part`
(`"SBT"` | `"BD"` | `"POD"` | `"RING"` — the role whose viewer
should render the annotation), `from` (list of source handles), `to`
(list of target handles), and `text` (per-sub-rule message; for
geometric rules this SHALL begin with an origin label of the form
`[<view>]` / `[file=<prefix>]` / `[<view> | file=<prefix>]` so the
coordinate space being checked is visible).

The viewer SHALL draw the shortest segment between any vertex pair
across `from` and `to`; `from` / `to` are lists rather than scalars
so a future rule MAY reference a group of entities on either side.

#### Scenario: Output is a dict of rule payloads
- **WHEN** `check_rules("p", {})` is called
- **THEN** the result is a dict where every value has keys `pass`, `text`, `rules`
- **AND** `pass` is a `bool`, `text` is a `str`, and `rules` is a `list`
- **AND** every entry in `rules` has the keys `part`, `from`, `to`, `text` with the documented types

### Requirement: Mock Rule1 — substrate-to-first-SMD distance

The mock checker SHALL implement Rule1: for every `(view, file_prefix)`
origin that contains BOTH a substrate match and an SMD-2T match in the
BD bundle, the shortest distance between the first substrate group
and the first SMD-2T group of that origin SHALL exceed 5 mm. Each
origin SHALL produce one sub-rule with `from` = substrate handles,
`to` = SMD-2T handles, and a `text` prefixed with the origin label
(`[top_view]`, `[bottom_view | file=aaaa0001]`, …). The rule SHALL
pass only when every checked origin passes.

#### Scenario: Far-apart substrate and SMD passes
- **WHEN** the substrate is at (0,0) and the first SMD is at (100,0)
- **THEN** Rule1 passes
- **AND** the sub-rule's `from` carries the substrate handles and `to` carries the first SMD's handles

#### Scenario: Close substrate and SMD fails
- **WHEN** the distance between substrate and first SMD is below 5 mm
- **THEN** Rule1 fails
- **AND** the description text contains the threshold value

#### Scenario: Missing substrate fails Rule1
- **WHEN** the Match JSON has no `substrate.*` / `<view>.substrate.*` entries
- **THEN** Rule1 fails with a description explaining what is missing

#### Scenario: Substrate and SMD-2T live in different views
- **WHEN** the BD bundle has substrate only in `top_view.substrate.*` and SMD-2T only in `bottom_view.smd_2t.*`
- **THEN** Rule1 fails with text indicating no shared view/DXF was found
- **AND** no sub-rule is emitted (no geometrically valid pair exists)

#### Scenario: Multi-view BD emits one sub-rule per shared origin
- **WHEN** the BD bundle has both substrate and SMD-2T in `top_view` AND in `bottom_view`
- **THEN** Rule1 emits exactly two sub-rules, one tagged with `top_view` and one with `bottom_view`
- **AND** the rule passes only if both pairs exceed the threshold

### Requirement: Rule check API and persistence

`POST /api/files/{file_id}/rule-check` SHALL load the file's persisted
Match JSON, invoke `check_rules` with the file's entity shapes, and
write the result to `data/rule_check/{file_id}.json`. The response
SHALL include the per-rule results and pass/fail counts. `GET` on the
same path SHALL return the most recently persisted result.

#### Scenario: Run rule check after Save Match
- **WHEN** the user has saved a Match JSON for a file
- **AND** invokes `POST /api/files/{id}/rule-check`
- **THEN** the response contains `rule_count`, `pass_count`, `fail_count`, `results`
- **AND** the result is persisted to `data/rule_check/{file_id}.json`

#### Scenario: Rule check before Save Match fails clearly
- **WHEN** the user invokes rule check on a file with no saved Match JSON
- **THEN** the API returns 400 with a message indicating Match JSON is missing

### Requirement: External DRC handoff bundle format

SMDR2 SHALL package every product's DRC inputs into a self-describing
**handoff bundle** the external rule-checking team consumes — a
directory (or zip) containing one `manifest.json` at the root plus
the DXF and Match JSON files referenced from it. (Production rule
checking is performed by a separate team; this requirement defines
the contract at that boundary.)

`manifest.json` SHALL conform to
`openspec/specs/design-rule-checking/drc-manifest.schema.json`
(JSON Schema draft 2020-12). The top-level object SHALL carry:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `bundle_version` | semver string | yes | Manifest contract version. Consumers MUST refuse a major version they do not understand. Initial value: `"1.0.0"`. |
| `product_id` | string | yes | SMDR2 internal product id, opaque to the consumer. |
| `product_name` | string | no | Human-readable name for cross-referencing reports. |
| `exported_at` | ISO 8601 string | no | Bundle generation time, second precision or finer. |
| `files` | array of `file_entry` | yes | Every (DXF, Match JSON) pair in the bundle. |

Every `file_entry` SHALL carry exactly these four keys:

| Field | Type | Meaning |
|---|---|---|
| `role` | `"SBT"` \| `"BD"` \| `"POD"` \| `"RING"` | Functional role this DXF plays. The same role MAY appear in multiple entries — that is the multi-DXF case. |
| `file_id` | lowercase-hex string | SMDR2's content-hash-derived file identifier. The first 8 hex chars are the canonical short form used internally. |
| `dxf` | bundle-relative POSIX path | The DXF file. MUST resolve to a regular file inside the bundle. |
| `match_json` | bundle-relative POSIX path | The Match JSON for this DXF. Keys are `<class>.<index>` or `<view>.<class>.<index>` (see "RuleChecking JSON output shape" requirement above for `<view>` values). |

Each Match JSON in the bundle SHALL be the file's own per-DXF Match
JSON exactly as persisted at `data/match/{file_id}.json` — **not**
the merged role-bundle form produced internally by
`run_product_rule_check`. Handles SHALL NOT carry the
`<file_id[:8]>:` prefix that the internal merge applies; the external
team's per-file consumption keeps every DXF in its own coordinate
space without needing to know the prefix scheme.

Within-file view scoping (top/bottom/side) SHALL remain encoded in
the Match JSON key prefix; no separate side-region rect data is
required in the bundle.

#### Scenario: Single-DXF-per-role product
- **WHEN** a product has exactly one DXF under each of `SBT`, `BD`, `POD`, `RING`
- **THEN** `manifest.files` has length 4
- **AND** each role appears in exactly one entry
- **AND** every `dxf` and `match_json` path resolves to a file inside the bundle

#### Scenario: Multi-DXF-per-role product
- **WHEN** a product has two DXFs under `BD` (e.g., top + bottom siblings) and one each under `SBT`, `POD`, `RING`
- **THEN** `manifest.files` has length 5
- **AND** exactly two entries carry `role: "BD"` with different `file_id` values
- **AND** each entry's `match_json` is the per-DXF Match JSON with raw, unprefixed handles

#### Scenario: Match JSON handles are not pre-merged
- **WHEN** a consumer reads any Match JSON referenced from a `file_entry`
- **THEN** every handle in every match group SHALL be a raw DXF handle
- **AND** no handle SHALL begin with `^[0-9a-f]{8}:` (the internal merge prefix)

#### Scenario: Major version mismatch is refused
- **WHEN** a consumer reads a manifest whose `bundle_version` major component does not match a major version it implements
- **THEN** the consumer SHALL refuse to process the bundle and SHALL surface a version-mismatch error to its operator

### Requirement: Rule panel hover and pinned highlight

In the viewer, the rule-check panel SHALL highlight a rule's `handleIds`
on the canvas when the rule item is hovered (ephemeral) and pin them
when clicked (persistent until clicked again or another rule is
clicked). The pinned rule's card SHALL have a distinct visual indicator
(left border + tint). Closing the panel and re-running rule check SHALL
clear any pinned state.

#### Scenario: Hover highlights then clears
- **WHEN** the user hovers a rule row
- **THEN** the rule's handleIds are highlighted in yellow on the canvas
- **WHEN** the cursor leaves the row
- **THEN** the yellow highlight clears

#### Scenario: Click pins the highlight and marks the card
- **WHEN** the user clicks a rule row
- **THEN** the rule's handleIds remain highlighted after the cursor leaves
- **AND** the card shows a yellow left-border and tinted background

#### Scenario: Click again unpins
- **WHEN** the user clicks the already-pinned rule
- **THEN** the highlight clears and the card returns to its default style

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
| `match_json` | `dict[str, list[list[str]]]` | Concatenation of every per-file Match JSON read from `data/match/{file_id}.json`, merged under the same keys. Each key is either `<class>.<index>` (instance bbox-center outside every side-region rect, or the file has no side regions) or `<view>.<class>.<index>` where `<view>` ∈ {`top_view`, `bottom_view`, `side_view`} (assigned by `app/side_regions.py:split_matches_by_side`). Rule helpers SHALL recognise both shapes. |
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
`_count_for_prefix`, `_iter_class_groups`, `_shortest_distance`) SHALL
treat handles as opaque strings — they SHALL NOT inspect, parse, or
branch on the prefix. Rules that genuinely need file-of-origin SHALL go through the
documented helper `_split_handle_prefix(h) -> tuple[str | None, str]`
(returns `(prefix_8_chars, raw_handle)` for prefixed handles,
`(None, h)` for unprefixed).

Geometric rules SHALL scope distance comparisons by
`(view, file_prefix)` origin via `_iter_class_groups(match_json, class)`,
which yields `((view, file_prefix), handles)` for every match group of
the given class. Comparing two shapes from different origins is not
defined (different coordinate spaces on the page, or different DXFs)
and SHALL be reported as a no-pair / no-substrate failure rather than
silently merging the shapes.

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


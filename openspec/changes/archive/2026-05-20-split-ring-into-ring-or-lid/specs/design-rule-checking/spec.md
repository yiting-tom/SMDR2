## MODIFIED Requirements

### Requirement: RuleChecking JSON output shape

The function `check_rules(product_id, dxfs_by_role)` SHALL return a
dict keyed by rule name. Each value SHALL be a dict with the keys
`pass` (bool), `text` (string, the overall rule description / failure
reason), and `rules` (list of zero or more sub-rules).

Each sub-rule SHALL carry `part` (`"SBT"` | `"BD"` | `"POD"` |
`"RING"` | `"LID"`), `from` (list of source DXF handles, raw /
unprefixed), `to` (list of target handles, raw / unprefixed), and
`text` (per-sub-rule message). Origin-scoped rules (every rule that
can name one source DXF — i.e. all built-in mock rules today) SHALL
also emit `file_id` carrying the full id of the DXF the sub-rule's
geometry lives in. The viewer and dashboard SHALL route on `file_id`
to pick which DXF to open / focus; `from` and `to` SHALL resolve
directly against that DXF's raw handle index, no prefix parsing
required by the consumer. Each sub-rule SHALL be a dict with the keys
`part` (`"SBT"` | `"BD"` | `"POD"` | `"RING"` | `"LID"` — the role
whose viewer should render the annotation), `from` (list of source
handles), `to` (list of target handles), and `text` (per-sub-rule
message; for geometric rules this SHALL begin with an origin label of
the form `[<view>]` / `[file=<prefix>]` / `[<view> | file=<prefix>]`
so the coordinate space being checked is visible).

The viewer SHALL draw the shortest segment between any vertex pair
across `from` and `to`; `from` / `to` are lists rather than scalars
so a future rule MAY reference a group of entities on either side.

#### Scenario: Output is a dict of rule payloads
- **WHEN** `check_rules("p", {})` is called
- **THEN** the result is a dict where every value has keys `pass`, `text`, `rules`
- **AND** `pass` is a `bool`, `text` is a `str`, and `rules` is a `list`
- **AND** every entry in `rules` has the keys `part`, `from`, `to`, `text` with the documented types

#### Scenario: LID is a valid sub-rule part value
- **WHEN** a future LID-targeting rule emits a sub-rule with `part: "LID"`
- **THEN** the output validates against the RuleChecking schema and routes to the LID DXF's viewer

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
| `bundle_version` | semver string | yes | Manifest contract version. Consumers MUST refuse a major version they do not understand. Current value: `"1.1.0"` (minor bumped from `1.0.0` when the `role` enum widened to include `"LID"`). |
| `product_id` | string | yes | SMDR2 internal product id, opaque to the consumer. |
| `product_name` | string | no | Human-readable name for cross-referencing reports. |
| `exported_at` | ISO 8601 string | no | Bundle generation time, second precision or finer. |
| `files` | array of `file_entry` | yes | Every (DXF, Match JSON) pair in the bundle. |

Every `file_entry` SHALL carry exactly these four keys:

| Field | Type | Meaning |
|---|---|---|
| `role` | `"SBT"` \| `"BD"` \| `"POD"` \| `"RING"` \| `"LID"` | Functional role this DXF plays. The same role MAY appear in multiple entries — that is the multi-DXF case. A single product SHALL NOT contain entries with both `"RING"` and `"LID"` (mutual exclusion enforced at upload). |
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

#### Scenario: Single-DXF-per-role product (RING configuration)
- **WHEN** a product has exactly one DXF under each of `SBT`, `BD`, `POD`, `RING`
- **THEN** `manifest.files` has length 4
- **AND** each role appears in exactly one entry
- **AND** no entry carries `role: "LID"`
- **AND** every `dxf` and `match_json` path resolves to a file inside the bundle

#### Scenario: Single-DXF-per-role product (LID configuration)
- **WHEN** a product has exactly one DXF under each of `SBT`, `BD`, `POD`, `LID`
- **THEN** `manifest.files` has length 4
- **AND** exactly one entry carries `role: "LID"`
- **AND** no entry carries `role: "RING"`

#### Scenario: Multi-DXF-per-role product
- **WHEN** a product has two DXFs under `BD` (e.g., top + bottom siblings) and one each under `SBT`, `POD`, `RING`
- **THEN** `manifest.files` has length 5
- **AND** exactly two entries carry `role: "BD"` with different `file_id` values
- **AND** each entry's `match_json` is the per-DXF Match JSON with raw, unprefixed handles

#### Scenario: Match JSON handles are not pre-merged
- **WHEN** a consumer reads any Match JSON referenced from a `file_entry`
- **THEN** every handle in every match group SHALL be a raw DXF handle
- **AND** no handle SHALL begin with `^[0-9a-f]{8}:` (the internal merge prefix)

#### Scenario: Manifest never mixes RING and LID for one product
- **WHEN** a product's bundle is exported
- **THEN** `manifest.files` SHALL NOT contain both an entry with `role: "RING"` and an entry with `role: "LID"`
- **AND** the bundle export step SHALL fail loudly if upstream data violates this invariant

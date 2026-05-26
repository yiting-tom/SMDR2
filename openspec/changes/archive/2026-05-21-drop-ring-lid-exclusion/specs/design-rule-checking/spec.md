## MODIFIED Requirements

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
| `bundle_version` | semver string | yes | Manifest contract version. Consumers MUST refuse a major version they do not understand. Current value: `"1.2.0"` (minor bumped from `1.1.0` when `customer` / `customer_id` were added). |
| `product_id` | string | yes | SMDR2 internal product id, opaque to the consumer. |
| `product_name` | string | no | Human-readable name for cross-referencing reports. |
| `customer_id` | string | yes | SMDR2 internal `library_id` the product is bound to. Opaque to the consumer; stable across library renames. Mirrors how `product_id` is treated. |
| `customer` | string | no | Human-readable customer / library name. Omitted when the underlying library has no name; consumers that need a stable display name SHOULD key by `customer_id` and join against their own customer table. |
| `exported_at` | ISO 8601 string | no | Bundle generation time, second precision or finer. |
| `files` | array of `file_entry` | yes | Every (DXF, Match JSON) pair in the bundle. |

Every `file_entry` SHALL carry exactly these four keys:

| Field | Type | Meaning |
|---|---|---|
| `role` | `"SBT"` \| `"BD"` \| `"POD"` \| `"RING"` \| `"LID"` | Functional role this DXF plays. The same role MAY appear in multiple entries — that is the multi-DXF case. All five roles are independent; a single product MAY carry entries under any subset of them, including both `"RING"` and `"LID"` simultaneously. |
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

The bundle builder SHALL resolve `customer_id` directly from the
product's `library_id` and SHALL look up the human-readable
`customer` name via the library registry at export time. If the
referenced library cannot be resolved (e.g., it was deleted
out-of-band between product creation and bundle export), the
builder SHALL raise a `ValueError` naming the offending library id
rather than emit a manifest with a missing or guessed customer.

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

#### Scenario: Product carries both RING and LID
- **WHEN** a product has one DXF under `SBT`, `BD`, `POD`, `RING`, and `LID`
- **THEN** `manifest.files` has length 5
- **AND** the manifest validates against `drc-manifest.schema.json`
- **AND** the bundle export SHALL NOT raise on the RING+LID combination

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

#### Scenario: Manifest carries customer_id and customer for a named library
- **WHEN** a product is bound to a library `lib-1` named `"ACME Corp"`
- **AND** the bundle is exported
- **THEN** `manifest.customer_id` equals `"lib-1"`
- **AND** `manifest.customer` equals `"ACME Corp"`

#### Scenario: customer is omitted when the library has no name
- **WHEN** a product is bound to a library whose `name` is an empty string or unset
- **AND** the bundle is exported
- **THEN** `manifest.customer_id` is present
- **AND** the `customer` key SHALL either be omitted entirely or set to an empty string — consumers that care about display names MUST tolerate both forms

#### Scenario: Missing library raises at export time
- **WHEN** a product references a `library_id` that no longer resolves through the library registry
- **AND** the export endpoint is invoked
- **THEN** the bundle builder SHALL raise (no manifest is written)
- **AND** the raised error SHALL name the unresolved `library_id`


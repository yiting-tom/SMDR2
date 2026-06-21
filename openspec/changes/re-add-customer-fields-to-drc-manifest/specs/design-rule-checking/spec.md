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
| `bundle_version` | semver string | yes | Manifest contract version. Consumers MUST refuse a major version they do not understand. Current value: `"2.3.0"` (minor bumped from `2.2.0` when `customer` / `customer_id` were re-added with customer-entity semantics; `2.2.0` added the `"NovelLID"` role; `2.1.0` added `check_dam`). |
| `product_id` | string | yes | SMDR2 internal product id, opaque to the consumer. |
| `product_name` | string | no | Human-readable name for cross-referencing reports. |
| `customer_id` | string | yes | The product's customer grouping — `products.customer_id` (the customer dimension that sits above product), opaque to the consumer and stable across customer renames. Mirrors how `product_id` is treated. Defaults to `"uncategorized"` (a seeded customer) when the product was never assigned one. |
| `customer` | string | no | Human-readable customer name, resolved from the `customers` table at export time. Omitted when the customer cannot be resolved or has no name; consumers that need a stable display name SHOULD key by `customer_id` and join against their own customer table. |
| `exported_at` | ISO 8601 string | no | Bundle generation time, second precision or finer. |
| `check_dam` | boolean | no | Whether the operator enabled the DAM (encapsulation dam) check for this version. The consumer SHOULD run its DAM rules only when `true`. Absent ≡ `false` (pre-`2.1.0` bundles). Added in `2.1.0`. |
| `files` | array of `file_entry` | yes | Every (DXF, Match JSON) pair in the bundle. |

Every `file_entry` SHALL carry exactly these seven keys:

| Field | Type | Meaning |
|---|---|---|
| `role` | `"SBT"` \| `"BD"` \| `"POD"` \| `"RING"` \| `"LID"` \| `"NovelLID"` | Functional role this DXF plays. The same role MAY appear in multiple entries — that is the multi-DXF case. All six roles are independent; a single product MAY carry entries under any subset of them, including any of `"RING"` / `"LID"` / `"NovelLID"` simultaneously. `"NovelLID"` added in `bundle_version` `2.2.0`. |
| `file_id` | lowercase-hex string | SMDR2's content-hash-derived file identifier. The first 8 hex chars are the canonical short form used internally. |
| `dxf` | bundle-relative POSIX path | The DXF file. MUST resolve to a regular file inside the bundle. |
| `match_json` | bundle-relative POSIX path | The Match JSON for this DXF. Keys are `<class>.<index>` or `<view>.<class>.<index>` (see "RuleChecking JSON output shape" requirement above for `<view>` values). |
| `user_unit` | `"mm"` \| `"m"` \| `"inch"` \| `"cm"` \| `"um"` \| `"km"` \| `null` | The unit currently in force for the operator: the operator's unit-override if one is set, otherwise the effective unit derived from the applied auto-rescale factor. `null` only when no named unit applies (a unitless file the detector rescaled to a non-standard factor). `um` is ASCII (internal `μm` is translated); in practice `user_unit` never takes `km`. |
| `original_unit` | `"mm"` \| `"m"` \| `"inch"` \| `"cm"` \| `"um"` \| `"km"` \| `null` | The DXF's declared `$INSUNITS` header mapped to a unit string (`1`→`inch`, `4`→`mm`, `5`→`cm`, `6`→`m`, `7`→`km`, `13`→`um`). `null` when the header is unitless (`0`), an unsupported unit (e.g. `2` foot), or missing. Reports the header verbatim, independent of whether the rescaler acted on it. |
| `view` | array of `"top"` \| `"bottom"` \| `"side"` | The views the DXF carries, in canonical order top → bottom → side — one entry per side-region the operator has set (`top_view_rect` / `bottom_view_rect` / `side_view_rect`). `[]` when no side regions are set. Values correspond to the Match JSON key prefixes `top_view` / `bottom_view` / `side_view` (the `_view` suffix is dropped here). |

The unit fields SHALL draw their non-null values exclusively from the
vocabulary `{"mm", "m", "inch", "cm", "um", "km"}` — micrometre SHALL be
emitted as ASCII `"um"`, not the internal Unicode `"μm"`.

The `view` array SHALL list a view if and only if that view's side-region
rectangle is set on the file, ordered top → bottom → side regardless of the
order the operator painted them; it SHALL carry the view presence only, not the
rectangle geometry.

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

The bundle builder SHALL resolve `customer_id` directly from
`products.customer_id` and SHALL look up the human-readable `customer`
name via the `customers` table at export time. A missing or nameless
customer SHALL NOT raise: `customer_id` is always emitted (it defaults to
the seeded `"uncategorized"` customer), and the `customer` name SHALL be
omitted when it cannot be resolved.

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

#### Scenario: Manifest carries customer_id and customer for a named customer
- **WHEN** a product's `customer_id` is `"cust-1"` and the `customers` table has `cust-1` named `"ACME Corp"`
- **AND** the bundle is exported
- **THEN** `manifest.customer_id` equals `"cust-1"`
- **AND** `manifest.customer` equals `"ACME Corp"`

#### Scenario: customer name is omitted when the customer has no name
- **WHEN** a product's customer cannot be resolved or has an empty name
- **AND** the bundle is exported
- **THEN** `manifest.customer_id` is present
- **AND** the `customer` key is omitted (consumers key by `customer_id`)

#### Scenario: Unassigned product still carries a customer_id
- **WHEN** a product was never assigned a customer (`products.customer_id` is the default `"uncategorized"`)
- **AND** the bundle is exported
- **THEN** `manifest.customer_id` equals `"uncategorized"`
- **AND** the export does not raise

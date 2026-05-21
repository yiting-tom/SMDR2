## MODIFIED Requirements

### Requirement: Multiple DXFs per (product, role)

A `(product_id, dxf_role)` pair SHALL accept any number of DXFs
provided the `dxf_role` is in the valid role set
`("SBT", "BD", "POD", "RING", "LID")`. The five roles are independent;
uploads to one role SHALL NOT be rejected on the basis of files held
under any other role. Uploading to a product role SHALL be purely
additive by default: an unconditional
`POST /api/products/{pid}/files` adds a new file without evicting
siblings. To replace a specific file in a role (the common "swap"
flow), the client SHALL pass `replace_file_id` — that file is
detached from the product before the new one is registered. The
schema SHALL NOT enforce per-slot uniqueness via a unique index.

#### Scenario: Additive upload leaves siblings intact
- **WHEN** product `p1` has one DXF in role `SBT`
- **AND** the client posts another DXF to the same `(p1, SBT)`
  without `replace_file_id`
- **THEN** both files are stored against `(p1, SBT)`
- **AND** the original file is unchanged

#### Scenario: replace_file_id evicts the named file only
- **WHEN** product `p1` has DXFs `A` and `B` under role `SBT`
- **AND** the client posts a new DXF with `replace_file_id = A.id`
- **THEN** `A` is detached from the product (its row remains but loses
  `product_id`, `dxf_role`, `dxf_view`)
- **AND** `B` is unchanged
- **AND** the new DXF is bound to `(p1, SBT)`

#### Scenario: replace_file_id from another product or role is rejected
- **WHEN** the client posts to `(p2, SBT)` with `replace_file_id`
  referring to a file bound to `(p1, BD)`
- **THEN** the server returns HTTP 400 and the eviction does not happen

#### Scenario: Unknown dxf_role is rejected
- **WHEN** the client posts a DXF with `dxf_role = "TOPCAP"` (not in the valid set)
- **THEN** the server returns HTTP 400 and no row is created

#### Scenario: RING and LID coexist on the same product
- **WHEN** product `p1` has one DXF under `RING`
- **AND** the client posts a DXF with `dxf_role = "LID"`
- **THEN** the upload succeeds with HTTP 200
- **AND** the file is bound to `(p1, LID)`
- **AND** the RING file is unchanged

## ADDED Requirements

### Requirement: RING and LID are independent roles

A product SHALL be able to hold DXFs under `RING` and `LID`
simultaneously. The upload handler, the rule-check pipeline, and the
DRC bundle builder SHALL treat `RING` and `LID` as independent roles —
neither role SHALL be rejected, dropped, or merged on the basis of
files present under the other. Both roles SHALL appear as first-class
entries in `files_by_role_all`, in the `rule_check` `dxfs_by_role`
bundle, and in the DRC bundle's `manifest.files` list whenever files
are uploaded under them.

#### Scenario: First LID upload to a product with no RING is accepted
- **WHEN** product `p1` has zero files under `RING` and zero under `LID`
- **AND** the client posts a DXF with `dxf_role = "LID"`
- **THEN** the upload succeeds with HTTP 200
- **AND** the file is bound to `(p1, LID)`

#### Scenario: LID upload succeeds when product already has RING
- **WHEN** product `p1` has at least one DXF under `RING`
- **AND** the client posts a DXF with `dxf_role = "LID"`
- **THEN** the server returns HTTP 200
- **AND** `GET /api/products/{p1}` shows the new file under
  `files_by_role_all["LID"]`
- **AND** the existing RING file remains under `files_by_role_all["RING"]`

#### Scenario: RING upload succeeds when product already has LID
- **WHEN** product `p1` has at least one DXF under `LID`
- **AND** the client posts a DXF with `dxf_role = "RING"`
- **THEN** the server returns HTTP 200
- **AND** `GET /api/products/{p1}` shows the new file under
  `files_by_role_all["RING"]`
- **AND** the existing LID file remains under `files_by_role_all["LID"]`

#### Scenario: DRC bundle carries both roles when both are uploaded
- **WHEN** product `p1` has one DXF under `RING` and one under `LID`
- **AND** the client requests `GET /api/products/{p1}/drc-bundle`
- **THEN** the response is HTTP 200 with a zip
- **AND** the embedded `manifest.json`'s `files` list contains one
  entry with `dxf_role = "RING"` and one with `dxf_role = "LID"`

#### Scenario: Rule check feeds both roles into the bundle
- **WHEN** product `p1` has saved Match JSON for files under `SBT`,
  `BD`, `POD`, `RING`, and `LID`
- **AND** the client posts `/api/products/{p1}/rule-check`
- **THEN** the worker builds `dxfs_by_role` with all five role keys
  populated
- **AND** the job completes with HTTP 200 and writes
  `rule_check.json`

## REMOVED Requirements

### Requirement: RING / LID per-product mutual exclusion
**Reason**: Real packages routinely involve both a RING and a LID, so a
single product card needs to represent both halves. The XOR was a
policy guardrail from when downstream rule-check / DRC bundle could
not reason about both roles at once; that constraint no longer
applies. Downstream contracts (`manifest.files` is role-flat;
`dxfs_by_role` is role-keyed) already support both roles coexisting.

**Migration**: Existing products holding only one of RING / LID are
unaffected — they simply gain the ability to accept the opposite-role
upload going forward. Clients that previously surfaced the HTTP 409
as a user-facing error MUST drop that branch; both halves of the 4th
dashboard slot are now independently fillable. See the new
"RING and LID are independent roles" Requirement for the replacement
positive scenarios.

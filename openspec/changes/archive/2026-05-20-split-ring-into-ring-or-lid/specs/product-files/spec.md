## ADDED Requirements

### Requirement: RING / LID per-product mutual exclusion

A product SHALL hold DXFs under AT MOST ONE of the two roles `RING`
and `LID`; it SHALL NOT hold files under both simultaneously. The
first DXF uploaded to either slot fixes the product's choice for
subsequent uploads. Any upload that would create a sibling file in the
opposite slot SHALL be rejected with HTTP 409 and an error message
naming the file id that already occupies the conflicting slot. The
constraint SHALL be enforced in the upload handler (application
layer), not via a DB CHECK, so the rule lives next to the existing
`validate_role` call.

`replace_file_id` SHALL NOT be usable to cross from RING to LID or
vice versa: the existing per-`(product, role)` scoping (see
"replace_file_id from another product or role is rejected") already
rejects such requests, and this requirement does not relax that.

#### Scenario: First LID upload to a product with no RING is accepted
- **WHEN** product `p1` has zero files under `RING` and zero under `LID`
- **AND** the client posts a DXF with `dxf_role = "LID"`
- **THEN** the upload succeeds with HTTP 200
- **AND** the file is bound to `(p1, LID)`

#### Scenario: Second RING upload to a product already holding RING is accepted
- **WHEN** product `p1` already has one DXF under `RING` and zero under `LID`
- **AND** the client posts another DXF with `dxf_role = "RING"`
- **THEN** the upload succeeds (additive — same as any other multi-DXF role)

#### Scenario: LID upload is rejected when product already has RING
- **WHEN** product `p1` has at least one DXF under `RING`
- **AND** the client posts a DXF with `dxf_role = "LID"`
- **THEN** the server returns HTTP 409
- **AND** the response body names at least one of the conflicting RING file ids

#### Scenario: RING upload is rejected when product already has LID
- **WHEN** product `p1` has at least one DXF under `LID`
- **AND** the client posts a DXF with `dxf_role = "RING"`
- **THEN** the server returns HTTP 409
- **AND** the response body names at least one of the conflicting LID file ids

#### Scenario: Conversion requires explicit removal
- **WHEN** product `p1` holds one DXF under `RING`
- **AND** the client wants `p1` to become a LID product
- **THEN** the client MUST detach the RING file first (the API does NOT auto-convert)
- **AND** only after `p1` has zero RING files MAY a `LID` upload succeed

## MODIFIED Requirements

### Requirement: Multiple DXFs per (product, role)

A `(product_id, dxf_role)` pair SHALL accept any number of DXFs
provided the `dxf_role` is in the valid role set
`("SBT", "BD", "POD", "RING", "LID")` and the upload does not violate
the RING / LID per-product mutual-exclusion rule (see "RING / LID
per-product mutual exclusion"). Uploading to a product role SHALL be
purely additive by default: an unconditional
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

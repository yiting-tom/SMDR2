# product-files Specification (delta)

## MODIFIED Requirements

### Requirement: Multiple DXFs per (product, role)

A `(version_id, role)` pair SHALL accept any number of DXFs
provided the `role` is in the valid role set
`("SBT", "BD", "POD", "RING", "LID")`. The five roles are independent;
uploads to one role SHALL NOT be rejected on the basis of files held
under any other role. Uploading to a version's role SHALL be purely
additive by default: an unconditional
`POST /api/versions/{vid}/files` adds a new binding without evicting
siblings. To replace a specific file in a role (the common "swap"
flow), the client SHALL pass `replace_file_id` — that binding is
removed from the version before the new one is registered; the
underlying `files` row (content storage) is retained and MAY remain
bound to other versions. The schema SHALL NOT enforce per-slot
uniqueness via a unique index. Bindings SHALL be stored in the
`version_files` junction; the `files` table SHALL carry no
version/product/role columns. All binding mutations SHALL be rejected
with HTTP 409 when the version is signed off.

#### Scenario: Additive upload leaves siblings intact
- **WHEN** version `v1` has one DXF in role `SBT`
- **AND** the client posts another DXF to the same `(v1, SBT)`
  without `replace_file_id`
- **THEN** both bindings exist under `(v1, SBT)`
- **AND** the original binding is unchanged

#### Scenario: replace_file_id evicts the named binding only
- **WHEN** version `v1` has DXFs `A` and `B` bound under role `SBT`
- **AND** the client posts a new DXF with `replace_file_id = A.id`
- **THEN** the `(v1, SBT, A)` binding is removed
- **AND** `A`'s `files` row still exists (other versions may bind it)
- **AND** `B`'s binding is unchanged
- **AND** the new DXF is bound to `(v1, SBT)`

#### Scenario: replace_file_id from another version or role is rejected
- **WHEN** the client posts to `(v2, SBT)` with `replace_file_id`
  referring to a file bound only under `(v1, BD)`
- **THEN** the server returns HTTP 400 and no eviction happens

#### Scenario: Unknown role is rejected
- **WHEN** the client posts a DXF with `role = "TOPCAP"` (not in the valid set)
- **THEN** the server returns HTTP 400 and no binding is created

#### Scenario: RING and LID coexist on the same version
- **WHEN** version `v1` has one DXF under `RING`
- **AND** the client posts a DXF with `role = "LID"`
- **THEN** the upload succeeds with HTTP 200
- **AND** the binding is `(v1, LID)`
- **AND** the RING binding is unchanged

### Requirement: Per-file region rects are independent

Each binding in a `(version, role)` SHALL mark its own
`top_view_rect`, `bottom_view_rect`, and `side_view_rect`
independently; the rects live on the `version_files` row, so the same
file bound to two versions MAY carry different rects per version. Two
sibling bindings MAY both have a non-null `top_view_rect` (or any
other view); the server SHALL accept these writes without cross-file
uniqueness checks. The rule-check merge consolidates the per-file
`match_json` documents into one role-level bundle (see "Per-role
rule-check merging"), so any view label may legitimately receive
matches from multiple source files.

#### Scenario: Both siblings mark top_view_rect successfully
- **WHEN** version `v1` has DXFs `A` and `B` bound under role `SBT`
- **AND** the client PATCHes `A`'s side-regions (with `version_id = v1`) with a non-null `top_view_rect`
- **AND** the client PATCHes `B`'s side-regions (with `version_id = v1`) with a different non-null `top_view_rect`
- **THEN** both PATCH requests return HTTP 200
- **AND** both bindings' `top_view_rect` values persist independently

#### Scenario: Same file in two versions carries independent rects
- **WHEN** versions `v1` and `v2` both bind file `F` under `SBT`
- **AND** the client PATCHes `F`'s side-regions under `v2` only
- **THEN** `(v1, F)`'s rects are unchanged

### Requirement: View resolution lookup (utility)

The server SHALL expose a utility module `app.product_views` with a
function `resolve_views(rows)` that, given the list of binding records
for a `(version, role)`, returns a mapping `{view: ViewSource}` where
`view ∈ {top, bottom, side}`. The function SHALL raise
`ViewCoverageConflict` if a view is claimed by more than one binding.
This module is provided for callers that need a single-source-per-view
view of a role; it is NOT used to validate writes on the upload or
side-regions paths.

#### Scenario: Single-source-per-view roles resolve cleanly
- **WHEN** version `v1` has one SBT binding with `top_view_rect` and
  `bottom_view_rect` set, and another SBT binding with `side_view_rect` set
- **AND** the caller invokes `resolve_views(rows)` for those rows
- **THEN** the returned mapping contains `top`, `bottom`, and `side`
  each pointing at exactly one binding

### Requirement: Per-role rule-check merging

`POST /api/versions/{version_id}/rule-check` SHALL accept any number
of DXFs per role and SHALL build the rule checker's per-role bundle
by merging across files: union the per-file `match_json` dicts and
the per-file `entity_shapes`. When a role has 2+ files, handles in
both maps SHALL be namespaced as `<short_file_id>:<handle>` (first 8
characters of the file id) to keep them unique across the merge.
When a role has exactly one file, handles SHALL remain bare so the
viewer's highlight path keeps working without changes.

#### Scenario: Single-file role produces bare handles
- **WHEN** role `SBT` of version `v1` has one DXF whose `match_json`
  contains handle `"A3F"`
- **THEN** the merged `dxfs_by_role['SBT']['match_json']` contains
  `"A3F"` verbatim
- **AND** the merged `entity_shapes` keys it under `"A3F"`

#### Scenario: Multi-file role namespaces handles per source file
- **WHEN** role `SBT` of version `v1` has DXFs `A` (id `abcdef01...`)
  and `B` (id `12345678...`) each contributing a handle `"7"`
- **THEN** the merged `match_json` and `entity_shapes` carry both as
  `"abcdef01:7"` and `"12345678:7"`

### Requirement: RING and LID are independent roles

A version SHALL be able to hold DXFs under `RING` and `LID`
simultaneously. The upload handler, the rule-check pipeline, and the
DRC bundle builder SHALL treat `RING` and `LID` as independent roles —
neither role SHALL be rejected, dropped, or merged on the basis of
files present under the other. Both roles SHALL appear as first-class
entries in `files_by_role_all`, in the `rule_check` `dxfs_by_role`
bundle, and in the DRC bundle's `manifest.files` list whenever files
are bound under them.

#### Scenario: First LID upload to a version with no RING is accepted
- **WHEN** version `v1` has zero bindings under `RING` and zero under `LID`
- **AND** the client posts a DXF with `role = "LID"`
- **THEN** the upload succeeds with HTTP 200
- **AND** the binding is `(v1, LID)`

#### Scenario: LID upload succeeds when version already has RING
- **WHEN** version `v1` has at least one binding under `RING`
- **AND** the client posts a DXF with `role = "LID"`
- **THEN** the server returns HTTP 200
- **AND** the version detail shows the new file under
  `files_by_role_all["LID"]`
- **AND** the existing RING binding remains under `files_by_role_all["RING"]`

#### Scenario: RING upload succeeds when version already has LID
- **WHEN** version `v1` has at least one binding under `LID`
- **AND** the client posts a DXF with `role = "RING"`
- **THEN** the server returns HTTP 200
- **AND** the version detail shows the new file under
  `files_by_role_all["RING"]`
- **AND** the existing LID binding remains under `files_by_role_all["LID"]`

#### Scenario: DRC bundle carries both roles when both are bound
- **WHEN** version `v1` has one DXF under `RING` and one under `LID`
- **AND** the client requests the version's DRC bundle
- **THEN** the response is HTTP 200 with a zip
- **AND** the embedded `manifest.json`'s `files` list contains one
  entry with `dxf_role = "RING"` and one with `dxf_role = "LID"`

#### Scenario: Rule check feeds both roles into the bundle
- **WHEN** version `v1` has saved Match JSON for files under `SBT`,
  `BD`, `POD`, `RING`, and `LID`
- **AND** the client posts `/api/versions/{v1}/rule-check`
- **THEN** the worker builds `dxfs_by_role` with all five role keys
  populated
- **AND** the job completes and writes `rule_check/{v1}.json`

# product-files Specification

## Purpose
Owns the product-scoped DXF file model: how many DXFs a (product, role)
can hold, how they accumulate vs. replace, how per-file region rects
combine, and how downstream rule-check merges matches across files in
the same role.
## Requirements
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

### Requirement: Per-file region rects are independent

Each DXF in a `(product, role)` SHALL mark its own `top_view_rect`,
`bottom_view_rect`, and `side_view_rect` independently. Two sibling
files MAY both have a non-null `top_view_rect` (or any other view);
the server SHALL accept these writes without cross-file uniqueness
checks. The rule-check merge consolidates the per-file `match_json`
documents into one role-level bundle (see "Per-role rule-check
merging"), so any view label may legitimately receive matches from
multiple source files.

#### Scenario: Both siblings mark top_view_rect successfully
- **WHEN** product `p1` has DXFs `A` and `B` under role `SBT`
- **AND** the client PATCHes `A/side-regions` with a non-null `top_view_rect`
- **AND** the client PATCHes `B/side-regions` with a different non-null `top_view_rect`
- **THEN** both PATCH requests return HTTP 200
- **AND** both files' `top_view_rect` values persist independently

### Requirement: View resolution lookup (utility)

The server SHALL expose a utility module `app.product_views` with a
function `resolve_views(rows)` that, given a list of `FileRecord`s for
a `(product, role)`, returns a mapping `{view: ViewSource}` where
`view ∈ {top, bottom, side}`. The function SHALL raise
`ViewCoverageConflict` if a view is claimed by more than one file.
This module is provided for callers that need a single-source-per-view
view of a role; it is NOT used to validate writes on the upload or
side-regions paths.

#### Scenario: Single-source-per-view roles resolve cleanly
- **WHEN** product `p1` has one SBT DXF with `top_view_rect` and
  `bottom_view_rect` set, and another SBT DXF with `side_view_rect` set
- **AND** the caller invokes `resolve_views(rows)` for those rows
- **THEN** the returned mapping contains `top`, `bottom`, and `side`
  each pointing at exactly one file

### Requirement: Per-role rule-check merging

`POST /api/products/{product_id}/rule-check` SHALL accept any number
of DXFs per role and SHALL build the rule checker's per-role bundle
by merging across files: union the per-file `match_json` dicts and
the per-file `entity_shapes`. When a role has 2+ files, handles in
both maps SHALL be namespaced as `<short_file_id>:<handle>` (first 8
characters of the file id) to keep them unique across the merge.
When a role has exactly one file, handles SHALL remain bare so the
viewer's highlight path keeps working without changes.

#### Scenario: Single-file role produces bare handles
- **WHEN** role `SBT` has one DXF whose `match_json` contains handle
  `"A3F"`
- **THEN** the merged `dxfs_by_role['SBT']['match_json']` contains
  `"A3F"` verbatim
- **AND** the merged `entity_shapes` keys it under `"A3F"`

#### Scenario: Multi-file role namespaces handles per source file
- **WHEN** role `SBT` has DXFs `A` (id `abcdef01...`) and `B`
  (id `12345678...`) each contributing a handle `"7"`
- **THEN** the merged `match_json` and `entity_shapes` carry both as
  `"abcdef01:7"` and `"12345678:7"`

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

### Requirement: Product read API exposes the caller's effective role

The product read endpoints SHALL include the caller's effective role for
each product, computed by the same authorization function the write guards
use, so clients can gate affordances without re-deriving authorization. The
field is additive and advisory; it does not change what any endpoint allows.

#### Scenario: List includes effective_role per product
- **WHEN** an authenticated caller requests `GET /api/products`
- **THEN** each product object includes `effective_role` with one of
  `"viewer"`, `"editor"`, or `"admin"`
- **AND** the value equals `app.guards.effective_role(caller, product_id)`
  for that product

#### Scenario: Single product includes effective_role
- **WHEN** an authenticated caller requests `GET /api/products/{id}`
- **THEN** the response includes `effective_role` for that product

#### Scenario: Bypass-admin resolves to admin
- **WHEN** the app runs in default bypass mode
- **THEN** `effective_role` is `"admin"` for every visible product

#### Scenario: Only visible products are returned (unchanged)
- **WHEN** a caller with a product-scoped viewer grant requests
  `GET /api/products`
- **THEN** only products they can read are returned, each carrying its
  `effective_role`


## ADDED Requirements

### Requirement: Multiple DXFs per (product, role)

A `(product_id, dxf_role)` pair SHALL accept any number of DXFs.
Uploading to a product role SHALL be purely additive by default: an
unconditional `POST /api/products/{pid}/files` adds a new file
without evicting siblings. To replace a specific file in a role
(the common "swap" flow), the client SHALL pass `replace_file_id` —
that file is detached from the product before the new one is
registered. The schema SHALL NOT enforce per-slot uniqueness via a
unique index; coverage uniqueness is enforced at write time by the
view-resolution layer (see "View coverage is unique").

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

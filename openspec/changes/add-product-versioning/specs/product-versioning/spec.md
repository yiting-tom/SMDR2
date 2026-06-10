# product-versioning Specification (delta)

## ADDED Requirements

### Requirement: Version entity under a product

The system SHALL support multiple versions per product. A version row
SHALL carry `id`, `product_id`, `label`, `library_id`,
`signed_off_by` (nullable), `signed_off_at` (nullable), and
`created_at`. Each version SHALL own exactly one library
(`versions.library_id` UNIQUE, 1:1); that library holds the version's
templates and per-class match configuration. A product SHALL always
have at least one version: `POST /api/products` SHALL require a
`version_label` field and SHALL create the product, its first version,
and an empty library in one transaction. Rules remain product-level
and are shared by all versions of the product.

#### Scenario: Creating a product creates its first version
- **WHEN** the client calls `POST /api/products` with
  `{"name": "PKG-1", "version_label": "v1"}`
- **THEN** the response is HTTP 200 with the product id and a
  `versions` list containing one entry labelled `"v1"`
- **AND** that version references a newly created empty library

#### Scenario: Creating a product without version_label fails
- **WHEN** the client calls `POST /api/products` with only `{"name": "PKG-1"}`
- **THEN** the response is HTTP 422
- **AND** no product row is created

#### Scenario: Version listing
- **WHEN** the client calls `GET /api/products/{pid}/versions`
- **THEN** the response lists every version with `id`, `label`,
  `signed_off_by`, `signed_off_at`, `created_at`, ordered by `created_at`

### Requirement: Version label rules

Version labels SHALL be free-form non-empty text, manually entered
(no auto-increment). Within one product, labels SHALL be unique
(`UNIQUE(product_id, label)`); creating a version with a duplicate
label SHALL return HTTP 409 and SHALL NOT overwrite the existing
version. The same label MAY exist under different products.

#### Scenario: Duplicate label within a product is rejected
- **WHEN** product `p1` already has a version labelled `"v1"`
- **AND** the client posts `POST /api/products/p1/versions` with `{"label": "v1"}`
- **THEN** the response is HTTP 409
- **AND** the existing version is unchanged and no new version exists

#### Scenario: Same label under different products is allowed
- **WHEN** product `p1` has a version `"v1"`
- **AND** the client creates version `"v1"` under product `p2`
- **THEN** the response is HTTP 200

### Requirement: Versions are never deletable

The system SHALL NOT expose any endpoint that deletes a version.
Deleting a product (admin operation) SHALL cascade-delete its
versions, their libraries, and their bindings; this is the only path
that removes version rows.

#### Scenario: No version delete endpoint
- **WHEN** the client calls `DELETE /api/versions/{vid}` (any method spelling)
- **THEN** the response is HTTP 404 or 405 (route does not exist)

#### Scenario: Product delete cascades
- **WHEN** a product with two versions is deleted
- **THEN** both version rows, both libraries (with their templates and
  class configs), and all `version_files` rows for them are removed

### Requirement: New version clones the source version

`POST /api/products/{pid}/versions` SHALL accept `{"label", "clone_from"?}`
where `clone_from` is an existing version id of the same product
(default: the product's most recently created version). Creation SHALL,
in a single transaction:

1. insert the `versions` row (label uniqueness enforced),
2. create a new library and copy every template row and every per-class
   match-config row from the source version's library into it,
3. copy every `version_files` row from the source version (same
   `file_id`, role, and per-version state) onto the new version.

Derived artifacts (parsed/prematch/match/layer previews/rule results)
SHALL NOT be cloned; they are recomputed on demand for the new version.

#### Scenario: Clone copies templates, config, and bindings
- **WHEN** version `v1` of product `p1` has 3 templates, a tuned
  match config for class `SMD-2T`, and an SBT binding to file `F`
- **AND** the client posts `{"label": "v2"}` (no `clone_from`)
- **THEN** the new version `v2` has its own library containing 3
  template rows equal in content to `v1`'s
- **AND** `v2`'s `SMD-2T` match config equals `v1`'s
- **AND** `v2` has an SBT binding to the same `file_id` `F`
- **AND** `v1`'s library and bindings are unchanged

#### Scenario: clone_from selects an older version
- **WHEN** product `p1` has versions `v1` and `v2`
- **AND** the client posts `{"label": "v3", "clone_from": "<v1.id>"}`
- **THEN** `v3`'s library content equals `v1`'s, not `v2`'s

#### Scenario: clone_from of another product is rejected
- **WHEN** the client posts to `POST /api/products/p1/versions` with
  `clone_from` referencing a version of product `p2`
- **THEN** the response is HTTP 400 and no version is created

#### Scenario: Editing the clone does not touch the source
- **WHEN** `v2` was cloned from `v1`
- **AND** the user commits a new template and deletes an old one in `v2`
- **THEN** `v1`'s template list is byte-for-byte unchanged

### Requirement: Version sign-off freezes the version

`POST /api/versions/{vid}/sign-off` SHALL set `signed_off_by` to the
acting identity and `signed_off_at` to the current time. While
`signed_off_by` is non-null, every mutating operation targeting the
version — template commit/delete/move, match-config changes, file
upload/replace/unbind, side-region or unit-override writes, match
re-runs (scan-all, match-json save), and rule-check submission — SHALL
be rejected server-side with HTTP 409 and a body identifying
`signed_off_by` and `signed_off_at`. Read access (viewing geometry,
match results, rule results) SHALL remain unaffected.

`DELETE /api/versions/{vid}/sign-off` SHALL clear both fields,
returning the version to the editable state.

Until the auth change lands, the acting identity SHALL be taken from
the `SMDR2_DEV_USER` environment variable (default `"dev"`); the
unsign endpoint SHALL NOT enforce an admin check yet (noted as a
follow-up for the auth change).

#### Scenario: Sign-off records who and when
- **WHEN** the client posts `POST /api/versions/{vid}/sign-off`
- **THEN** the version's `signed_off_by` equals the configured dev user
- **AND** `signed_off_at` is non-null
- **AND** `GET /api/products/{pid}/versions` reflects both fields

#### Scenario: Signing an already-signed version is rejected
- **WHEN** version `v` is already signed off
- **AND** the client posts sign-off again
- **THEN** the response is HTTP 409

#### Scenario: Mutations on a signed version are blocked
- **WHEN** version `v` is signed off
- **AND** the client attempts a template commit, a file upload to `v`,
  a match-config change, or a rule-check submission for `v`
- **THEN** each response is HTTP 409 naming `signed_off_by`
- **AND** no row, file binding, or artifact changes

#### Scenario: Reads on a signed version still work
- **WHEN** version `v` is signed off
- **AND** the client GETs its versions list, match JSON, or rule result
- **THEN** each response is HTTP 200 with the frozen content

#### Scenario: Unsign reopens the version
- **WHEN** version `v` is signed off
- **AND** the client calls `DELETE /api/versions/{vid}/sign-off`
- **THEN** `signed_off_by` and `signed_off_at` are null
- **AND** a subsequent template commit succeeds

### Requirement: Role-file bindings live on the version

The binding of DXF files to roles SHALL be stored in a
`version_files(version_id, role, file_id, …)` junction, not on the
`files` table. `files` rows SHALL be pure content storage keyed by
content hash; the same `file_id` MAY be bound to any number of
versions (within and across products) with zero byte duplication.
Per-version interpretation state (selected layers, view, side-region
rects, unit override) SHALL be stored on the junction row, so two
versions sharing one file MAY hold different layer selections.

#### Scenario: Carried-over file is shared, not copied
- **WHEN** version `v2` is cloned from `v1` which binds SBT file `F`
- **AND** the client replaces only the POD binding on `v2`
- **THEN** `v2`'s SBT binding still references the same `file_id` `F`
- **AND** only one copy of `F`'s bytes exists under `uploads/`

#### Scenario: Per-version layer selection is independent
- **WHEN** versions `v1` and `v2` both bind file `F` under SBT
- **AND** the client confirms layer set `{A}` for `(v1, F)` and `{A, B}` for `(v2, F)`
- **THEN** each version's stored `selected_layers` reflects its own choice

### Requirement: Derived artifacts are keyed by (version, file)

Every derived artifact SHALL be stored under a version-scoped key:
`parsed/{version_id}/{file_id}.json`,
`prematch/{version_id}/{file_id}.json`,
`match/{version_id}/{file_id}.json`,
`layer_preview/{version_id}/{file_id}/…`, and
`rule_check/{version_id}.json`. Re-running any computation for one
version SHALL NOT modify another version's artifacts. Old versions'
match and rule results SHALL remain readable indefinitely.

#### Scenario: Re-running v2 leaves v1's results intact
- **WHEN** `v1` has a saved `match/{v1}/{F}.json`
- **AND** `v2` (sharing file `F`) runs scan-all and saves match JSON
- **THEN** `match/{v2}/{F}.json` is written
- **AND** `match/{v1}/{F}.json` is byte-for-byte unchanged

#### Scenario: Old version results readable after new versions exist
- **WHEN** product `p1` has signed-off `v1` and active `v3`
- **AND** the client GETs `v1`'s rule-check result
- **THEN** the response is HTTP 200 with the result computed when `v1` was active

### Requirement: Version switcher UI

The product page SHALL present a version switcher listing every
version of the product with its label and sign-off state. Selecting a
version SHALL scope the whole page — role slots, file lists, viewer
links, match status, and rule-check panel — to that version. Signed
versions SHALL render a badge showing `signed_off_by` and a formatted
`signed_off_at`, and SHALL present all editing affordances disabled
(server-side enforcement still applies). The page SHALL offer a "new
version" action that prompts for the label and clones the currently
selected version.

#### Scenario: Switching versions re-scopes the page
- **WHEN** the user selects `v1` in the switcher on a product with `v1` and `v2`
- **THEN** the role slots and rule-check panel show `v1`'s bindings and results

#### Scenario: Signed version shows badge and read-only state
- **WHEN** the selected version is signed off
- **THEN** a badge shows who signed and when
- **AND** upload/commit/re-run controls are rendered disabled

#### Scenario: New-version action clones the selected version
- **WHEN** the user clicks "new version" while `v1` is selected and enters `"v2"`
- **THEN** `POST /api/products/{pid}/versions` is sent with
  `{"label": "v2", "clone_from": "<v1.id>"}`
- **AND** the switcher refreshes with `v2` selected

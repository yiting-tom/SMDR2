## ADDED Requirements

### Requirement: DRC bundle export endpoint

SMDR2 SHALL expose `GET /api/products/{product_id}/drc-bundle`, an HTTP
endpoint that returns a zip archive conforming to the "External DRC
handoff bundle format" requirement. The endpoint SHALL stream the zip
with `Content-Type: application/zip` and a `Content-Disposition`
header whose filename is `drc-bundle-{product_id}.zip`.

The endpoint SHALL apply these preconditions before assembling the
bundle, returning HTTP 400 (or 404 for missing product) with a
human-readable JSON `detail` message on failure:

- 404 when `product_id` does not resolve to a known product.
- 400 when the product has no DXFs with a non-null `dxf_role` attached.
- 400 when any role-attached `FileRecord` has `match_saved == false`,
  with the message listing every offending role. This mirrors the
  precondition of `POST /api/products/{product_id}/rule-check` so users
  see one consistent error vocabulary.

When all preconditions hold, the zip SHALL contain exactly:

- `manifest.json` at the archive root, conforming to
  `openspec/specs/design-rule-checking/drc-manifest.schema.json`.
- `dxfs/{file_id}.dxf` for every role-attached file, copied byte-for-byte
  from `data/uploads/{file_id}.dxf`.
- `match/{file_id}.json` for every role-attached file, copied
  byte-for-byte from `data/match/{file_id}.json`.

The manifest SHALL populate:

- `bundle_version` = `"1.0.0"` (until the schema's MAJOR is bumped).
- `product_id` = the product's id.
- `product_name` = the product's display name, when one is set.
- `exported_at` = the moment of export, UTC ISO-8601 with second
  precision (e.g. `"2026-05-19T07:30:00Z"`).
- `files[]` = one entry per role-attached `FileRecord`, with `role`,
  `file_id`, `dxf` (= `"dxfs/{file_id}.dxf"`), and `match_json`
  (= `"match/{file_id}.json"`).

Each Match JSON inside the bundle SHALL be the per-file document
exactly as persisted at `data/match/{file_id}.json`, with raw,
unprefixed handles. The `<file_id[:8]>:` prefix that
`run_product_rule_check` applies for the internal mock checker SHALL
NOT appear in the exported bundle.

#### Scenario: Successful export for a single-DXF-per-role product
- **WHEN** a product has exactly four role-attached files (one per role) and every file has `match_saved == true`
- **AND** the user calls `GET /api/products/{product_id}/drc-bundle`
- **THEN** the response status is 200
- **AND** `Content-Type` is `application/zip`
- **AND** the zip contains `manifest.json` plus four `dxfs/*.dxf` and four `match/*.json` entries
- **AND** `manifest.files` has length 4 with one entry per role
- **AND** every `manifest.files[].dxf` and `match_json` path resolves to an entry inside the zip

#### Scenario: Successful export for a multi-DXF-per-role product
- **WHEN** a product has two `BD` files plus one each of `SBT`, `POD`, `RING`
- **AND** every file has `match_saved == true`
- **AND** the user calls the endpoint
- **THEN** the response status is 200
- **AND** `manifest.files` has length 5
- **AND** exactly two entries carry `role: "BD"` with different `file_id` values
- **AND** the zip contains five `dxfs/*.dxf` and five `match/*.json` entries

#### Scenario: Match JSONs are exported with raw handles
- **WHEN** the bundle is produced for a multi-DXF role
- **AND** a consumer reads any `match/*.json` entry from the zip
- **THEN** no handle in any match group begins with `^[0-9a-f]{8}:` (the internal merge prefix)
- **AND** each handle is identical to the handle stored in `data/match/{file_id}.json`

#### Scenario: Missing Match JSON for any role rejects the export
- **WHEN** the user calls the endpoint on a product whose `BD` file has `match_saved == false`
- **THEN** the response status is 400
- **AND** the JSON `detail` includes the role string `"BD"`
- **AND** no zip body is produced

#### Scenario: Unknown product returns 404
- **WHEN** the user calls the endpoint with a `product_id` that does not exist
- **THEN** the response status is 404

#### Scenario: Product with no role-attached DXFs rejects the export
- **WHEN** the product exists but every `FileRecord` has `dxf_role == None`
- **THEN** the response status is 400 with a message indicating no DXFs are attached

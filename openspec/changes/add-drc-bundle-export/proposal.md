## Why

Design rule checking is run by a separate team. Today we hand them files
ad-hoc — "the four DXFs and their Match JSONs" — which breaks the moment
a product has multiple DXFs in one role (BD top + bottom siblings, SBT
revisions, etc.). The handoff contract has already been defined in
`openspec/specs/design-rule-checking/spec.md` ("External DRC handoff
bundle format" requirement) and `drc-manifest.schema.json`; we now need
SMDR2 to actually produce that bundle on demand so the external team has
a stable, scriptable artifact to consume.

## What Changes

- New HTTP endpoint `GET /api/products/{product_id}/drc-bundle` returns
  a zip stream containing `manifest.json` + every product DXF + every
  per-file Match JSON, conforming to the existing manifest schema.
- The endpoint walks `FileRecord`s with a non-null `dxf_role`, requires
  every file to have `match_saved == true` (same precondition as
  `POST /api/products/{product_id}/rule-check`), and 400s with a clear
  list of missing roles otherwise.
- Each Match JSON is shipped **per-file** (raw handles, no
  `<file_id[:8]>:` merge prefix). The per-role merging that
  `run_product_rule_check` does internally is NOT applied to the
  exported bundle — the external team gets each DXF in its own
  coordinate space.
- `manifest.json` is built from the file list with the contract fields
  (`bundle_version`, `product_id`, optional `product_name` /
  `exported_at`, `files[]`). DXFs are stored under `dxfs/<file_id>.dxf`,
  Match JSONs under `match/<file_id>.json` inside the zip; manifest
  paths reference these relative paths.
- No change to the rule-check execution path; the bundle is purely an
  out-of-process handoff format.

## Capabilities

### New Capabilities
<!-- None — every requirement added by this change lives in the existing
     design-rule-checking spec. -->

### Modified Capabilities
- `design-rule-checking`: adds a new "DRC bundle export endpoint"
  requirement specifying the HTTP route, preconditions, zip layout, and
  manifest population. The existing "External DRC handoff bundle
  format" requirement defined the *contract*; this change adds the
  *endpoint that produces it*.

## Impact

- **New code**: `app/main.py` gains the export endpoint; a new small
  module (e.g. `app/drc_bundle.py`) holds the zip + manifest assembly
  so the route handler stays thin and the assembly is unit-testable.
- **No DB / storage changes**: the bundle is assembled on demand from
  existing `data/uploads/{file_id}.dxf` + `data/match/{file_id}.json`;
  nothing new is persisted.
- **Tests**: a new `tests/test_drc_bundle.py` covers single-DXF-per-role,
  multi-DXF-per-role, the no-merge-prefix invariant, manifest schema
  conformance, the `match_saved` precondition, and missing-product / no
  uploads error paths.
- **Spec**: one new `### Requirement:` block in the
  `design-rule-checking` spec; `drc-manifest.schema.json` is unchanged.
- **External team**: their loader iterates `manifest.files`, groups by
  `role` for role-scoped rules, processes per-file for geometry rules.
  No prefix-parsing required.

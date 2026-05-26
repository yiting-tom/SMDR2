## Context

The DRC handoff format is already specified — `openspec/specs/design-rule-checking/spec.md`
defines the bundle layout (`manifest.json` + per-file DXFs + per-file
Match JSONs, no merge prefix) and `drc-manifest.schema.json` pins the
manifest schema. SMDR2 already persists every input the bundle needs:
`data/uploads/{file_id}.dxf` and `data/match/{file_id}.json`. The
existing internal endpoint `POST /api/products/{product_id}/rule-check`
walks `FileRecord`s with non-null `dxf_role`, demands every file has
`match_saved == true`, and groups by role — the same walk this export
needs.

Stakeholders: the external rule-checking team (consumer), the SMDR2
backend (producer), and the engineer driving the product through the
SMDR2 UI (initiator — clicks "Export" or hits the endpoint via tooling).

## Goals / Non-Goals

**Goals:**
- One HTTP route that produces a complete, self-describing bundle for
  a given product id, conformant to the existing manifest schema.
- Bundle is reproducible: re-exporting the same product after no input
  changes SHOULD produce byte-identical DXF / Match JSON contents
  (timestamp in `exported_at` is the only differing field).
- Stream the zip — no temp file on disk, no full-bundle in-memory copy
  for large multi-DXF products.
- Same precondition + 400 messaging as `POST .../rule-check`, so users
  see one consistent error vocabulary.

**Non-Goals:**
- No authentication / authorisation gating (out of scope; SMDR2 is
  currently single-tenant on a trusted LAN).
- No bundle history / persistence — re-export is cheap, no need to
  store every export.
- No transport (email, S3 upload, webhook) — just an HTTP endpoint
  that returns the zip. Delivery is whatever the caller wants to do
  with the response body.
- No change to the internal `run_product_rule_check` merge logic. The
  bundle uses **per-file** Match JSONs; the merged role-bundle form is
  an internal-only artifact for the SMDR2 mock checker.

## Decisions

**Decision 1: New module `app/drc_bundle.py` instead of inlining into `main.py`.**
The assembly logic (read files, walk role list, build manifest, stream
zip entries) is non-trivial and benefits from isolated unit tests.
`main.py` keeps a thin route handler that wires the FastAPI request to
the module.

*Alternative considered:* Inline in `main.py`. Rejected because the
zip-streaming + manifest assembly is testable in isolation without
spinning up the full app, and `main.py` already carries a lot of
endpoint logic.

**Decision 2: Stream via `zipfile.ZipFile` over a `BytesIO` buffer rather than `aiozip` or a temp file.**
`zipfile` in the stdlib supports incremental writes against any
file-like object. A `BytesIO` is fine for the typical product
(handful of DXFs, each <= a few MB). For larger payloads, FastAPI's
`StreamingResponse` lets us yield the bytes without holding the whole
zip in RAM at once — but the current upload size cap on existing
endpoints suggests we're well below the threshold where streaming
matters. Start with `BytesIO`, leave a TODO for chunked streaming if
real bundle sizes ever push past a few hundred MB.

*Alternative considered:* Spool to a temp file and `FileResponse`.
Rejected for adding disk I/O the bundle doesn't otherwise need; we
already read the inputs from disk, no reason to round-trip the output
through it too.

**Decision 3: Zip internal layout pins paths so manifest references are stable.**

```
manifest.json
dxfs/<file_id>.dxf
match/<file_id>.json
```

Using `<file_id>.dxf` rather than the user-uploaded filename makes the
bundle deterministic (no filename collisions between sibling DXFs that
happen to share an upload name; no user-PII leaking via filenames).
The manifest's `dxf` / `match_json` fields carry these relative paths
exactly.

*Alternative considered:* Preserve original upload filenames (e.g.
`dxfs/bd-top.dxf`). Rejected — uploads aren't guaranteed unique,
and the external team consumes file_id anyway via the manifest.

**Decision 4: Precondition: every `FileRecord` with a `dxf_role` must have `match_saved == true`.**
Matches the precondition of `POST .../rule-check`. A bundle without a
Match JSON for one of its DXFs is useless to the external team. 400
with the same error shape the rule-check endpoint already returns,
listing the offending roles, so the UI / caller has one consistent
recovery path.

*Alternative considered:* Export DXFs even when their Match JSON is
missing. Rejected because the manifest schema mandates a non-empty
`match_json` per entry; a partial bundle would either violate the
schema or require a "draft" mode we have no use case for.

**Decision 5: `manifest.exported_at` is UTC ISO-8601 with second precision.**
Useful for cross-referencing exports against rule-check reports the
external team produces. Second precision is enough for human
debugging; sub-second precision adds bytes without value.

**Decision 6: No external team auth — the route is open.**
SMDR2 has no auth layer today; adding one for this endpoint alone would
be inconsistent. If the deployment ever moves to a hostile network, the
whole app needs auth, not just this route.

## Risks / Trade-offs

- **[Risk]** Large products with many big DXFs blow `BytesIO` memory.
  → **Mitigation**: Document the limit in the route docstring; if
  real-world bundles ever push past ~256 MB, swap `BytesIO` for a
  `tempfile.SpooledTemporaryFile` (rolls over to disk past a
  configurable threshold) without changing the public contract.
- **[Risk]** External team's loader trusts manifest paths without
  validating they stay inside the bundle (zip slip).
  → **Mitigation**: All paths we emit are simple `dxfs/...` /
  `match/...` strings with no `../`. We can document the invariant in
  the schema description so naive consumers know not to extract paths
  with `os.path.join`-style absolutes. Not our bug if they ignore it,
  but documenting it costs nothing.
- **[Risk]** Re-exporting the same product produces a different
  `exported_at` and therefore a different zip checksum, breaking
  byte-equality smoke tests downstream.
  → **Mitigation**: Document `exported_at` as the only nondeterministic
  field. If the external team really wants reproducibility for caching,
  they can hash the contents excluding `exported_at`.
- **[Trade-off]** Per-file Match JSON (raw handles) vs merged
  (prefixed). Chosen per-file — see proposal "Why" and the existing
  spec requirement. Trade-off: external team has to do their own
  grouping by role; benefit: no shared prefix convention to evolve in
  lockstep across two teams.

## Migration Plan

No migration needed — this is a pure addition. Steps to deploy:

1. Land the change.
2. Notify the external team that the endpoint is available; share the
   manifest schema and the existing spec requirement.
3. Existing ad-hoc file-sharing workflow remains usable in parallel;
   the external team can switch on their own timeline.

Rollback: revert the endpoint addition. No data migration to undo.

## Open Questions

- Should we expose the export through the dashboard UI (e.g. a button
  on the product page) as part of this change, or is the HTTP endpoint
  enough for the first cut? **Tentative**: endpoint only for now; UI
  can come later once the external team's loader is proven.
- Should `bundle_version` be configurable, or hard-coded at `"1.0.0"`
  until the schema actually evolves? **Tentative**: hard-coded
  constant in `app/drc_bundle.py`; bump alongside any schema change.

## Context

`app/drc_bundle.py::build_manifest(product, files, *, now=None)` is
the single chokepoint for what ends up at the top of `manifest.json`.
Today it emits `bundle_version`, `product_id`, `exported_at`,
optionally `product_name`, and the `files` array. It has no
dependency on the library registry; the product object carries a
`library_id` but the builder never resolves it.

The endpoint at `app/main.py:1058` (`GET /api/products/{pid}/drc-bundle`)
loads the product and the role-attached files and hands them to
`build_bundle`, which delegates the manifest portion to
`build_manifest`. The library lookup is reachable from the same
process: `app/library.py` exposes `LIBRARIES = LibraryRegistry(_STORE)`
with `LIBRARIES.get(library_id) -> Library` and the underlying
store's `get_library(library_id)` returns the SQLite row with both
`id` and `name`.

## Goals / Non-Goals

**Goals:**
- Surface the customer dimension at the top of the manifest as
  `customer_id` (opaque) + `customer` (human-readable).
- Keep the field naming symmetric with the existing
  `product_id` / `product_name` pair.

**Non-Goals:**
- Per-file `customer` annotation. The customer is product-scoped, so
  it lives at the top level.
- Customer-side policy or filtering inside SMDR2. We just emit the
  name; what the external team does with it is their concern.
- Multi-library products. A product binds to exactly one `library_id`
  (see `app/products.py::Product`); cross-library aggregation isn't
  on the roadmap.

## Decisions

### Resolve the library name inside `build_manifest`, not at the call site

The endpoint already has the `product`; passing the library name in
as a separate argument leaks responsibility — every future caller
would have to remember the lookup. Doing it inside `build_manifest`
keeps the API as `build_manifest(product, files)` and gives the
function a single, documented dependency on `LIBRARIES`.

**Alternative — pass `library_name` as a kwarg**: rejected. It would
mean two parallel sources of truth (library_id on product, name from
caller) and a footgun if they ever drift.

### `customer_id` is required; `customer` is optional

Mirrors `product_id` (required) and `product_name` (optional). Every
product has a `library_id` (it's NOT NULL in the schema), so
`customer_id` is always available. The library `name` column is
populated by the standard creation flow but the spec doesn't
mandate non-empty; treating it as optional keeps the schema honest
without surprising the export when a library is renamed to empty.

### Library lookup failure → fail loudly, not silent fallback

If `LIBRARIES.get(product.library_id)` raises `KeyError` (the library
was deleted out-of-band) the manifest builder SHALL re-raise as
`ValueError` with the offending id. The DRC handoff is a contract —
emitting a manifest without a resolvable customer would be a silent
data-integrity failure the external team can't debug. The endpoint
handler can surface this as HTTP 500.

### `bundle_version` bump: `1.1.0` → `1.2.0`

Adding a required top-level field IS technically a breaking change
for strict JSON Schema consumers. The external team's stance (per
the existing manifest contract) is that they refuse only on major
version mismatch, so a minor bump is correct as long as it changes
*something* in `bundle_version` so they can detect old bundles. We
keep the policy consistent with the `1.0.0 → 1.1.0` precedent set by
the RING/LID change.

## Risks / Trade-offs

- **Risk — `LIBRARIES.get` blocks bundle export** if the library
  registry is slow to initialise.
  → Mitigation: `LIBRARIES.get` is in-memory cached after first
  read; for a default library it's a constant-time hit. Real risk
  is negligible.

- **Risk — external consumer rejects the new bundle because their
  parser is stricter than the documented contract** (e.g., they
  fail on unknown fields).
  → Mitigation: announce the bump, point them at the spec, and
  cushion with the `bundle_version` change so they have a clean
  signal to update their parser.

- **Risk — library renames change the manifest's customer name
  without bumping `bundle_version`**.
  → Mitigation: `customer_id` stays stable, so consumers keying on
  id are fine. Library renames are an editorial action by the
  internal team; if external reports need a stable display name,
  they should key by `customer_id` and join against their own
  customer table.

## Migration Plan

1. Land code + schema + spec changes together (one commit).
2. No data migration needed — existing bundles on disk are not
   regenerated; the next export picks up the new fields naturally.

Rollback: revert the `build_manifest` change and the schema. The
spec delta is then drift; either revert the spec edit too or roll a
follow-up reverting both.

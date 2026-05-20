## Why

The DRC handoff manifest (`manifest.json` inside the
`Download All Match` bundle) names the product and lists every DXF,
but it never names the **customer** that product belongs to. Inside
SMDR2 the customer dimension is modelled as a `library` (i.e. a
template-grouping namespace); to the external rule-checking team that
consumes the bundle it is the customer name, and they currently have
to cross-reference reports by hand because the manifest doesn't
include it.

Surfacing `customer` + `customer_id` at the top of the manifest lets
the external team route reports by customer without out-of-band
lookups and gives SMDR2 a clean place to expose any future
customer-scoped policy.

## What Changes

- Add two top-level scalar fields to `manifest.json`:
  - **`customer_id`** (required, string): the SMDR2 internal
    `library_id` the product is bound to. Opaque to consumers,
    mirrors how `product_id` is treated.
  - **`customer`** (optional, string): the human-readable library
    name. Omitted when the library has no name (defensive — every
    library created through the standard API has one, but the schema
    keeps the field optional to match the existing `product_name`
    pattern).
- `app/drc_bundle.py::build_manifest` resolves the library name via
  `LIBRARIES.get(product.library_id)` and populates both fields. The
  function gains a small dependency on the library registry; the
  bundle builder is no longer purely a function of `Product` + files.
- Bump `BUNDLE_VERSION` from `"1.1.0"` to `"1.2.0"` (additive minor
  bump per the existing rule — adding required fields IS technically a
  breaking change for strict consumers, but the external team agreed
  enum/field widening is a minor bump as long as `bundle_version`
  itself is bumped so they can refuse old bundles).
- Update `drc-manifest.schema.json`: add `customer_id` to `required`,
  add both fields to `properties`.
- Update the design-rule-checking spec table + add scenarios that
  cover the new fields and the missing-library failure mode.
- Extend `tests/test_drc_bundle.py` with positive + edge-case
  coverage (named library, unnamed-library fallback).

## Capabilities

### New Capabilities
- (none)

### Modified Capabilities
- `design-rule-checking`: handoff bundle manifest gains `customer` /
  `customer_id` top-level fields; `bundle_version` bumps to `1.2.0`.

## Impact

- Code: `app/drc_bundle.py` (build_manifest, BUNDLE_VERSION) only.
  The endpoint at `app/main.py:1058` is unaffected — it already
  passes `product` through to `build_bundle`.
- Schema: `openspec/specs/design-rule-checking/drc-manifest.schema.json`.
- Spec: `design-rule-checking/spec.md` — manifest field table and
  related scenarios.
- Tests: `tests/test_drc_bundle.py`.
- Coordination: the external rule-checking team needs to either
  ignore the new fields (forward-compatible JSON) or pick them up at
  their leisure. The `bundle_version` bump is the signalling channel.

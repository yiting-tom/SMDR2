## Why

`customer_id` / `customer` were in the DRC manifest (bundle `1.2.0`) but were
**dropped on 2026-06-10** when the one-library-per-version topology removed the
old library-as-customer dimension. Since then customer has come back as a
**first-class grouping above product** — `products.customer_id` (migration
`0004`) referencing the `customers` table (the auth/production work). The
external rule-checking team needs the customer back at the top of the manifest
to route reports without an out-of-band lookup.

This re-adds `customer_id` / `customer`, but with the **new** semantics
(`products.customer_id` → `customers`, not the dead `library_id`). It also
reconciles the spec, which still describes the stale 1.x library-based fields
and `bundle_version 1.4.0` while the code is at `2.2.0`.

## What Changes

- `app/drc_bundle.py` `build_manifest`: emit `customer_id` = `product.customer_id`
  (required, mirrors `product_id`) and `customer` = the resolved customer name
  when non-empty (optional, mirrors `product_name`). Stays a pure function — the
  name is passed in via a new `customer_name` param; `build_bundle` gains the
  same param and threads it through.
- The bundle endpoint (`app/main.py`) resolves the name via
  `AUTH_STORE.get_customer(product.customer_id)` and passes it down.
- Unlike the old 1.x behaviour, a missing/nameless customer does **not** raise:
  `customer_id` is always present (`products.customer_id` defaults to
  `"uncategorized"`, a seeded customer named `未分類`); the `customer` name is
  simply omitted when it can't be resolved.
- `bundle_version` `2.2.0` → `2.3.0`.
- Update `drc-manifest.schema.json` (re-add `customer_id` required + `customer`
  optional) and the `design-rule-checking` spec's bundle-format requirement to
  the new semantics + corrected version note.

## Capabilities

### Modified Capabilities

- `design-rule-checking`: the "External DRC handoff bundle format" requirement
  re-adds the `customer_id` / `customer` top-level fields with customer-entity
  semantics, corrects the `bundle_version` note, and replaces the old
  library-registry resolution (which raised on a missing library) with a
  customer-table lookup that omits the name when absent.

## Impact

- **Code**: `app/drc_bundle.py` (manifest fields + `customer_name` param),
  `app/main.py` (resolve + pass the name). No new dependency.
- **Manifest contract**: additive at the top level — `bundle_version` minor
  bump `2.2.0` → `2.3.0`; consumers on `2.x` keep working. `customer_id` is
  required again; `customer` optional.
- **Schema / spec**: `drc-manifest.schema.json` + the DRC spec requirement
  updated (and de-staled from the 1.x library wording).
- **Tests**: `tests/test_drc_bundle.py` — flip the existing
  "customer is absent" assertion to expect `customer_id` (and `customer` for a
  named customer); bump the `bundle_version` assertion.
- **APIs / DB / migrations**: none.

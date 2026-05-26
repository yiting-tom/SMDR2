## 1. Manifest schema

- [x] 1.1 Update `openspec/specs/design-rule-checking/drc-manifest.schema.json`:
  - Add `"customer_id"` to the top-level `required` array.
  - Add `customer_id` (string, minLength 1) and `customer` (string) to top-level `properties` with `description` fields.
  - Update the `bundle_version` `examples` entry from `"1.1.0"` to `"1.2.0"`.

## 2. Manifest builder

- [x] 2.1 In `app/drc_bundle.py` import `LIBRARIES` from `app.library`.
- [x] 2.2 Bump `BUNDLE_VERSION` from `"1.1.0"` to `"1.2.0"`.
- [x] 2.3 In `build_manifest`, after the existing RING/LID exclusion check, resolve the library via `LIBRARIES.get(product.library_id)`. Catch `KeyError` and re-raise as `ValueError(f"library {product.library_id!r} not found ...")`.
- [x] 2.4 Write `manifest["customer_id"] = product.library_id`.
- [x] 2.5 If the resolved library has a non-empty name, write `manifest["customer"] = name`; otherwise omit the key entirely.
- [x] 2.6 Update the module docstring at the top of `drc_bundle.py` to mention the new `customer` / `customer_id` fields and the registry dependency.

## 3. Tests

- [x] 3.1 In `tests/test_drc_bundle.py`, extend the existing happy-path test (or add a new one) to assert that `manifest["customer_id"]` equals the seeded product's `library_id` and `manifest["customer"]` equals the library's name.
- [x] 3.2 Add a test for the unnamed-library case: create a library with an empty name (or stub the registry), build the manifest, and assert `customer` is absent while `customer_id` is present.
- [x] 3.3 Add a test for the missing-library case: register a product, then forcibly delete the library row (or pass a product with a bogus `library_id`), and assert `build_manifest` raises `ValueError` whose message includes the unresolved id.
- [x] 3.4 Re-run the existing schema-validation tests (`test_build_bundle_*_validates_against_schema`) and confirm they still pass with the new required field — they exercise `seeded_product` which uses the default library, so `customer_id` will be populated naturally.

## 4. Sync + smoke check

- [x] 4.1 Run `openspec validate add-customer-fields-to-drc-manifest`.
- [x] 4.2 Run the full pytest suite and resolve any regressions.
- [x] 4.3 Manual sanity: `curl /api/products/<pid>/drc-bundle` for a known product, unzip, `cat manifest.json` and confirm both fields render correctly. (Optional — covered by tests.)

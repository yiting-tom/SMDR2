## 1. Code

- [x] 1.1 `app/drc_bundle.py` `build_manifest`: add a `customer_name: str | None = None` keyword param; in the manifest dict emit `"customer_id": product.customer_id` (after `product_id`) and, when `customer_name`, `"customer": customer_name`. Keep the function pure.
- [x] 1.2 `app/drc_bundle.py` `build_bundle`: add the same `customer_name` param and pass it to `build_manifest`.
- [x] 1.3 `app/drc_bundle.py`: bump `BUNDLE_VERSION` to `"2.3.0"`; update the module docstring (the "customer dimension is gone" note → customer is back via `products.customer_id`).
- [x] 1.4 `app/main.py` (the download-bundle endpoint, ~line 2271): resolve `cust = AUTH_STORE.get_customer(product.customer_id)`, pass `customer_name=(cust or {}).get("name") or None` to `build_bundle`.

## 2. Schema + spec

- [x] 2.1 `openspec/specs/design-rule-checking/drc-manifest.schema.json`: re-add `customer_id` (string, minLength 1) to `properties` + `required`, and `customer` (string) to `properties`; update any `bundle_version` const/example if present.
- [x] 2.2 (delta) MODIFIED `design-rule-checking` "External DRC handoff bundle format": customer rows + resolution + version note updated to the customer-entity semantics (handled in `specs/.../spec.md`).

## 3. Tests

- [x] 3.1 `tests/test_drc_bundle.py`: flip `test_manifest_carries_version_fields` to assert `manifest["customer_id"] == product.customer_id` and `customer` present when a name is passed; add a case for the omitted-name path; bump the `bundle_version == "2.3.0"` assertion(s).
- [x] 3.2 `uv run ruff check` clean on touched files; `uv run pytest -q` green (esp. `tests/test_drc_bundle.py`).

## 4. Archive

- [ ] 4.1 After tasks 1–3 pass, run `/opsx:archive re-add-customer-fields-to-drc-manifest`.

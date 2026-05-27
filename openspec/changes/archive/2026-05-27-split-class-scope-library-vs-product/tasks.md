## 1. Class-scope registry (`app/library.py`)

- [x] 1.1 Add module-level constant `PRODUCT_SCOPED_CLASSES: frozenset[str]` containing `{"Substrate", "Lid", "LidOuter", "LidInner", "DieArea", "C4Ball", "BGABall", "Protrusion"}` immediately after `DEFAULT_CLASSES` / `CLASS_JSON_KEY`. Include a 2-line comment noting "library-scoped is the implicit default; only classes in this set persist with a non-null product_id".
- [x] 1.2 Add helper `def is_product_scoped(class_name: str) -> bool` co-located with `is_allowed_view`.
- [x] 1.3 Sanity-check at import time that every member of `PRODUCT_SCOPED_CLASSES` also appears in `DEFAULT_CLASSES`; raise `ValueError` on mismatch (mirrors the existing arbitration registry's validation pattern).

## 2. Schema + migration (`app/library.py`)

- [x] 2.1 Add `product_id TEXT` (no NOT NULL, no default) to the `CREATE TABLE templates` block in `SCHEMA`.
- [x] 2.2 In `Store._migrate()`, add an idempotent block that uses the existing `has_col("templates", "product_id")` pattern to ALTER TABLE add the column when missing.
- [x] 2.3 In `Store._migrate()`, after the deprecation/seed/re-rank loop, add an idempotent `DELETE FROM templates WHERE class_name IN (...) AND product_id IS NULL` purge using `PRODUCT_SCOPED_CLASSES` as the IN-list parameters.

## 3. Store API: scope-aware load / insert

- [x] 3.1 Change `Store.insert_template` signature to `(self, library_id: str, t: Template, *, product_id: str | None = None) -> None` and bind the new column in the INSERT statement.
- [x] 3.2 Change `Store.load_library` signature to `(self, library_id: str, *, product_id: str | None = None) -> tuple[...]`. Build the templates SELECT with `WHERE library_id = ?` when `product_id is None`, otherwise `WHERE library_id = ? AND (product_id IS NULL OR product_id = ?)`.
- [x] 3.3 Verify `delete_template` and `update_template_class` need no change (they key off `id` only).

## 4. Library facade (`app/library.py`, `class Library`)

- [x] 4.1 Add method `def add_template_for_file(self, tmpl: Template, *, product_id: str | None) -> None`. It SHALL compute `scope_pid = product_id if is_product_scoped(tmpl.class_name) else None`, then call `self.store.insert_template(self.library_id, tmpl, product_id=scope_pid)` and mirror the in-memory cache update that today's `add_template` does.
- [x] 4.2 Keep `add_template(tmpl)` as a thin wrapper that delegates to `add_template_for_file(tmpl, product_id=None)`. Existing tests and fixtures keep working.

## 5. Match worker plumbing (`app/jobs.py`)

- [x] 5.1 In every `store.load_library(library_id)` callsite, change to `store.load_library(library_id, product_id=rec.product_id)`. Confirmed callsites: `app/jobs.py:178` (prematch) and `app/jobs.py:734` (match). Also plumbed `product_id` through `_preprocess_worker` and `submit_preprocess`.
- [x] 5.2 Grep for any other `load_library(` callsite to make sure nothing was missed — only the `Library.__init__` constructor remains, which stays library-only by design (admin view).

## 6. API plumbing (`app/main.py`)

- [x] 6.1 In `/api/files/{file_id}/scan-all` (the `scan_all` handler near `app/main.py:1039`), the current call `LIBRARIES.get(rec.library_id)` returns a `Library` that was hydrated without product scope. Decide whether scan-all hydrates a *per-call* library view (call `Store.load_library(rec.library_id, product_id=rec.product_id)` directly) or hydrates the `Library` cache to be product-aware. **Implementation choice**: call the Store directly inside `scan_all` to get a scope-aware `(classes, configs, templates_by_class)` snapshot, bypassing the cached `Library`. Document inline that the cache is library-only for admin views.
- [x] 6.2 In `/api/files/{file_id}/commit`, replace the call sequence:
  - `lib = LIBRARIES.get(rec.library_id)`
  - `lib.add_template(tmpl)`
  with the new entry point:
  - `lib.add_template_for_file(tmpl, product_id=rec.product_id)`
- [x] 6.3 In the commit handler, **before** building the template, reject the request with HTTP 400 if `is_product_scoped(req.class_name) and rec.product_id is None`. Message: `"file is not bound to a product; cannot commit product-scoped class '<name>'"`.
- [x] 6.4 Confirm `/api/libraries/{id}/templates` continues to return only library-scoped templates (it goes through `Library.all_templates()` which iterates the in-memory cache — that cache is library-only by construction, so no change needed). Added inline docstring noting the contract.

## 7. Spec sync

- [x] 7.1 Confirm `openspec/changes/split-class-scope-library-vs-product/specs/template-library/spec.md` (already written) matches the implemented behavior word-for-word.

## 8. Tests (`tests/test_library.py`)

- [x] 8.1 Add `test_is_product_scoped_partition` asserting the 8 product-scoped names return True and a sampling of library-scoped names (`SMD-2T`, `FiducialCircle`, `Pin-1`, `2DBarcode`, a custom name `MyMarker`) return False.
- [x] 8.2 Add `test_product_scoped_classes_subset_of_defaults` asserting `PRODUCT_SCOPED_CLASSES <= set(DEFAULT_CLASSES)`.
- [x] 8.3 Add `test_load_library_default_is_library_scope_only` — insert one library-scoped `SMD-2T` template (`product_id=None`) and one product-scoped `Substrate` template (`product_id="p1"`), call `Store.load_library("default")` without a product_id, assert the `Substrate` list is empty.
- [x] 8.4 Add `test_load_library_with_product_id_merges_scopes` — same fixture, call `Store.load_library("default", product_id="p1")`, assert both `SMD-2T` (length 1) and `Substrate` (length 1) are present.
- [x] 8.5 Add `test_load_library_other_product_does_not_see_substrate` — same fixture, call `Store.load_library("default", product_id="p2")`, assert `Substrate` is empty.
- [x] 8.6 Add `test_insert_template_keyword_product_id_roundtrips` — round-trip via `load_library` confirms the column.
- [x] 8.7 Add `test_migration_purges_legacy_library_scope_product_class_rows` — seed a row with `product_id IS NULL` for a product-scoped class, re-open Store, assert the row is gone.
- [x] 8.8 Add `test_migration_purge_is_idempotent` — boot twice in succession, second boot leaves tables unchanged.

## 9. Tests (`tests/test_api.py`)

- [x] 9.1 `test_add_template_for_file_routes_library_scoped` — library-scoped class lands with `product_id IS NULL` even when a product_id is supplied.
- [x] 9.2 `test_add_template_for_file_routes_product_scoped` — product-scoped class lands with `product_id = "p1"`.
- [x] 9.3 `test_commit_product_scoped_class_on_orphan_file_400s` — HTTP test: 400 with message mentioning "not bound to a product" and the class name.
- [x] 9.4 `test_scan_all_isolates_product_scoped_templates` — same library, two products: file in p1 sees the Substrate template; file in p2 does not. SMD-2T (library-scoped) visible to both. Tests the underlying scope rule the scan-all endpoint depends on.

## 10. Verification

- [x] 10.1 Run `pytest tests/test_library.py tests/test_api.py tests/test_products.py -x` — passed (40 + 39 + N).
- [x] 10.2 Run `pytest -x` — **464 passed** (up from 452 baseline; 12 new tests added, no regressions).
- [ ] 10.3 Boot the app against an existing `data/library.sqlite` that has at least one `Substrate` template; confirm boot logs / first request show the Substrate template is gone, then re-commit through the UI and confirm it reappears only in the originating product — **deferred to user** (requires manual browser session against real data).
- [ ] 10.4 Open two products under the same library, commit `SMD-2T` in product A, confirm product B's toolbar reflects the same `SMD-2T` count — **deferred to user** (UI verification; covered by test 9.4 at the Store level).

## 11. OpenSpec finalization

- [x] 11.1 Run `openspec validate split-class-scope-library-vs-product --strict` — `Change 'split-class-scope-library-vs-product' is valid`.
- [ ] 11.2 After implementation merges and ships, archive the change with `/opsx:archive` (syncs the delta into the main `template-library` spec) — deferred to post-merge.

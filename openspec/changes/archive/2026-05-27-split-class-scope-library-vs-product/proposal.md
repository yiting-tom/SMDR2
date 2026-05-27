## Why

Today every template lives at **library scope**: an `SMD-2T` template
added to a library is shared by every product bound to that library —
which is the right behaviour for generic, reusable parts (SMDs,
fiducials, pin marks, barcodes). But the same scope is wrong for
**design-specific** classes: `Substrate`, `Lid`, `DieArea`, `C4Ball`,
`BGABall`, `Protrusion` — every product has its own substrate outline,
its own die area, its own ball pattern. Sharing those across products
within a library forces users to either keep one product per library
(defeating the library abstraction) or live with cross-contamination
where Product A's substrate template matches into Product B's DXF and
inflates counts.

## What Changes

- Introduce a **class-scope partition** in `app/library.py`:
  - **Library-scoped** (templates shared across every product in the
    library) — the 9 generic classes:
    `SMD-2T`, `SMD-3T`, `SMD-8T`, `SMD-14T`, `FiducialCircle`,
    `FiducialCross`, `FiducialSquare`, `Pin-1`, `2DBarcode`.
  - **Product-scoped** (templates shared only inside one product) —
    the 8 design-specific classes:
    `Substrate`, `Lid`, `LidOuter`, `LidInner`, `DieArea`, `C4Ball`,
    `BGABall`, `Protrusion`.
- Add a nullable `templates.product_id` column. `NULL` means
  library-scoped; a non-null value means product-scoped to that
  product. Schema migration is an idempotent `ALTER TABLE … ADD COLUMN`.
- `Store.load_library(library_id, *, product_id=None)` SHALL merge two
  result sets: rows where `product_id IS NULL` (library-scoped) and rows
  where `product_id = <given>` (product-scoped). Calling without
  `product_id` returns only library-scoped rows (used by library-admin
  listings).
- The match worker (`app/jobs.py`) SHALL pass `file_record.product_id`
  into `load_library`. The Scan All endpoint (`app/main.py`) SHALL do
  the same.
- The commit endpoint (`POST /api/files/{file_id}/commit`) SHALL persist
  a new template with `product_id = file.product_id` when the class is
  in the product-scoped set, and `product_id = NULL` otherwise. Custom
  user-added classes (not in `DEFAULT_CLASSES`) SHALL default to
  library-scoped for backwards compatibility.
- **Migration**: on boot the `Store._migrate()` pass SHALL delete every
  template row whose `class_name` is in the product-scoped set **and**
  whose `product_id IS NULL`. Idempotent. The user re-commits the
  affected templates per product. This is acceptable because the
  product-scoped classes have always been wrongly stored (one
  per-library template intended to mean "this product's substrate")
  and there is no automatic mapping from library to product (a library
  may have many products).

## Capabilities

### New Capabilities
<!-- none — this extends an existing capability -->

### Modified Capabilities
- `template-library`: introduces a per-class scope dimension
  (library vs. product). Schema gains `templates.product_id`. The
  default-class seeding requirement is unchanged in *which* classes
  exist, but a new requirement defines *how each class's templates are
  scoped*. `load_library` gains an optional `product_id` parameter and
  its semantics shift from "all templates in this library" to "all
  library-scoped templates plus all product-scoped templates for the
  given product, if any". `Template CRUD` gains the rule that the
  scope is derived from `class_name`, not chosen by the caller.

## Impact

- **Code**:
  - `app/library.py` — new `PRODUCT_SCOPED_CLASSES` frozenset,
    `is_product_scoped()` helper, schema with new column, migration
    cleanup of stale library-scoped rows for product-scoped classes,
    `insert_template`/`load_library` signature changes,
    `Library.add_template` routing.
  - `app/jobs.py` — every `store.load_library(library_id)` callsite
    (prematch + Match phases) gains `product_id=rec.product_id`.
  - `app/main.py` — `/api/files/{file_id}/scan-all` and
    `/api/files/{file_id}/commit` thread `product_id` through; library
    listing endpoints (`/api/libraries/{id}/classes`,
    `/api/libraries/{id}/templates`) keep their library-only semantics
    so library admins still see only library-scoped templates.
- **Spec**: `openspec/specs/template-library/spec.md` —
  add `### Requirement: Per-class storage scope`, update
  `### Requirement: Template CRUD` (commit routes by class), and update
  `### Requirement: SQLite persistence` (new column, new migration step).
- **DB migration**:
  - One idempotent `ALTER TABLE templates ADD COLUMN product_id TEXT`
    (NULL default).
  - One idempotent purge of rows where
    `class_name IN PRODUCT_SCOPED_CLASSES AND product_id IS NULL`.
  - No new index needed for v1 (volume is small); a future change MAY
    add `CREATE INDEX IF NOT EXISTS idx_templates_lib_prod_class
    ON templates(library_id, product_id, class_name)` if scan-all
    perf becomes a concern.
- **DRC / rule check**: **no change**. DRC consumes Match JSON
  downstream; whatever templates the matcher loaded propagates through
  naturally via the existing pipeline.
- **UI / toolbar**: **no change**. Toolbar still shows all 17 classes;
  the scope dimension is invisible to the user. Counts on toolbar
  chips will reflect *what's visible in the current product context*
  (library-scoped count + this product's count for product-scoped
  classes).
- **Breaking change for product-scoped templates**: any existing
  templates for the 8 product-scoped classes are dropped on next boot.
  Documented in the migration plan; the call has already been signed
  off by the user with the clean-slate option.
- **Out of scope**: cross-product borrowing of product-scoped templates;
  toolbar UI changes; per-class strategy duplication per product;
  product-scoped custom user classes.

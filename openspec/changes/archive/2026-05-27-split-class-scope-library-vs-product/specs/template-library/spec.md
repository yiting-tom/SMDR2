## ADDED Requirements

### Requirement: Per-class storage scope (library vs. product)

The library SHALL partition its 17 default classes into two
storage-scope tiers via a module-level constant
`library.PRODUCT_SCOPED_CLASSES: frozenset[str]`.

**Product-scoped (8)** — templates SHALL be stored per-product;
templates of one product SHALL NOT be visible to any other product
sharing the same library:

- `Substrate`
- `Lid`
- `LidOuter`
- `LidInner`
- `DieArea`
- `C4Ball`
- `BGABall`
- `Protrusion`

**Library-scoped (the remaining 9 defaults plus any custom user
class)** — templates SHALL be stored per-library and SHALL be visible
to every product bound to that library:

- `SMD-2T`, `SMD-3T`, `SMD-8T`, `SMD-14T`
- `FiducialCircle`, `FiducialCross`, `FiducialSquare`
- `Pin-1`
- `2DBarcode`
- any class added by the user that is not in `PRODUCT_SCOPED_CLASSES`

The system SHALL expose a helper
`library.is_product_scoped(class_name: str) -> bool` returning `True`
iff `class_name in PRODUCT_SCOPED_CLASSES`.

The `templates` table SHALL carry a nullable `product_id` column. A
template's scope SHALL be encoded by that column: `NULL` means
library-scoped, a non-null value means product-scoped to that product.
The class's membership in `PRODUCT_SCOPED_CLASSES` SHALL be the only
input that determines which value is written; the caller SHALL NOT
choose the scope independently of the class.

#### Scenario: Helper reflects the partition
- **WHEN** `is_product_scoped` is called for `"Substrate"`, `"C4Ball"`,
  `"BGABall"`, `"DieArea"`, `"Protrusion"`, `"Lid"`, `"LidOuter"`, or
  `"LidInner"`
- **THEN** it returns `True`
- **AND** for `"SMD-2T"`, `"FiducialCircle"`, `"FiducialSquare"`,
  `"Pin-1"`, `"2DBarcode"`, or any custom class name
- **THEN** it returns `False`

#### Scenario: Library-scoped templates are visible to every product in the library
- **WHEN** a template T is committed to library L through a file
  belonging to product A with class `SMD-2T`
- **AND** a file in product B (same library L) loads its library
- **THEN** T appears in the loaded `templates_by_class["SMD-2T"]`
  list for the file in product B

#### Scenario: Product-scoped templates are isolated to their product
- **WHEN** a template T is committed to library L through a file
  belonging to product A with class `Substrate`
- **AND** a file in product B (same library L) loads its library
- **THEN** T does NOT appear in the loaded `templates_by_class["Substrate"]`
  list for the file in product B
- **AND** a file in product A loading its library DOES see T in
  `templates_by_class["Substrate"]`

### Requirement: Scope-aware template loading

`Store.load_library` SHALL accept an optional keyword-only
`product_id: str | None = None` parameter. The returned
`templates_by_class` dict SHALL contain:

- every template row where `library_id = <given>` AND `product_id IS NULL`
  (library-scoped), and
- every template row where `library_id = <given>` AND `product_id = <given product_id>`
  (the current product's product-scoped templates) — but only when
  `product_id` is non-null at the call site.

Calling `load_library(library_id)` without a `product_id` SHALL return
ONLY the library-scoped rows. This is the library-admin view (used by
`GET /api/libraries/{id}/templates`).

The match worker (`app/jobs.py`) and the Scan All endpoint
(`app/main.py`) SHALL pass the file record's `product_id` into
`load_library`. The library-admin endpoints SHALL NOT.

#### Scenario: load_library without product_id returns library-scoped only
- **WHEN** the library has one library-scoped `SMD-2T` template and
  one product-scoped `Substrate` template (any product)
- **AND** `Store.load_library("default")` is called with no product_id
- **THEN** the returned `templates_by_class["SMD-2T"]` has length 1
- **AND** the returned `templates_by_class["Substrate"]` has length 0

#### Scenario: load_library with product_id merges scopes
- **WHEN** the library has one library-scoped `SMD-2T` template and
  one product-scoped `Substrate` template for product `p1`
- **AND** `Store.load_library("default", product_id="p1")` is called
- **THEN** the returned `templates_by_class["SMD-2T"]` has length 1
- **AND** the returned `templates_by_class["Substrate"]` has length 1

#### Scenario: load_library with a different product_id excludes other products' templates
- **WHEN** the library has one product-scoped `Substrate` template for
  product `p1` and none for product `p2`
- **AND** `Store.load_library("default", product_id="p2")` is called
- **THEN** the returned `templates_by_class["Substrate"]` has length 0

### Requirement: Commit routes templates by class scope

`POST /api/files/{file_id}/commit` SHALL persist new templates with
their `product_id` derived from the file and the class:

- if `is_product_scoped(class_name)` is `True`, the new template's
  `product_id` SHALL equal the file's `product_id`;
- otherwise the new template's `product_id` SHALL be `NULL`
  (library-scoped).

A file with a NULL `product_id` SHALL NOT be allowed to commit a
template for a product-scoped class — the API SHALL return HTTP 400
with a clear error message ("file is not bound to a product; cannot
commit product-scoped class").

#### Scenario: Commit on a library-scoped class lands library-scoped
- **WHEN** a file bound to product `p1` commits handles to class
  `SMD-2T`
- **THEN** the new `templates` row has `product_id IS NULL`

#### Scenario: Commit on a product-scoped class lands product-scoped
- **WHEN** a file bound to product `p1` commits handles to class
  `Substrate`
- **THEN** the new `templates` row has `product_id = "p1"`

#### Scenario: Commit on a product-scoped class without a product fails
- **WHEN** a file with `product_id IS NULL` POSTs to
  `/api/files/{id}/commit` with `class_name = "C4Ball"`
- **THEN** the API returns HTTP 400 with a body explaining that the
  file must be bound to a product

## MODIFIED Requirements

### Requirement: Template CRUD

The library SHALL support creating, listing, deleting and moving
templates between classes within the same library. Creation SHALL
follow the per-class storage scope rule (see
`Per-class storage scope` above): library-scoped classes persist
templates with `product_id IS NULL`; product-scoped classes persist
with `product_id = <file's product>`. Deletion and class-rename
operations SHALL operate by template primary key and SHALL NOT change
a template's scope.

#### Scenario: Commit creates a template under a file's library
- **WHEN** the user posts handles to `POST /api/files/{id}/commit` with a class name
- **THEN** the template is added to the file's library, not any other library
- **AND** the response carries the new template id and the bound `library_id`

#### Scenario: Commit honours per-class storage scope
- **WHEN** the user commits handles to a product-scoped class
- **THEN** the persisted row has `product_id = file.product_id`
- **AND** when the user commits handles to a library-scoped class
- **THEN** the persisted row has `product_id IS NULL`

#### Scenario: Delete removes a template
- **WHEN** `DELETE /api/templates/{template_id}` is called
- **THEN** the template no longer appears in any library's listing
- **AND** the change is persistent across server restart

#### Scenario: Move template across classes
- **WHEN** `PATCH /api/templates/{template_id}` is called with a new `class_name`
- **THEN** the template is moved into that class within its current library
- **AND** the new class is auto-created in the library if missing
- **AND** the template's `product_id` is left unchanged (a class
  rename does not implicitly re-scope the row)

### Requirement: SQLite persistence with migration from pre-multi-library schema

Library state SHALL persist to `data/library.sqlite` and SHALL survive
server restarts. The store SHALL detect databases predating the
multi-library schema and migrate them in-place by:
1. creating the `libraries` table and ensuring the `default` library exists,
2. rebuilding the `classes` table with composite primary key `(library_id, name)`,
3. adding `library_id` to the `templates` table and rebuilding it to
   drop the stale `class_name → classes(name)` foreign key,
4. adding `library_id` to the `files` table,
5. adding the nullable `product_id` column to the `templates` table
   (idempotent — gated on a `PRAGMA table_info(templates)` check),
6. on every boot — idempotently — purging classes listed in
   `DEPRECATED_CLASSES` (and any templates filed under them),
   seeding any missing entry from `DEFAULT_CLASSES` for every existing
   library, re-ranking every library's `classes` rows so the
   `rank` column tracks the current `DEFAULT_CLASSES` order, AND
   deleting every `templates` row whose `class_name IN
   PRODUCT_SCOPED_CLASSES AND product_id IS NULL` (these are the
   library-scope-mis-stored templates from before the class-scope
   split — the user re-commits them per product after the migration).
   Classes added by the user that are not in `DEFAULT_CLASSES` SHALL
   be pushed after the canonical rows, preserving their relative order.

#### Scenario: Round-trip persistence
- **WHEN** a template is added and the application is restarted
- **THEN** the template appears in `GET /api/libraries/{id}/templates` after restart

#### Scenario: Legacy DB is migrated
- **WHEN** the Store opens a SQLite file that lacks the `libraries` table
- **THEN** the migration runs without raising
- **AND** all pre-existing classes and templates are tagged with `library_id = "default"`

#### Scenario: Deprecation purge is idempotent
- **WHEN** the Store boots twice in succession against the same DB
- **THEN** the second boot leaves the `classes` and `templates` tables
  unchanged

#### Scenario: Re-rank places new defaults at their canonical position
- **WHEN** a library is missing `FiducialCircle` and `FiducialCross` at
  boot time
- **THEN** after migration both rows exist
- **AND** their `rank` values place them at positions 7 and 8 in the
  ordered class listing, between `DieArea` and `SMD-2T`

#### Scenario: First boot drops library-scoped product-class templates
- **WHEN** the Store boots against a DB that contains a `templates`
  row with `class_name = "Substrate"` and `product_id IS NULL`
- **THEN** after migration that row no longer exists
- **AND** a re-boot leaves the table unchanged (idempotent)

#### Scenario: Migration adds product_id column idempotently
- **WHEN** the Store boots against a DB whose `templates` table has
  no `product_id` column
- **THEN** after migration the column exists and is `NULL` for every
  pre-existing row
- **AND** a second boot does not re-issue the `ALTER TABLE`

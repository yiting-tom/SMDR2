# template-library Specification (delta)

## MODIFIED Requirements

### Requirement: Multi-library template store

The system SHALL support multiple template libraries identified by
unique `library_id`. Each library SHALL have its own set of classes and
templates; no template SHALL be shared across libraries. Each library
SHALL belong to exactly one version (`versions.library_id`, 1:1);
libraries SHALL be created only as part of version creation (first
version of a product, or clone-on-new-version) and SHALL be deleted
only by the cascade of deleting their product. There SHALL be no
standalone library CRUD API and no shared `default` library.

#### Scenario: Templates in one library do not leak into another
- **WHEN** template T is added to library A
- **AND** library B is queried for class T.class_name
- **THEN** library B's count for that class is zero

#### Scenario: Library lifecycle is bound to its version
- **WHEN** a new version is created for a product
- **THEN** exactly one new library exists, referenced by that version
- **AND** when the product is deleted, the library and its templates are removed

### Requirement: Template CRUD

The library SHALL support creating, listing, deleting and moving
templates between classes within the same library. Every template row
SHALL belong to the library of exactly one version; there SHALL be no
scope column and no scope routing. Deletion and class-rename
operations SHALL operate by template primary key. All template
mutations SHALL be rejected with HTTP 409 when the owning version is
signed off.

#### Scenario: Commit creates a template under the version's library
- **WHEN** the user posts handles to `POST /api/files/{id}/commit`
  with a class name and a `version_id`
- **THEN** the template is added to that version's library, not any other library
- **AND** the response carries the new template id and the bound `library_id`

#### Scenario: Delete removes a template
- **WHEN** `DELETE /api/templates/{template_id}` is called
- **THEN** the template no longer appears in any library's listing
- **AND** the change is persistent across server restart

#### Scenario: Move template across classes
- **WHEN** `PATCH /api/templates/{template_id}` is called with a new `class_name`
- **THEN** the template is moved into that class within its current library
- **AND** the new class is auto-created in the library if missing

#### Scenario: Mutations on a signed-off version's library are blocked
- **WHEN** the owning version is signed off
- **AND** the client attempts commit, delete, or move on its templates
- **THEN** each response is HTTP 409 and no row changes

### Requirement: SQLite persistence with migration from pre-multi-library schema

Library state SHALL persist to the SQLite database and SHALL survive
server restarts. The store SHALL detect databases predating the
versioned schema (any database lacking the `versions` table) and SHALL
rebuild the schema from scratch — dropping and recreating `libraries`,
`classes`, `templates`, `files`, `products`, and creating `versions`
and `version_files`. No legacy data SHALL be preserved (decision C9:
existing data is development-only). The rebuild SHALL be idempotent —
a database already carrying the versioned schema SHALL boot without
modification. On every boot the store SHALL idempotently seed any
missing `DEFAULT_CLASSES` entry for every existing library and re-rank
class rows to track the `DEFAULT_CLASSES` order, pushing user-added
classes after the canonical rows in their existing relative order.

#### Scenario: Round-trip persistence
- **WHEN** a template is added and the application is restarted
- **THEN** the template appears in the version's library listing after restart

#### Scenario: Pre-versioning DB is rebuilt
- **WHEN** the Store opens a SQLite file that lacks the `versions` table
- **THEN** the schema is dropped and recreated without raising
- **AND** the resulting database is empty of products, versions, and templates

#### Scenario: Versioned DB boots unchanged
- **WHEN** the Store boots twice in succession against a versioned-schema DB
- **THEN** the second boot leaves all tables unchanged

#### Scenario: Class seeding stays idempotent per library
- **WHEN** a library is missing a class from `DEFAULT_CLASSES` at boot
- **THEN** after boot the class row exists at its canonical rank
- **AND** a re-boot leaves the `classes` table unchanged

### Requirement: Commit-time template deduplication by canonical signature

The library SHALL deduplicate templates at commit time using a canonical signature derived from each template's entity point sets. The signature SHALL be a pure function of the input point sets — translation-invariant, entity-order-invariant, and vertex-order-invariant; distinct under rotation, scale, and reflection. The signature SHALL bucket coordinates at a 10⁻⁴ mm grid (0.1 µm), parallel to the existing `_radius_bucket_key` precision.

The dedup scope SHALL be `(library_id, class_name)`. Because each
library belongs to exactly one version, this scope is naturally
per-version: the same shape committed in two different versions of the
same product produces one row in each version's library. Templates
with the same signature in a different class or different library
SHALL be considered distinct and SHALL produce new rows.

When a commit's canonical signature matches an existing template's signature within the same scope, the library SHALL short-circuit: NO new row is appended to the in-memory cache, NO row is inserted into the persistent store, and the existing template's id SHALL be returned to the caller.

`Library.add_template_for_file(template)` SHALL return `tuple[Template, bool]` where the second element is `True` when the call hit an existing template and `False` when a new row was inserted. `Library.add_template(template)` SHALL delegate as today and propagate the same tuple.

#### Scenario: Same point sets committed twice produces one row

- **WHEN** a template T1 is committed under class C in library L
- **AND** a template T2 with point sets equal to T1's (up to vertex order) is committed under the same class C in the same library L
- **THEN** the second `add_template_for_file` call returns `(T1_existing, True)`
- **AND** `lib.count(C)` is unchanged after the second call
- **AND** only ONE row exists in the `templates` table for the `(L, C)` tuple

#### Scenario: Same signature in different class is two rows

- **WHEN** a template with point sets P is committed under class C1
- **AND** a template with point sets P is committed under class C2 in the same library
- **THEN** both commits return `already_existed = False`
- **AND** each commit produces a distinct template id

#### Scenario: Same signature in different library is two rows

- **WHEN** a template with point sets P is committed under class C in library L1
- **AND** a template with point sets P is committed under class C in library L2 (another version's library)
- **THEN** both commits return `already_existed = False`

#### Scenario: Rotated copy is a new row

- **WHEN** a template T1 is committed under class C
- **AND** a 90°-rotated copy of T1 is committed under the same class C in the same scope
- **THEN** the second commit returns `already_existed = False`
- **AND** `lib.count(C)` increments by 1

#### Scenario: Translation-equivalent shape framed at a different on-canvas position deduplicates

- **WHEN** a template T1 is committed from a frame-select at one on-canvas location
- **AND** the operator frame-selects a different instance of the same shape elsewhere on the same DXF and commits under the same class
- **THEN** the second commit returns `already_existed = True`
- **AND** the response's `template_id` is T1's id

## REMOVED Requirements

### Requirement: Per-class storage scope (library vs. product)
**Reason**: 拓樸定案(2026-06-10):一 version 一 library、無任何共用範本。兩層 scope(`PRODUCT_SCOPED_CLASSES` / `templates.product_id`)整個消失,所有 class 一律屬於其版本的 library。
**Migration**: 刪除 `PRODUCT_SCOPED_CLASSES`、`is_product_scoped()`、`templates.product_id` 欄位與所有分支。無資料遷移(C9)。

### Requirement: Scope-aware template loading
**Reason**: 雙 scope merge 隨兩層 scope 一併消失;`load_library(library_id)` 單參數即回傳該版本 library 的全部範本。
**Migration**: 呼叫端(jobs.py、scan-all)從傳 `(library_id, product_id)` 改為傳 version 解析出的 `library_id` 一個值。

### Requirement: Commit routes templates by class scope
**Reason**: commit 不再依 class 路由 scope——一律寫入 version 的 library(見 MODIFIED「Template CRUD」)。
**Migration**: `POST /api/files/{id}/commit` 改吃必填 `version_id`,刪除 400「file is not bound to a product」分支。

### Requirement: Per-file library binding with reassignment
**Reason**: files 退化為純內容儲存,不再綁 library;library 隸屬 version,「換 library」語意不存在(等價操作 = 在另一版本工作)。
**Migration**: 刪除 `files.library_id` 與 `PATCH /api/files/{id}` 的 library 重綁分支;viewer 的 library 切換 UI 一併移除(見 viewer-ui delta)。

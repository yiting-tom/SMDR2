# template-library Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
### Requirement: Multi-library template store

The system SHALL support multiple template libraries identified by
unique `library_id`. Each library SHALL have its own set of classes and
templates; no template SHALL be shared across libraries. A `Default`
library (`library_id = "default"`) SHALL always exist and SHALL NOT be
deletable.

#### Scenario: Templates in one library do not leak into another
- **WHEN** template T is added to library A
- **AND** library B is queried for class T.class_name
- **THEN** library B's count for that class is zero

#### Scenario: Default library cannot be deleted
- **WHEN** the user calls `DELETE /api/libraries/default`
- **THEN** the API returns 400

### Requirement: Default class seeding

Every newly-created library SHALL be seeded with the following 17
canonical IC-packaging classes, in this order, and the order SHALL
be the toolbar / class-list order surfaced in the UI:

1. `Substrate`
2. `Pin-1`
3. `Lid`
4. `LidOuter`
5. `LidInner`
6. `DieArea`
7. `FiducialCircle`
8. `FiducialCross`
9. `FiducialSquare`
10. `SMD-2T`
11. `C4Ball`
12. `BGABall`
13. `Protrusion`
14. `2DBarcode`
15. `SMD-3T`
16. `SMD-8T`
17. `SMD-14T`

The trailing three SMD variants (`SMD-3T`, `SMD-8T`, `SMD-14T`)
SHALL be members of the viewer's collapsed-toolbar fold group so
the toolbar stays compact by default.

Two classes that previously appeared in the seed list SHALL be
deprecated and SHALL NOT be seeded into any new or existing
library: `FiducialMark` (superseded by the
`FiducialCircle` / `FiducialCross` / `FiducialSquare` family) and
`Side` (unused in practice).

#### Scenario: New library has the 17 default classes in canonical order
- **WHEN** the user creates a new library via `POST /api/libraries`
- **THEN** `GET /api/libraries/{id}/classes` returns the 17 names listed above
- **AND** the names appear in the listed order (Substrate first, SMD-14T last)
- **AND** `C4Ball` appears immediately before `BGABall`
- **AND** `FiducialSquare` appears immediately after `FiducialCross`

#### Scenario: Deprecated classes are not seeded
- **WHEN** a new library is created
- **THEN** the returned class list contains neither `FiducialMark` nor `Side`

#### Scenario: Existing library converges to the new defaults on boot
- **WHEN** a Store boots against a DB whose `default` library still has the
  legacy class set (`SMD-2T, Substrate, …, FiducialMark, Side, …`)
- **THEN** the migration drops every template filed under `FiducialMark`
  or `Side`
- **AND** drops the `FiducialMark` and `Side` rows from `classes`
- **AND** seeds the missing defaults (`FiducialCircle`, `FiducialCross`,
  `FiducialSquare`, `C4Ball`)
- **AND** re-ranks the surviving rows so they match the canonical order

#### Scenario: Boot seeds C4Ball into existing libraries
- **WHEN** a Store boots against a DB whose libraries already have the
  pre-`C4Ball` canonical set (15 classes, no `C4Ball` row)
- **THEN** after migration every library has a `C4Ball` class row
- **AND** its `rank` places it immediately before `BGABall` in the
  ordered class listing

#### Scenario: Boot seeds FiducialSquare into existing libraries
- **WHEN** a Store boots against a DB whose libraries already have the
  pre-`FiducialSquare` canonical set (16 classes, no `FiducialSquare`
  row)
- **THEN** after migration every library has a `FiducialSquare` class
  row
- **AND** its `rank` places it immediately after `FiducialCross` in the
  ordered class listing

### Requirement: Per-file library binding with reassignment

Each file SHALL be bound to exactly one `library_id`. The bound library
SHALL be selectable at upload time via the `library_id` form field
(default: `default`). `PATCH /api/files/{file_id}` with
`{"library_id": "<new-id>"}` SHALL reassign the file and re-trigger
preprocessing so the pre-match overlay reflects the new library's
templates.

#### Scenario: Switching a file's library re-preprocesses
- **WHEN** a file's library is reassigned via PATCH
- **THEN** its status becomes `preprocessing`
- **AND** a new preprocess job is submitted with the new `library_id`
- **AND** after completion the prematch JSON reflects the new library's templates

#### Scenario: Switching to the same library is a no-op
- **WHEN** PATCH is called with the file's existing library_id
- **THEN** the response carries `unchanged: true`
- **AND** no new job is submitted

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

### Requirement: Display name vs. match-JSON key separation

Every class SHALL have two stable identifiers:

- a **display ID** used in the database, viewer toolbar, API
  responses about templates, and user-facing labels — the
  CamelCase / hyphenated form (`Substrate`, `Pin-1`, `BGABall`,
  `C4Ball`, `SMD-2T`, `FiducialCircle`, `FiducialSquare`, …);
- a **match-JSON key** used inside `data/match/{file_id}.json` and
  any downstream consumer that reads that file — the snake_case
  identifier-safe form (`substrate`, `pin_1`, `bga_ball`, `c4_ball`,
  `smd_2t`, `fiducial_circle`, `fiducial_square`, …).

The mapping SHALL be defined by `library.CLASS_JSON_KEY` and SHALL
be applied wherever match-JSON keys are constructed. Other layers
(viewer, library API, UI hotkey labels, color map) SHALL continue
to use the display ID.

| Display ID       | Match-JSON key    |
|------------------|-------------------|
| `Substrate`      | `substrate`       |
| `Pin-1`          | `pin_1`           |
| `Lid`            | `lid`             |
| `LidOuter`       | `lid_outer`       |
| `LidInner`       | `lid_inner`       |
| `DieArea`        | `die_area`        |
| `FiducialCircle` | `fiducial_circle` |
| `FiducialCross`  | `fiducial_cross`  |
| `FiducialSquare` | `fiducial_square` |
| `SMD-2T`         | `smd_2t`          |
| `C4Ball`         | `c4_ball`         |
| `BGABall`        | `bga_ball`        |
| `2DBarcode`      | `2d_barcode`      |
| `SMD-3T`         | `smd_3t`          |
| `SMD-8T`         | `smd_8t`          |
| `SMD-14T`        | `smd_14t`         |

A class added by the user that is not in this table SHALL fall back
to using its display ID verbatim as the match-JSON key.

#### Scenario: BGABall match JSON uses snake_case key
- **WHEN** a library contains one `BGABall` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the saved JSON contains the key `bga_ball.0`
- **AND** the saved JSON does NOT contain the key `BGABall.0`

#### Scenario: C4Ball match JSON uses snake_case key
- **WHEN** a library contains one `C4Ball` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the saved JSON contains the key `c4_ball.0`
- **AND** the saved JSON does NOT contain the key `C4Ball.0`

#### Scenario: FiducialSquare match JSON uses snake_case key
- **WHEN** a library contains one `FiducialSquare` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the saved JSON contains the key `fiducial_square.0`
- **AND** the saved JSON does NOT contain the key `FiducialSquare.0`

#### Scenario: Display ID is preserved in library APIs
- **WHEN** the user fetches `GET /api/libraries/default/classes`
- **THEN** the response lists `BGABall` (display ID), not `bga_ball`
- **AND** the response lists `C4Ball` (display ID), not `c4_ball`
- **AND** the response lists `FiducialSquare` (display ID), not
  `fiducial_square`

#### Scenario: Custom class falls through unchanged
- **WHEN** the user has added a custom class named `MyMarker` and
  saves a match JSON
- **THEN** the saved JSON keys use `MyMarker.<idx>` (or the
  side-prefixed variant) verbatim

### Requirement: Per-class view constraint registry

The system SHALL expose a data-driven registry
`library.CLASS_VIEW_CONSTRAINTS: dict[str, frozenset[str]]` that
maps a class **display ID** to the frozen set of allowed view
prefixes (`"top_view"`, `"bottom_view"`, `"side_view"`), encoding
the physical fact that some IC-packaging classes only appear in
specific views (e.g., a C4 bump only appears in the chip's
top-down view; a BGA ball only appears in the package's bottom or
side cross-section view).

The registry SHALL include at minimum:

| Display ID | Allowed views                  |
|------------|--------------------------------|
| `C4Ball`   | `{"top_view"}`                 |
| `BGABall`  | `{"bottom_view", "side_view"}` |

A class whose display ID is **absent** from the registry SHALL be
treated as unconstrained (matches in any view, including unassigned,
are allowed).

A class whose display ID **is** in the registry SHALL be treated
strictly: the "unassigned" position (no view rectangle covers the
instance) is never allowed, even if no relevant view rectangle is set
on the file. The match in that case is dropped, not preserved.

The system SHALL expose a helper
`library.is_allowed_view(class_name: str, view: str | None) -> bool`
returning `True` when the `(class_name, view)` pair is permitted under
the rule above. Both the match-JSON serialiser (see `dxf-pipeline`)
and the viewer's Scan All overlay (see `viewer-ui`) SHALL use this
helper as their single oracle.

#### Scenario: Unconstrained class admits every view
- **WHEN** `CLASS_VIEW_CONSTRAINTS` does not contain `"Substrate"`
- **THEN** `is_allowed_view("Substrate", "top_view")` returns `True`
- **AND** `is_allowed_view("Substrate", "bottom_view")` returns `True`
- **AND** `is_allowed_view("Substrate", "side_view")` returns `True`
- **AND** `is_allowed_view("Substrate", None)` returns `True`

#### Scenario: C4Ball is allowed only in top_view
- **WHEN** `CLASS_VIEW_CONSTRAINTS["C4Ball"] == frozenset({"top_view"})`
- **THEN** `is_allowed_view("C4Ball", "top_view")` returns `True`
- **AND** `is_allowed_view("C4Ball", "bottom_view")` returns `False`
- **AND** `is_allowed_view("C4Ball", "side_view")` returns `False`
- **AND** `is_allowed_view("C4Ball", None)` returns `False`

#### Scenario: BGABall is allowed only in bottom_view and side_view
- **WHEN** `CLASS_VIEW_CONSTRAINTS["BGABall"] == frozenset({"bottom_view", "side_view"})`
- **THEN** `is_allowed_view("BGABall", "bottom_view")` returns `True`
- **AND** `is_allowed_view("BGABall", "side_view")` returns `True`
- **AND** `is_allowed_view("BGABall", "top_view")` returns `False`
- **AND** `is_allowed_view("BGABall", None)` returns `False`

#### Scenario: Constrained class with unassigned position is rejected
- **WHEN** a file has no `top_view_rect` set
- **AND** a `C4Ball` match instance is therefore unassigned
- **THEN** `is_allowed_view("C4Ball", None)` returns `False`
- **AND** the instance SHALL be dropped by the match-JSON serialiser
  and by the Scan All overlay

### Requirement: Per-class neighbour-count rule registry

The system SHALL expose a data-driven registry
`library.CLASS_ARBITRATION_GROUPS` (see the `class-arbitration`
capability for the full schema) co-located with `CLASS_VIEW_CONSTRAINTS`
in `app/library.py`.

The library SHALL seed at least one default group whose members are
`{"BGABall", "FiducialCircle"}` with the rules and fallback configured by
the `class-arbitration` spec. Both members are already part of
`DEFAULT_CLASSES`, so no additional class-seeding migration is needed:
existing libraries on disk already carry both member classes via the
existing seed-on-boot logic (`Default class seeding` requirement above).

The library module SHALL expose a helper
`library.arbitration_group_for(class_name: str) -> ArbitrationGroup | None`
that returns the (unique) group containing `class_name`, or `None` when
the class is not part of any group. A class display ID SHALL belong to
at most one group; constructing the registry with a class in two groups
SHALL fail at import time with a clear `ValueError`.

If both `CLASS_VIEW_CONSTRAINTS` and `CLASS_ARBITRATION_GROUPS` apply to
a class, the view-constraint check SHALL remain the final gate: after
arbitration reassigns an instance, its new class's view rule is
re-checked, and the instance is dropped if disallowed
(see `class-arbitration` spec's "Integration with Match JSON
serialisation" requirement for the exact ordering).

#### Scenario: Default seed includes BGA/Fiducial group
- **WHEN** the application boots
- **THEN** `CLASS_ARBITRATION_GROUPS` contains a group whose `members`
  equals `frozenset({"BGABall", "FiducialCircle"})`

#### Scenario: Lookup helper returns the containing group
- **WHEN** `arbitration_group_for("BGABall")` is called
- **THEN** the returned group's `members` contains both `"BGABall"`
  and `"FiducialCircle"`
- **AND** `arbitration_group_for("Substrate")` returns `None`

#### Scenario: A class cannot appear in two groups
- **WHEN** the registry is constructed with `"BGABall"` listed in two
  separate `ArbitrationGroup` entries
- **THEN** import-time validation SHALL raise `ValueError`
  naming the conflicting class

#### Scenario: JS drift guard mirrors the Python registry
- **WHEN** any UI affordance under `app/static/canvas.js` is added
  that consumes the arbitration registry (the full group structure,
  or a derived view such as the flat set of arbitration member
  class names)
- **THEN** the JS literal SHALL be wrapped in
  `// <NAME>_BEGIN ... // <NAME>_END` sentinel comments where
  `<NAME>` matches the JS constant's identifier (for example
  `CLASS_ARBITRATION_GROUPS_BEGIN/_END` for a full-structure mirror,
  or `CLASS_ARBITRATION_MEMBERS_BEGIN/_END` for the
  members-only derived view used by
  `commitCurrentTemplate`'s incremental-overlay fallback)
- **AND** a test under `tests/test_canvas_constants.py` SHALL parse
  the JS literal and assert structural equality with the Python
  registry's relevant subset, failing the build on drift
- **AND** the JS may be a strict subset of the Python registry's
  fields (e.g., omit fields the UI does not need); the equality SHALL
  be checked over the fields the JS chooses to expose

#### Scenario: Members-only JS mirror is allowed
- **WHEN** the viewer needs only the flat set of arbitration member
  class names (not the rules / pitch / default-class details)
- **THEN** a constant `CLASS_ARBITRATION_MEMBERS` SHALL be wrapped in
  `// CLASS_ARBITRATION_MEMBERS_BEGIN ... // CLASS_ARBITRATION_MEMBERS_END`
  sentinels in `app/static/canvas.js`
- **AND** the drift-guard test SHALL build the same flat union from
  `CLASS_ARBITRATION_GROUPS[*].members` on the Python side and
  assert equality with the JS array


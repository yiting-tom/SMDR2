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

Every newly-created library SHALL be seeded with the following 15
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
9. `SMD-2T`
10. `BGABall`
11. `Protrusion`
12. `2DBarcode`
13. `SMD-3T`
14. `SMD-8T`
15. `SMD-14T`

The trailing three SMD variants (`SMD-3T`, `SMD-8T`, `SMD-14T`)
SHALL be members of the viewer's collapsed-toolbar fold group so
the toolbar stays compact by default.

Two classes that previously appeared in the seed list SHALL be
deprecated and SHALL NOT be seeded into any new or existing
library: `FiducialMark` (superseded by the `FiducialCircle` /
`FiducialCross` split) and `Side` (unused in practice).

#### Scenario: New library has the 15 default classes in canonical order
- **WHEN** the user creates a new library via `POST /api/libraries`
- **THEN** `GET /api/libraries/{id}/classes` returns the 15 names listed above
- **AND** the names appear in the listed order (Substrate first, SMD-14T last)

#### Scenario: Deprecated classes are not seeded
- **WHEN** a new library is created
- **THEN** the returned class list contains neither `FiducialMark` nor `Side`

#### Scenario: Existing library converges to the new defaults on boot
- **WHEN** a Store boots against a DB whose `default` library still has the
  legacy class set (`SMD-2T, Substrate, …, FiducialMark, Side, …`)
- **THEN** the migration drops every template filed under `FiducialMark`
  or `Side`
- **AND** drops the `FiducialMark` and `Side` rows from `classes`
- **AND** seeds the missing defaults (`FiducialCircle`, `FiducialCross`)
- **AND** re-ranks the surviving rows so they match the canonical order

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
templates between classes within the same library.

#### Scenario: Commit creates a template under a file's library
- **WHEN** the user posts handles to `POST /api/files/{id}/commit` with a class name
- **THEN** the template is added to the file's library, not any other library
- **AND** the response carries the new template id and the bound `library_id`

#### Scenario: Delete removes a template
- **WHEN** `DELETE /api/templates/{template_id}` is called
- **THEN** the template no longer appears in any library's listing
- **AND** the change is persistent across server restart

#### Scenario: Move template across classes
- **WHEN** `PATCH /api/templates/{template_id}` is called with a new `class_name`
- **THEN** the template is moved into that class within its current library
- **AND** the new class is auto-created in the library if missing

### Requirement: SQLite persistence with migration from pre-multi-library schema

Library state SHALL persist to `data/library.sqlite` and SHALL survive
server restarts. The store SHALL detect databases predating the
multi-library schema and migrate them in-place by:
1. creating the `libraries` table and ensuring the `default` library exists,
2. rebuilding the `classes` table with composite primary key `(library_id, name)`,
3. adding `library_id` to the `templates` table and rebuilding it to
   drop the stale `class_name → classes(name)` foreign key,
4. adding `library_id` to the `files` table,
5. on every boot — idempotently — purging classes listed in
   `DEPRECATED_CLASSES` (and any templates filed under them),
   seeding any missing entry from `DEFAULT_CLASSES` for every existing
   library, and re-ranking every library's `classes` rows so the
   `rank` column tracks the current `DEFAULT_CLASSES` order. Classes
   added by the user that are not in `DEFAULT_CLASSES` SHALL be
   pushed after the canonical rows, preserving their relative order.

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

### Requirement: Display name vs. match-JSON key separation

Every class SHALL have two stable identifiers:

- a **display ID** used in the database, viewer toolbar, API
  responses about templates, and user-facing labels — the
  CamelCase / hyphenated form (`Substrate`, `Pin-1`, `BGABall`,
  `SMD-2T`, `FiducialCircle`, …);
- a **match-JSON key** used inside `data/match/{file_id}.json` and
  any downstream consumer that reads that file — the snake_case
  identifier-safe form (`substrate`, `pin_1`, `bga_ball`, `smd_2t`,
  `fiducial_circle`, …).

The mapping SHALL be defined by `library.CLASS_JSON_KEY` and SHALL
be applied wherever match-JSON keys are constructed. Other layers
(viewer, library API, UI hotkey labels, color map) SHALL continue
to use the display ID.

| Display ID       | Match-JSON key   |
|------------------|------------------|
| `Substrate`      | `substrate`      |
| `Pin-1`          | `pin_1`          |
| `Lid`            | `lid`            |
| `LidOuter`       | `lid_outer`      |
| `LidInner`       | `lid_inner`      |
| `DieArea`        | `die_area`       |
| `FiducialCircle` | `fiducial_circle`|
| `FiducialCross`  | `fiducial_cross` |
| `SMD-2T`         | `smd_2t`         |
| `BGABall`        | `bga_ball`       |
| `2DBarcode`      | `2d_barcode`     |
| `SMD-3T`         | `smd_3t`         |
| `SMD-8T`         | `smd_8t`         |
| `SMD-14T`        | `smd_14t`        |

A class added by the user that is not in this table SHALL fall back
to using its display ID verbatim as the match-JSON key.

#### Scenario: BGABall match JSON uses snake_case key
- **WHEN** a library contains one `BGABall` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the saved JSON contains the key `bga_ball.0`
- **AND** the saved JSON does NOT contain the key `BGABall.0`

#### Scenario: Display ID is preserved in library APIs
- **WHEN** the user fetches `GET /api/libraries/default/classes`
- **THEN** the response lists `BGABall` (display ID), not `bga_ball`

#### Scenario: Custom class falls through unchanged
- **WHEN** the user has added a custom class named `MyMarker` and
  saves a match JSON
- **THEN** the saved JSON keys use `MyMarker.<idx>` (or the
  side-prefixed variant) verbatim


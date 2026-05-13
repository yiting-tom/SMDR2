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

Every newly-created library SHALL be seeded with the 9 canonical
IC-packaging classes: `smd`, `substrate`, `die_area`, `lid_outer`,
`lid_inner`, `bga_ball`, `pin_mark`, `fiducial_mark`, `2d_barcode`.

#### Scenario: New library has default classes
- **WHEN** the user creates a new library via `POST /api/libraries`
- **THEN** `GET /api/libraries/{id}/classes` returns at least the 9 default class names

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
4. adding `library_id` to the `files` table.

#### Scenario: Round-trip persistence
- **WHEN** a template is added and the application is restarted
- **THEN** the template appears in `GET /api/libraries/{id}/templates` after restart

#### Scenario: Legacy DB is migrated
- **WHEN** the Store opens a SQLite file that lacks the `libraries` table
- **THEN** the migration runs without raising
- **AND** all pre-existing classes and templates are tagged with `library_id = "default"`


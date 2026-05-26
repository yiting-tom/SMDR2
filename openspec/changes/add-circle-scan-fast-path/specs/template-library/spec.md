## ADDED Requirements

### Requirement: Per-entity primitive kind persisted on Template

Each `Template` SHALL persist a parallel `entity_kinds: list[str |
None]` list of the same length as `entity_point_sets`. Each element
SHALL record the source primitive `type` (e.g., `"circle"`,
`"polyline"`, `"line"`) that the corresponding `entity_point_sets`
entry was collected from at commit time. When the source DXF handle
aggregates primitives of more than one type, the matching element
SHALL be `None` (mixed-kind).

`Template.entity_kinds` SHALL round-trip through SQLite persistence
via a new `entity_kinds TEXT` column on the `templates` table,
JSON-encoded. The `LibraryStore` initialisation SHALL perform an
in-place migration for databases predating this change: when
`PRAGMA table_info(templates)` does not include `entity_kinds`, the
column SHALL be added with `ALTER TABLE templates ADD COLUMN
entity_kinds TEXT` and defaulted to `NULL`. Legacy rows with `NULL`
SHALL be read back as `[None] * len(entity_point_sets)` so existing
templates remain functional.

`POST /api/files/{file_id}/commit` SHALL capture
`entity_kinds = [collect_entity_kinds(primitives, handle_index, h)
for h in handles]` from the drawing's primitives and pass them into
`Template.from_entities`.

#### Scenario: Committing a CIRCLE template records kind="circle"
- **WHEN** the user posts a single CIRCLE handle to `POST /api/files/{id}/commit`
- **THEN** the resulting `Template.entity_kinds` equals `["circle"]`
- **AND** the value survives a server restart (round-trip via SQLite)

#### Scenario: Committing a multi-entity polyline+circle template records both kinds
- **WHEN** the user posts two handles — one CIRCLE and one polyline
- **THEN** `Template.entity_kinds` is `["circle", "polyline"]` in the
  same order as `entity_point_sets`

#### Scenario: Mixed-kind handle records None
- **WHEN** a committed handle aggregates a polyline and a circle primitive
- **THEN** the corresponding `entity_kinds` element is `None`

#### Scenario: Legacy library migrates in place on startup
- **WHEN** the server starts against a `library.sqlite` whose `templates`
  table lacks the `entity_kinds` column
- **THEN** the column is added via `ALTER TABLE` on startup
- **AND** existing templates load with `entity_kinds = [None] * len(entity_point_sets)`
- **AND** subsequent commits write real kind values into the new column

#### Scenario: Legacy template still scans correctly
- **WHEN** a template with `entity_kinds = [None]` is used in `scan_all`
- **THEN** `find_matches_from_pointsets` runs the generic path and
  returns the same matches as before this change (no fast-path
  acceleration, no regression)

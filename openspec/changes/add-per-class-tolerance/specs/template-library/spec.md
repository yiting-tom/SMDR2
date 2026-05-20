## ADDED Requirements

### Requirement: Per-class chamfer tolerance override

Each template class SHALL carry an optional chamfer-tolerance value
(in drawing units, typically mm). When a class's tolerance is set, every
scan that matches against templates filed under that class SHALL use the
class's value as the matcher's `tolerance` argument; when unset (NULL),
the global default (`TOLERANCE_ABS = 0.05`) applies. The class summary
endpoint SHALL surface the field. A dedicated endpoint SHALL set or
clear the value.

The tolerance field SHALL be:
- Stored on the `classes` table as a nullable REAL column named
  `tolerance`.
- Settable via `PUT /api/libraries/{library_id}/classes/{class_name}/tolerance`
  with body `{"tolerance": <number>}` to set or `{"tolerance": null}` to
  clear.
- Validated server-side: `tolerance` MUST be a positive finite number ≤
  100 (drawing units), or `null`. Other values SHALL respond HTTP 400.
- Exposed by `GET /api/libraries/{library_id}/classes` and the file-bound
  summary endpoint via a `tolerance` field on each class entry (number
  or `null`).
- Migration-safe: existing libraries get NULL on every row when the
  column is added, preserving pre-change behaviour.

#### Scenario: Newly-created class has NULL tolerance
- **WHEN** a new class is added to a library via `add_class`
- **THEN** the class's `tolerance` is `null` in `GET /api/libraries/{id}/classes`
- **AND** matching against templates in that class uses `TOLERANCE_ABS = 0.05`

#### Scenario: Set a per-class tolerance
- **WHEN** `PUT /api/libraries/{lib}/classes/Substrate/tolerance` with body `{"tolerance": 0.5}` is called
- **THEN** the response is HTTP 200
- **AND** subsequent `GET /api/libraries/{lib}/classes` returns `tolerance: 0.5` on the Substrate row
- **AND** subsequent matching against Substrate templates uses 0.5 as the chamfer tolerance

#### Scenario: Clear a per-class tolerance
- **WHEN** `PUT /api/libraries/{lib}/classes/Substrate/tolerance` with body `{"tolerance": null}` is called on a class with a previously-set value
- **THEN** the response is HTTP 200
- **AND** the class summary reports `tolerance: null`
- **AND** subsequent matching against Substrate templates uses `TOLERANCE_ABS` again

#### Scenario: Invalid tolerance values are rejected
- **WHEN** `PUT /api/libraries/{lib}/classes/Substrate/tolerance` is called with body `{"tolerance": -0.1}` or `{"tolerance": 0}` or `{"tolerance": 200}` or `{"tolerance": "loose"}`
- **THEN** the response is HTTP 400
- **AND** the stored tolerance is unchanged

#### Scenario: Tolerance survives DB migration
- **WHEN** an existing SQLite file pre-dating the column is opened
- **THEN** migration adds a `tolerance REAL NULL` column to `classes`
- **AND** every existing row has `tolerance == NULL`
- **AND** matching behaviour is identical to the pre-migration state

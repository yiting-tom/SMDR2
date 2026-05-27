## MODIFIED Requirements

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

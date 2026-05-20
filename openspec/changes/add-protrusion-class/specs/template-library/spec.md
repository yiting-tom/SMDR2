## MODIFIED Requirements

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
- **AND** seeds the missing defaults (`FiducialCircle`, `FiducialCross`, `Protrusion`)
- **AND** re-ranks the surviving rows so they match the canonical order

#### Scenario: Protrusion gets a snake_case match-JSON key
- **WHEN** a template filed under `Protrusion` is matched
- **THEN** the persisted Match JSON key for that class is `protrusion`
- **AND** the canonical display label in the UI stays `Protrusion`

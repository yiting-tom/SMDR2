## MODIFIED Requirements

### Requirement: Server-side DXF flatten

The system SHALL parse uploaded DXF files server-side using ezdxf and
emit JSON-serialisable drawing primitives. The allowed `type` values
SHALL be `line`, `polyline`, `filled_polygon`, `point`, and `circle`.
Non-circular curves SHALL be flattened to polylines with a configurable
deviation tolerance (default 0.01 in drawing units). Circular
sub-paths produced by `Frontend.draw_path` (typically CIRCLE entities
and 360° CIRCULAR-ARC entities) SHALL be emitted as a `circle`
primitive carrying `center: [x, y]` and `r: float` instead of being
flattened to a closed polyline. The detection predicate SHALL require
at least 8 candidate vertices and a radial variance
`(rmax - rmin) / rmean ≤ 0.02`. Each primitive SHALL carry the source
DXF entity handle so the matching engine and frontend can resolve back
to the original entity.

#### Scenario: Flatten the bundled sample
- **WHEN** `flatten_for_render("data/test.dxf")` is called
- **THEN** the result contains at least one primitive
- **AND** every primitive's `type` is one of `line / polyline / filled_polygon / point / circle`
- **AND** every primitive carries a non-empty `handle`
- **AND** the result's `bbox` and `background` fields are populated

#### Scenario: A CIRCLE entity becomes a circle primitive
- **WHEN** a DXF containing a single CIRCLE entity (radius 0.15 mm) is flattened
- **THEN** the result contains a primitive with `type == "circle"`
- **AND** that primitive carries numeric `center` (length 2) and `r` matching the source CIRCLE within 1 % radial tolerance
- **AND** the result contains no closed polyline primitive for that handle

#### Scenario: A true polyline stays a polyline
- **WHEN** a DXF containing an 8-vertex closed POLYLINE that is NOT a circular approximation is flattened
- **THEN** the result contains a `polyline` primitive (not a `circle`) for that handle
- **AND** the polyline's `points` list preserves the source vertices

#### Scenario: Index primitives by source DXF handle
- **WHEN** `build_handle_index(primitives)` is called over a flattened DXF
- **THEN** every entry maps a handle to the list of primitive indices for that entity
- **AND** the relation `primitives[idx]["handle"] == handle` holds for every (handle, idx)

## ADDED Requirements

### Requirement: Matcher consumes circle primitives via synthetic vertex sampling

`collect_entity_points` SHALL, for primitives of `type == "circle"`,
synthesize a deterministic, evenly-spaced sample of points around the
circle so the matching engine sees a point cloud equivalent to the
pre-change flattened-polyline representation. The number of samples N
SHALL be chosen as `max(8, min(64, round(2π·r / 0.01)))` so the
sampling density tracks the previous flattening tolerance and the
same input DXF always yields the same fingerprint.

#### Scenario: Circle primitive contributes points to the matcher
- **WHEN** a parsed file contains a `circle` primitive with `r = 0.15`
- **AND** `collect_entity_points` is invoked for that handle
- **THEN** the returned list has between 8 and 64 points
- **AND** each point lies within 1 % of radial distance `r` from `center`
- **AND** invoking the function again on the same primitive returns an identical list

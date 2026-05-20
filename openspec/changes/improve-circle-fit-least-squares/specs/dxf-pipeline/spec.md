## MODIFIED Requirements

### Requirement: Server-side DXF flatten

The system SHALL parse uploaded DXF files server-side using ezdxf and
emit JSON-serialisable drawing primitives. The allowed `type` values
SHALL be `line`, `polyline`, `filled_polygon`, `point`, and `circle`.
Non-circular curves SHALL be flattened to polylines with a
per-file flatten tolerance derived from the file's modelspace bbox
diagonal so vertex count stays bounded across pathological unit
scales. The tolerance SHALL be `max(BASE_TOLERANCE, diagonal *
SCALE_FACTOR)` with `BASE_TOLERANCE = 0.01` drawing units and
`SCALE_FACTOR = 1e-5`. Files whose extents cannot be determined SHALL
fall back to `BASE_TOLERANCE`. Circular sub-paths produced by
`Frontend.draw_path` (typically CIRCLE entities and 360° CIRCULAR-ARC
entities) SHALL be emitted as a `circle` primitive carrying
`center: [x, y]` and `r: float` instead of being flattened to a
closed polyline.

Closed sub-paths produced by `Frontend.draw_path` SHALL be probed by
the circle detector **regardless of whether the sub-path carries
curve segments**. When `sub.has_curves == True` the detector SHALL
require at least `CIRCLE_MIN_VERTS = 8` candidate vertices; when
`sub.has_curves == False` (pure line segments — typically a closed
LWPOLYLINE / POLYLINE authored as an N-gon approximation of a
circle, as commonly seen for BGA balls in some packaging DXFs) the
detector SHALL require at least `CIRCLE_MIN_VERTS_NOCURVE = 11`
candidate vertices. The higher no-curves threshold protects
legitimate low-N polygon pads (squares, hexagons, octagons,
dodecagons) whose radial variance can coincidentally fall under
`CIRCLE_RADIAL_TOL`; the threshold is set to 11 because regular
polygons used as deliberate pad shapes in IC packaging
substantively use N ∈ {3, 4, 6, 8, 12} and never N=11.

HATCH entities SHALL be stripped from modelspace before
`Frontend.draw_layout` is invoked, so no HATCH-sourced primitive
reaches the backend. The result of `flatten_for_render` SHALL contain
zero primitives whose source DXF handle belongs to a HATCH entity.
Other decorative entity types (TEXT, MTEXT, DIMENSION) SHALL continue
to be rendered with `decorative: true`.

The circle detector SHALL estimate the circle's centre by Kåsa
algebraic least-squares fitting — minimise Σᵢ
(xᵢ² + yᵢ² + D·xᵢ + E·yᵢ + F)² over D, E, F via the closed-form 3×3
normal equations, then take `(cx, cy) = (−D/2, −E/2)`. When the
normal-equation matrix is numerically singular (collinear or
near-collinear vertices), the detector SHALL fall back to the
centroid-based estimate `(Σx/n, Σy/n)`. After resolving the centre,
the detector SHALL recompute per-vertex distances
`rᵢ = hypot(xᵢ − cx, yᵢ − cy)` and accept the sub-path iff
`(rmax − rmin) / rmean ≤ CIRCLE_RADIAL_TOL = 0.02`. The emitted
primitive's `r` field SHALL be `rmean` (mean per-vertex distance to
the resolved centre).

Stroke-only circles emitted from `draw_path` SHALL continue to omit
the `filled` field (equivalent to `filled: false`); the field is
strictly additive and OPTIONAL on the `circle` primitive shape.

Each primitive SHALL carry the source DXF entity handle so the matching
engine and frontend can resolve back to the original entity. When the
chosen tolerance differs from `BASE_TOLERANCE`, the system SHALL emit
one info-level log line recording the diagonal and the chosen tolerance.

#### Scenario: Flatten the bundled sample
- **WHEN** `flatten_for_render("data/test.dxf")` is called
- **THEN** the result contains at least one primitive
- **AND** every primitive's `type` is one of `line / polyline / filled_polygon / point / circle`
- **AND** every primitive carries a non-empty `handle`
- **AND** the result's `bbox` and `background` fields are populated

#### Scenario: Normal-scale file uses the base tolerance
- **WHEN** a DXF whose modelspace bbox diagonal is below 1000 drawing units is flattened
- **THEN** the effective flatten tolerance equals `BASE_TOLERANCE` (0.01)
- **AND** no tolerance-adjustment log line is emitted

#### Scenario: Oversized-scale file relaxes the tolerance
- **WHEN** a DXF whose modelspace bbox diagonal is 100_000 drawing units is flattened
- **THEN** the effective flatten tolerance equals `1.0` (= 100_000 × 1e-5)
- **AND** the number of primitives produced for an ELLIPSE entity in the file is comparable to the count the same entity would produce at unit-scale (within 2×)
- **AND** an info-level log line records the diagonal and the chosen tolerance

#### Scenario: File with no determinable extents falls back to base tolerance
- **WHEN** a DXF whose extents cannot be determined (empty modelspace, all entities outside ezdxf's fast-bbox support) is flattened
- **THEN** the effective flatten tolerance equals `BASE_TOLERANCE`
- **AND** flatten proceeds without raising

#### Scenario: A CIRCLE entity becomes a circle primitive
- **WHEN** a DXF containing a single CIRCLE entity (radius 0.15 mm) is flattened
- **THEN** the result contains a primitive with `type == "circle"`
- **AND** that primitive carries numeric `center` (length 2) and `r` matching the source CIRCLE within 1 % radial tolerance
- **AND** the result contains no closed polyline primitive for that handle
- **AND** the primitive's `filled` field is absent or falsey

#### Scenario: HATCH entities emit no primitives
- **WHEN** a DXF containing one or more HATCH entities (any boundary shape — circle-bounded, polyline-bounded, multi-sub-path with holes, pattern-filled) is flattened
- **THEN** the result contains zero primitives whose `handle` matches any HATCH entity's handle
- **AND** non-HATCH entities in the same file are flattened normally

#### Scenario: A pure-line LWPOLYLINE approximating a circle becomes a circle primitive
- **WHEN** a DXF containing a closed LWPOLYLINE with 24 vertices spaced uniformly on a circle (radius 0.15 mm, centre (3.0, 4.0)) is flattened
- **THEN** the result contains exactly one primitive for the LWPOLYLINE's handle with `type == "circle"` and `filled` absent or falsey
- **AND** the primitive's `center` and `r` match the source vertices within 1 % radial tolerance
- **AND** the result contains no `polyline` primitive for that handle

#### Scenario: A pure-line LWPOLYLINE at the boundary vertex count is promoted
- **WHEN** a DXF containing a closed LWPOLYLINE with exactly 11 vertices uniformly on a circle is flattened
- **THEN** the result contains a `circle` primitive for that handle
- **AND** does NOT contain a `polyline` primitive for that handle

#### Scenario: A pure-line LWPOLYLINE below the no-curves threshold stays a polyline
- **WHEN** a DXF containing a closed LWPOLYLINE with 10 vertices uniformly on a circle (i.e., a decagon) is flattened
- **THEN** the result contains a `polyline` primitive for that handle
- **AND** does NOT contain a `circle` primitive (vertex count is below `CIRCLE_MIN_VERTS_NOCURVE`)

#### Scenario: Unevenly-sampled circle has accurate LS-fit centre
- **WHEN** a DXF containing a closed LWPOLYLINE whose 30 vertices lie on a circle (centre (5.0, −3.0), radius 0.5 mm) but with 24 of those vertices densely packed on a single 90° arc and the remaining 6 spaced over the other 270° is flattened
- **THEN** the result contains a `circle` primitive for that handle
- **AND** the primitive's `center` matches `(5.0, −3.0)` within `1e-3 × r` (the LS fit removes the centroid bias toward the dense arc)
- **AND** the primitive's `r` matches `0.5` within 1 % radial tolerance

#### Scenario: A true polyline stays a polyline
- **WHEN** a DXF containing an 8-vertex closed POLYLINE that is NOT a circular approximation is flattened
- **THEN** the result contains a `polyline` primitive (not a `circle`) for that handle
- **AND** the polyline's `points` list preserves the source vertices

#### Scenario: Index primitives by source DXF handle
- **WHEN** `build_handle_index(primitives)` is called over a flattened DXF
- **THEN** every entry maps a handle to the list of primitive indices for that entity
- **AND** the relation `primitives[idx]["handle"] == handle` holds for every (handle, idx)

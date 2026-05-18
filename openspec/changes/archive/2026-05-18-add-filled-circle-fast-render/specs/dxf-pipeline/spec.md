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

Filled circular regions reaching `Frontend.draw_filled_paths` (for
example, a HATCH entity whose only boundary is a circular edge) SHALL
likewise be emitted as a single `circle` primitive — `center` + `r`
plus an additional `filled: true` flag — instead of as a
`filled_polygon` carrying the flattened boundary ring, **provided
the call satisfies all of**: exactly one input path, exactly one
sub-path, `sub.is_closed`, `getattr(sub, "has_curves", False)`, and a
positive detection from the same circle predicate used by
`draw_path`. When any one of those conditions fails (multi-path
HATCH, multi-sub-path HATCH with holes, polyline-only sub-path that
happens to be near-circular, etc.), the system SHALL fall back to
the existing `filled_polygon` emit.

Stroke-only circles emitted from `draw_path` SHALL continue to omit
the `filled` field (equivalent to `filled: false`); the field is
strictly additive and OPTIONAL on the `circle` primitive shape.

The detection predicate SHALL require at least 8 candidate vertices
and a radial variance `(rmax - rmin) / rmean ≤ 0.02`, identical for
the `draw_path` and `draw_filled_paths` code paths. Each primitive
SHALL carry the source DXF entity handle so the matching engine and
frontend can resolve back to the original entity. When the chosen
tolerance differs from `BASE_TOLERANCE`, the system SHALL emit one
info-level log line recording the diagonal and the chosen tolerance.

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

#### Scenario: A HATCH bounded by a circle becomes a filled circle primitive
- **WHEN** a DXF containing a HATCH whose only boundary is a single circular edge (radius 0.30 mm) is flattened
- **THEN** the result contains exactly one primitive for the HATCH's handle with `type == "circle"` and `filled == true`
- **AND** that primitive's `center` and `r` match the source HATCH boundary within 1 % radial tolerance
- **AND** the result contains no `filled_polygon` primitive for that handle
- **AND** the primitive's `decorative` flag is `true` (HATCH is in `DECORATIVE_DXFTYPES`)

#### Scenario: A HATCH with a non-circular polyline boundary stays a filled_polygon
- **WHEN** a DXF containing a HATCH whose boundary is a polyline-only path (`has_curves == False`) is flattened
- **THEN** the result for that handle contains a `filled_polygon` primitive
- **AND** does NOT contain a `circle` primitive, even if the polyline vertices happen to lie on a circle

#### Scenario: A multi-sub-path HATCH (e.g., annulus) stays a filled_polygon
- **WHEN** a DXF containing a HATCH with an outer circular boundary and an inner circular hole is flattened
- **THEN** the result for that handle contains one `filled_polygon` primitive with two rings
- **AND** does NOT contain a `circle` primitive (the fast path requires exactly one sub-path)

#### Scenario: A true polyline stays a polyline
- **WHEN** a DXF containing an 8-vertex closed POLYLINE that is NOT a circular approximation is flattened
- **THEN** the result contains a `polyline` primitive (not a `circle`) for that handle
- **AND** the polyline's `points` list preserves the source vertices

#### Scenario: Index primitives by source DXF handle
- **WHEN** `build_handle_index(primitives)` is called over a flattened DXF
- **THEN** every entry maps a handle to the list of primitive indices for that entity
- **AND** the relation `primitives[idx]["handle"] == handle` holds for every (handle, idx)

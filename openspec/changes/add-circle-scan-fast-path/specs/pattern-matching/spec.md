## ADDED Requirements

### Requirement: Primitive kind on EntityShape

Each `EntityShape` SHALL carry a `kind: str | None` field identifying
the source primitive type (e.g., `"circle"`, `"polyline"`, `"line"`).
`build_entity_shapes` SHALL compute `kind` by inspecting the
primitives sharing the handle: when every primitive for that handle
has the same `type`, `kind` is that type string; when types disagree,
`kind` is `None` (mixed-kind handle). The same field SHALL be settable
on virtual EntityShapes constructed via `EntityShape.from_points` so
that `find_matches_from_pointsets` can preserve kind information for
templates loaded from the library.

#### Scenario: CIRCLE primitive yields kind="circle"
- **WHEN** a handle's primitives consist of a single `type: "circle"` entry
- **THEN** `build_entity_shapes` produces an `EntityShape` with `kind == "circle"`

#### Scenario: Mixed-kind handle yields kind=None
- **WHEN** a handle aggregates one `polyline` and one `circle` primitive
- **THEN** the resulting `EntityShape.kind` is `None`

#### Scenario: Pointset-constructed EntityShape carries the supplied kind
- **WHEN** `EntityShape.from_points(handle, points, kind="circle")` is called
- **THEN** the returned shape's `kind` is `"circle"`
- **AND** when no kind is supplied, `kind` defaults to `None`

### Requirement: Single-CIRCLE template fast path via radius bucket

The matcher SHALL dispatch to a circle-specialised path whenever the
template is exactly one entity, `template.kind == "circle"`, and
`template.radius > 0`. The fast path SHALL:

1. Compute a bucket key `key = round(radius * 10 ** CIRCLE_RADIUS_KEY_DIGITS)` (default `CIRCLE_RADIUS_KEY_DIGITS = 6`, i.e. 1 nm precision in mm units — loose enough to absorb the ~10⁻¹¹-mm noise that CAD transforms accumulate on large-radius circles while still distinguishing real design steps far below human-meaningful resolution).
2. Look up a per-drawing `radius_buckets: dict[int, list[handle]]` built lazily over the drawing's `kind == "circle"` entries and cached keyed by drawing identity. Cache entries MUST be invalidated when the drawing's shapes dict is rebuilt (a fresh dict object yields a fresh cache slot).
3. Return every bucketed handle (excluding entries in `skip`) as a `MatchResult` with `score = 0.0` and `scale = 1.0`.
4. Emit NO `NearMiss` entries — `MatchOutput.near_misses` SHALL be the empty list.

The fast path SHALL NOT compute `signatures_compatible`, PCA axes,
Chamfer distance, or any per-candidate alignment.

#### Scenario: Frame-select one BGA ball returns every bit-identical-radius circle
- **WHEN** the drawing contains 1000 CIRCLE entities of identical radius `r`
- **AND** the template is one of those circles
- **THEN** `find_matches([h_template], drawing)` returns 999 matches (the template's own handle is excluded via the skip set)
- **AND** every match has `kind == "circle"` and `radius` whose bucket key equals the template's bucket key
- **AND** `MatchOutput.near_misses == []`

#### Scenario: Different-radius circle is excluded (no near-miss)
- **WHEN** the drawing contains a 1.0-mm circle template plus circles of radii 1.001 mm, 1.05 mm, and 0.5 mm
- **THEN** none of the 1.001 / 1.05 / 0.5 mm circles appear in `matches`
- **AND** none of them appear in `near_misses` either

#### Scenario: FP-noise radii within bucket precision still match
- **WHEN** two circles' radii differ by less than `10 ** -CIRCLE_RADIUS_KEY_DIGITS` (e.g., 1e-12)
- **THEN** they share a bucket key
- **AND** matching on either one returns the other

#### Scenario: Same-radius polyline that is NOT detected as a circle does not match
- **WHEN** the drawing contains a CIRCLE template and a closed polyline whose flattened points lie on a circle of the same radius
- **AND** the polyline's primitive `type` is `"polyline"` (i.e., `_detect_circle_subpath` did not promote it)
- **THEN** the polyline's EntityShape has `kind != "circle"` and is NOT in the radius bucket
- **AND** it does NOT appear in matches via the fast path

#### Scenario: Template with kind=None falls back to generic path
- **WHEN** the template entity has `kind == None` (mixed or legacy)
- **THEN** `find_matches` dispatches to the generic `_match_single`, not the circle fast path

#### Scenario: Template with radius=0 falls back to generic path
- **WHEN** the template entity has `kind == "circle"` but `radius == 0`
- **THEN** `find_matches` dispatches to the generic `_match_single`

#### Scenario: Cache invalidates when the drawing shapes dict is rebuilt
- **WHEN** the matcher is invoked twice against two different shapes dicts (e.g., before and after a library swap rebuilds shapes)
- **THEN** the second invocation computes its own bucket dict
- **AND** matches reflect the second drawing's circles, not the first's

### Requirement: Circle fast path applies to library-stored templates

`find_matches_from_pointsets` SHALL accept an optional parallel
`entity_kinds: list[str | None]` argument. When the template consists
of one entity AND its `entity_kinds[0] == "circle"`, the matcher SHALL
dispatch to the same radius-bucket fast path used by `find_matches`.
When `entity_kinds` is omitted or contains `None` for the template
entity, the function SHALL fall back to the generic path (preserving
backwards compatibility for library templates committed before this
change).

#### Scenario: scan-all CIRCLE template uses the fast path
- **WHEN** a library template was committed from one CIRCLE primitive
- **AND** `scan_all` runs against a drawing with 500 same-radius circles
- **THEN** the matcher dispatches to the radius-bucket fast path
- **AND** all 500 circles appear in `by_class["<template_class>"]`

#### Scenario: Legacy template with NULL kinds still scans correctly
- **WHEN** a library template was committed before this change (entity_kinds is `[None]` after migration)
- **AND** the template's stored points describe a circle
- **THEN** `find_matches_from_pointsets` falls back to the generic path and still returns the expected matches (no fast-path speed-up, no regression)

## MODIFIED Requirements

### Requirement: Single-entity template matching

When the template is exactly one entity, the matcher SHALL scan every
candidate entity in the drawing, pre-filter by `signatures_compatible`
(vertex count within ±25%, path length within ±20%), and verify
remaining candidates with `align_score`. Template-side state SHALL be
computed once outside the loop. **EXCEPT** when the template entity
has `kind == "circle"` and `radius > 0`: in that case the matcher
SHALL dispatch to the radius-bucket fast path (see "Single-CIRCLE
template fast path via radius bucket"), which bypasses
`signatures_compatible` / PCA / Chamfer in favour of a per-drawing
bucket lookup.

#### Scenario: Find translated copies of a single entity
- **WHEN** the template is one rectangle and the drawing contains 3 translated copies plus the template itself
- **THEN** `find_matches([template_handle], drawing)` returns exactly the 3 copies
- **AND** the template's own handle does not appear in the results

#### Scenario: Reject a different shape with similar size
- **WHEN** a candidate has the same vertex count but different aspect ratio
- **THEN** it does not appear in matches
- **AND** it MAY appear in near-misses with `reason: "shape"`

#### Scenario: CIRCLE template skips signatures_compatible
- **WHEN** the template is a single CIRCLE EntityShape (`kind == "circle"`, `radius > 0`)
- **THEN** the matcher does NOT call `signatures_compatible` against any candidate
- **AND** does NOT compute PCA axes or Chamfer for any candidate
- **AND** does NOT emit any `NearMiss`

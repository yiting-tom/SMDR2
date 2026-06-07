## MODIFIED Requirements

### Requirement: Server-side DXF flatten

The system SHALL parse uploaded DXF files server-side using ezdxf and
emit JSON-serialisable drawing primitives. The allowed `type` values
SHALL be `line`, `polyline`, `filled_polygon`, `point`, and `circle`.
Non-circular curves SHALL be flattened to polylines with a
per-file flatten tolerance derived from the rendered tab's bbox
diagonal so vertex count stays bounded across pathological unit
scales. The tolerance SHALL be `max(BASE_TOLERANCE, diagonal *
SCALE_FACTOR)` with `BASE_TOLERANCE = 0.01` drawing units and
`SCALE_FACTOR = 1e-5`. Files whose extents cannot be determined SHALL
fall back to `BASE_TOLERANCE`. Circular sub-paths produced by
`Frontend.draw_path` (typically CIRCLE entities and 360° CIRCULAR-ARC
entities) SHALL be emitted as a `circle` primitive carrying
`center: [x, y]` and `r: float` instead of being flattened to a
closed polyline.

`flatten_for_render` SHALL accept an optional `layout_name` selecting
which AutoCAD tab — modelspace or a paper-space layout — to render.
When `layout_name` is omitted or None, the system SHALL auto-resolve
the tab: modelspace when it contains any entities (the default for
normal files, so model-space DXFs are unaffected), otherwise the
paper-space layout with the most **renderable** entities — so DXFs
whose geometry lives in a layout tab (exported from DWGs organised one
view per tab) render rather than returning empty. The renderable count
SHALL exclude `NON_RENDERED_DXFTYPES` (the VIEWPORT framing entity that
AutoCAD writes into every paper-space tab), so a viewport-only
framing/title-block tab never out-ranks a tab with real geometry. An explicit `layout_name` SHALL
render that tab; an unknown name SHALL degrade to auto-resolution
rather than raising. The result SHALL carry `source_layout` (the
resolved tab name, e.g. `"Model"` / `"Layout1"`) and a boolean
`source_is_paperspace`. The bbox diagonal used to pick the flatten
tolerance SHALL be computed from the rendered tab: modelspace MAY use
the `$EXTMIN/$EXTMAX` header shortcut, while a paper-space layout SHALL
use a direct entity-extents sweep of that layout (the header extents
describe model space only).

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

Filled circular regions reaching `Frontend.draw_filled_paths` (for
example, a HATCH entity whose only boundary is a circular edge) SHALL
likewise be emitted as a single `circle` primitive — `center` + `r`
plus an additional `filled: true` flag — instead of as a
`filled_polygon` carrying the flattened boundary ring, **provided
the call satisfies all of**: exactly one input path, exactly one
sub-path, `sub.is_closed`, and a positive detection from the same
circle predicate used by `draw_path` (with the same dual-threshold:
`CIRCLE_MIN_VERTS = 8` for `has_curves == True`,
`CIRCLE_MIN_VERTS_NOCURVE = 11` for `has_curves == False`). When any
one of those conditions fails (multi-path HATCH, multi-sub-path
HATCH with holes, sub-path whose vertex count is below the
applicable threshold, sub-path whose radial variance exceeds
tolerance, etc.), the system SHALL fall back to the existing
`filled_polygon` emit.

Stroke-only circles emitted from `draw_path` SHALL continue to omit
the `filled` field (equivalent to `filled: false`); the field is
strictly additive and OPTIONAL on the `circle` primitive shape.

The detection predicate SHALL require a radial variance
`(rmax - rmin) / rmean ≤ CIRCLE_RADIAL_TOL = 0.02`, identical for
the `draw_path` and `draw_filled_paths` code paths and identical
for the curves and no-curves cases. Each primitive SHALL carry the
source DXF entity handle so the matching engine and frontend can
resolve back to the original entity. When the chosen tolerance
differs from `BASE_TOLERANCE`, the system SHALL emit one info-level
log line recording the diagonal and the chosen tolerance.

#### Scenario: Flatten the bundled sample
- **WHEN** `flatten_for_render("data/test.dxf")` is called
- **THEN** the result contains at least one primitive
- **AND** every primitive's `type` is one of `line / polyline / filled_polygon / point / circle`
- **AND** every primitive carries a non-empty `handle`
- **AND** the result's `bbox` and `background` fields are populated
- **AND** `source_layout == "Model"` and `source_is_paperspace` is false

#### Scenario: Geometry in model space takes precedence
- **WHEN** a DXF whose model space holds geometry AND which also has a
  non-empty paper-space layout is flattened with no `layout_name`
- **THEN** `source_layout == "Model"` and `source_is_paperspace` is false
- **AND** only the model-space entities are present in the result

#### Scenario: Empty model space falls back to the richest paper-space layout
- **WHEN** a DXF whose model space is empty and whose paper-space layouts
  hold geometry is flattened with no `layout_name`
- **THEN** the result renders the paper-space layout with the most entities
- **AND** `source_is_paperspace` is true and `source_layout` is that tab's name
- **AND** the result contains a non-empty `bbox`

#### Scenario: Explicit layout_name renders that tab
- **WHEN** `flatten_for_render(path, layout_name="Layout2")` is called and
  `Layout2` exists
- **THEN** only `Layout2`'s entities are rendered and `source_layout == "Layout2"`

#### Scenario: Unknown layout_name degrades to auto-resolution
- **WHEN** `flatten_for_render(path, layout_name=<a name that does not exist>)`
  is called
- **THEN** the system auto-resolves the tab (model space, else richest
  paper-space layout) and does not raise

#### Scenario: Normal-scale file uses the base tolerance
- **WHEN** a DXF whose rendered tab's bbox diagonal is below 1000 drawing units is flattened
- **THEN** the effective flatten tolerance equals `BASE_TOLERANCE` (0.01)
- **AND** no tolerance-adjustment log line is emitted

#### Scenario: Oversized-scale file relaxes the tolerance
- **WHEN** a DXF whose rendered tab's bbox diagonal is 100_000 drawing units is flattened
- **THEN** the effective flatten tolerance equals `1.0` (= 100_000 × 1e-5)
- **AND** the number of primitives produced for an ELLIPSE entity in the file is comparable to the count the same entity would produce at unit-scale (within 2×)
- **AND** an info-level log line records the diagonal and the chosen tolerance

#### Scenario: File with no determinable extents falls back to base tolerance
- **WHEN** a DXF whose rendered tab's extents cannot be determined (no entities anywhere, or all entities outside ezdxf's fast-bbox support) is flattened
- **THEN** the effective flatten tolerance equals `BASE_TOLERANCE`
- **AND** flatten proceeds without raising

#### Scenario: A CIRCLE entity becomes a circle primitive
- **WHEN** a DXF containing a single CIRCLE entity (radius 0.15 mm) is flattened
- **THEN** the result contains a primitive with `type == "circle"`
- **AND** that primitive carries numeric `center` (length 2) and `r` matching the source CIRCLE within 1 % radial tolerance
- **AND** the result contains no closed polyline primitive for that handle
- **AND** the primitive's `filled` field is absent or falsey

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

#### Scenario: A true polyline stays a polyline
- **WHEN** a DXF containing an 8-vertex closed POLYLINE that is NOT a circular approximation is flattened
- **THEN** the result contains a `polyline` primitive (not a `circle`) for that handle
- **AND** the polyline's `points` list preserves the source vertices

#### Scenario: Index primitives by source DXF handle
- **WHEN** `build_handle_index(primitives)` is called over a flattened DXF
- **THEN** every entry maps a handle to the list of primitive indices for that entity
- **AND** the relation `primitives[idx]["handle"] == handle` holds for every (handle, idx)

### Requirement: File lifecycle status

Each uploaded file SHALL track exactly one status value at any time
from: `discovering_layers`, `awaiting_layout`, `awaiting_layers`,
`preprocessing`, `ready_to_match`, `checking_rules`, `report`, `error`.

The default upload path takes a file through
`discovering_layers` (during Phase 1) → `awaiting_layers` (after
Phase 1, waiting for the operator's layer pick) → `preprocessing`
(Phase 2) → `ready_to_match` on success, or `error` on any
worker failure.

When a file's geometry is found in more than one paper-space layout
(model space empty), Phase 1 SHALL instead transition the file to
`awaiting_layout` after rendering a layout-picker manifest; the file
SHALL remain there until the operator picks a tab, which re-enters
`discovering_layers` and then continues to `awaiting_layers`. Files
whose geometry is in model space, or in a single paper-space layout,
SHALL NOT enter `awaiting_layout`.

The dev-mode skip path (see the `Multi-file upload with
deterministic file IDs` requirement's `skip_layer_pick` field)
takes a file through `preprocessing` → `ready_to_match`
directly, skipping `discovering_layers`, `awaiting_layout`, and
`awaiting_layers` entirely.

In both paths, preprocess failure SHALL transition the file to
`error` with the captured exception in `error`.

The `error` field SHALL capture either:
- a single exception message and traceback (the historical case,
  e.g. an OS error or a downstream pipeline failure), **or**
- a combined strict + recover exception string when both DXF parse
  paths failed (see `DXF parsing uses strict-first with recover
  fallback`).

The `dxf_recover_notes` field SHALL be populated independently of
the lifecycle status: a file may reach `ready_to_match` with
non-null `dxf_recover_notes` (the recover path succeeded), or
`error` with null `dxf_recover_notes` (recover did not save it, or
the failure was not DXF-parse related).

#### Scenario: Successful preprocess
- **WHEN** the preprocess worker returns successfully for a file
- **THEN** the file's status becomes `ready_to_match`
- **AND** `parsed_at`, `primitive_count`, `bbox`, and `background` are populated

#### Scenario: Preprocess failure
- **WHEN** the preprocess worker raises an exception
- **THEN** the file's status becomes `error`
- **AND** the `error` field captures the exception message and traceback

#### Scenario: Multi-layout file parks for a tab pick
- **WHEN** Phase 1 discovery finds model space empty and more than one
  paper-space layout carrying geometry
- **THEN** the file's status becomes `awaiting_layout`
- **AND** a layout manifest with one entry per content-bearing tab is
  written, each with an SVG thumbnail

#### Scenario: Ready file may carry recover notes
- **WHEN** a file's preprocess succeeds via the recover fallback
- **THEN** the file's status is `ready_to_match`
- **AND** `FileRecord.dxf_recover_notes` is a non-null dict carrying
  the audit summary

#### Scenario: Skip-layer-pick path bypasses layer-related statuses
- **WHEN** a file is uploaded with `skip_layer_pick=true`
- **THEN** the file's status transitions are
  `preprocessing` → `ready_to_match` (or `error`)
- **AND** the status never reads `discovering_layers`, `awaiting_layout`,
  or `awaiting_layers` for this file's upload

## ADDED Requirements

### Requirement: AutoCAD layout (tab) selection

The system SHALL let the operator pick which AutoCAD tab to load before
layer selection when a DXF's geometry lives in more than one paper-space
layout (model space empty), and SHALL persist that choice so every
re-preprocess renders the same tab.

The Phase-1 discover worker SHALL detect this case only on the
paper-space-fallback path — i.e. when auto-resolution returned
`source_is_paperspace == True` — so files whose geometry is in model
space incur no extra work. On detection it SHALL enumerate the tabs by
**renderable** entity count (VIEWPORT framing entities excluded) and,
when at least two paper-space tabs carry content, flatten each candidate
and render one SVG thumbnail per tab that produces primitives, then —
only when at least two tabs actually render — write a layout manifest
(`data/layer_preview/{file_id}/layouts/layouts.json` with `name`,
`safe_name`, `svg_filename`, `entity_count`, `is_paperspace` per tab)
and report `needs_layout_pick` so the file parks in `awaiting_layout`.
Tabs that render empty (viewport-only or otherwise non-emitting) SHALL
NOT appear in the picker. A single content-bearing layout SHALL NOT
trigger the picker — the parser's auto-fallback already renders it and
the file proceeds to layer selection.

The chosen tab SHALL be persisted on the file row as `chosen_layout`
(a nullable column; NULL means model space, the default for every
legacy row). Confirming a tab SHALL persist it and re-run layer
discovery against that tab (so the layout pick chains into the existing
layer picker). `chosen_layout` SHALL be threaded into every
re-preprocess (layer confirm, library swap, unit override,
reprocess-all) the same way `user_unit_override` is, so the file keeps
rendering its chosen tab. When auto-resolution rendered a paper-space
layout, the system SHALL stamp that tab into `chosen_layout` after
preprocess so the choice is stable and surfaceable.

The system SHALL expose:
- `GET /api/files/{file_id}/layouts` → the layout manifest + the current
  `chosen_layout` (404 when no picker applies to the file).
- `POST /api/files/{file_id}/layouts` with `{ "layout": "<name>" }` →
  validate the name against the manifest, persist `chosen_layout`, and
  re-run layer discovery against the tab.
- `GET /api/files/{file_id}/layout-preview/{safe_name}.svg` → one tab's
  thumbnail.

Confirming a tab that differs from the file's current `chosen_layout`
SHALL invalidate any saved per-file Match JSON (delete it and reset
`match_saved` to false), because a tab switch replaces the entire entity
set and the saved match references the old tab's handles — mirroring the
side-region edit invalidation.

While a file is in `awaiting_layout`, operations that would re-preprocess
it without a tab choice — the library-swap (`PATCH /api/files/{id}`) and
unit-override (`POST /api/files/{id}/unit-override`) endpoints — SHALL be
refused with `409`, so the file goes through the layout picker first
rather than rendering an auto-resolved tab the operator never picked.
(The reprocess-all job already skips `awaiting_layout` files.)

The dashboard SHALL show a "Pick view" action for `awaiting_layout`
files and a "tab: <name>" badge for files rendered from a paper-space
layout.

#### Scenario: Multi-layout DXF drives the layout picker then the layer picker
- **WHEN** a DXF with empty model space and two content-bearing
  paper-space layouts is uploaded
- **THEN** the file reaches `awaiting_layout` with a layout manifest of
  two tabs, each with an SVG thumbnail
- **AND** after `POST /api/files/{id}/layouts` with one tab, the file
  re-runs discovery and reaches `awaiting_layers` exposing that tab's layers
- **AND** after confirming layers the file reaches `ready_to_match` with
  `chosen_layout` set to the picked tab

#### Scenario: Single-layout DXF skips the layout picker
- **WHEN** a DXF with empty model space and exactly one content-bearing
  paper-space layout is uploaded
- **THEN** the file goes straight to `awaiting_layers` (no `awaiting_layout`)
- **AND** `GET /api/files/{id}/layouts` returns 404
- **AND** after the file is preprocessed `chosen_layout` is stamped with
  that layout's name

#### Scenario: Picking an unknown tab is rejected
- **WHEN** `POST /api/files/{id}/layouts` is called with a `layout` not in
  the file's manifest
- **THEN** the request fails with 400 and `chosen_layout` is unchanged

#### Scenario: Chosen tab survives re-preprocess
- **WHEN** a file with `chosen_layout` set is re-preprocessed (library
  swap / unit override / reprocess-all)
- **THEN** the preprocess renders that same tab

#### Scenario: A viewport-only framing tab does not trip the picker
- **WHEN** a DXF with empty model space, one paper-space layout holding
  real geometry, and a second paper-space layout holding only a VIEWPORT
  (the typical DWG-export framing tab) is uploaded
- **THEN** the file goes straight to `awaiting_layers` (no `awaiting_layout`)
  rendering the real-geometry tab
- **AND** `GET /api/files/{id}/layouts` returns 404

#### Scenario: Re-picking a different tab invalidates the saved match
- **WHEN** a file with a saved Match JSON has its tab changed via
  `POST /api/files/{id}/layouts` to a different layout
- **THEN** the saved Match JSON is deleted and `match_saved` becomes false

#### Scenario: awaiting_layout refuses library swap and unit override
- **WHEN** `PATCH /api/files/{id}` or `POST /api/files/{id}/unit-override`
  is called on a file in `awaiting_layout`
- **THEN** the request fails with `409` and the file stays in `awaiting_layout`

# dxf-pipeline Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
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

#### Scenario: A HATCH bounded by a pure-line LWPOLYLINE circle becomes a filled circle primitive
- **WHEN** a DXF containing a HATCH whose only boundary is a closed LWPOLYLINE with 24 vertices uniformly on a circle (radius 0.30 mm) is flattened
- **THEN** the result contains exactly one primitive for the HATCH's handle with `type == "circle"` and `filled == true`
- **AND** the primitive's `decorative` flag is `true`
- **AND** the result contains no `filled_polygon` primitive for that handle

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

### Requirement: Multi-file upload with deterministic file IDs

Users SHALL be able to upload one or more DXF files at the same time via
`POST /api/files`. Each accepted file SHALL receive a deterministic
`file_id` derived from the SHA-256 of its bytes (first 16 hex chars).
Re-uploading the same content SHALL deduplicate to the existing
`file_id` and skip re-processing if already ready.

#### Scenario: New DXF upload kicks off background processing
- **WHEN** a user uploads a previously-unseen `.dxf` file
- **THEN** the response contains a `file_id`, `status: "preprocessing"`, and a `job_id`
- **AND** a preprocess job is submitted to the worker pool

#### Scenario: Duplicate upload is deduplicated
- **WHEN** a user uploads bytes-identical content to a file already processed
- **THEN** the response carries `deduped: true` and `status: "ready_to_match"`
- **AND** no new preprocess job is submitted

#### Scenario: Non-DXF file is rejected
- **WHEN** a user uploads a file without a `.dxf` extension
- **THEN** the per-file response carries a `skipped` field with the reason
- **AND** no record is registered

### Requirement: File lifecycle status

Each uploaded file SHALL track exactly one status value at any time
from: `preprocessing`, `ready_to_match`, `checking_rules`, `report`,
`error`. Initial state SHALL be `preprocessing`; successful preprocess
SHALL transition to `ready_to_match`; preprocess failure SHALL
transition to `error` with the captured exception in `error`.

#### Scenario: Successful preprocess
- **WHEN** the preprocess worker returns successfully for a file
- **THEN** the file's status becomes `ready_to_match`
- **AND** `parsed_at`, `primitive_count`, `bbox`, and `background` are populated

#### Scenario: Preprocess failure
- **WHEN** the preprocess worker raises an exception
- **THEN** the file's status becomes `error`
- **AND** the `error` field captures the exception message and traceback

### Requirement: Background pre-processing with pre-match

For every uploaded file the system SHALL run a background pipeline that
parses the DXF, builds the entity shape index, runs scan-all against
the file's library snapshot, and persists the parsed primitives and the
pre-match handle-by-class to disk under `data/parsed/{file_id}.json`
and `data/prematch/{file_id}.json`.

#### Scenario: Pre-match against an empty library
- **WHEN** preprocessing completes for a file whose library has no templates
- **THEN** `data/prematch/{file_id}.json` exists with `{by_class: {}, total: 0}`

#### Scenario: Pre-match against a populated library
- **WHEN** preprocessing completes for a file whose library has at least one template
- **THEN** `data/prematch/{file_id}.json` contains handles grouped by class
- **AND** the totals match the sum of unique handles across classes

### Requirement: Per-file Match JSON export

`POST /api/files/{file_id}/match-json` SHALL produce a Match JSON of
the form `{"<class>.<template-index>": [[handle, ...], ...]}` over the
file's library and SHALL persist it to `data/match/{file_id}.json`.

The `<class>` token in every key SHALL be the **match-JSON key** form
defined by `library.CLASS_JSON_KEY` (see the `template-library`
capability), i.e. the snake_case / identifier-safe form derived from
the class's display ID. The viewer's per-class display label (which
uses the CamelCase display ID) SHALL be unaffected — only the
persisted JSON key changes.

For a class without an entry in `CLASS_JSON_KEY` (custom classes
added by the user), the `<class>` token SHALL be the display ID
verbatim.

#### Scenario: Single-entity template export
- **WHEN** a file's library has a `BGABall` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `bga_ball.0`
- **AND** every match in `bga_ball.0` is a single-handle list

#### Scenario: Multi-entity template export
- **WHEN** a file's library has a `SMD-2T` template composed of 3 entities at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `smd_2t.0`
- **AND** every match in `smd_2t.0` is a 3-handle list

#### Scenario: Substrate export uses snake_case key
- **WHEN** a file's library has a `Substrate` template at index 0
  and the file has no side regions drawn
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `substrate.0`
- **AND** the response does NOT include the key `Substrate.0`

#### Scenario: Custom class key passes through verbatim
- **WHEN** a library has a user-added class `MyMarker` with one template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json`
- **THEN** the response includes the key `MyMarker.0` (no case-folding)

### Requirement: Process-pool worker isolation

DXF parsing SHALL run in a child process via `ProcessPoolExecutor` so
the FastAPI event loop is never blocked. Worker count SHALL default to
2 and be controlled via `MAX_WORKERS` in `app/jobs.py`. Worker pool
SHALL be shut down cleanly on application shutdown.

#### Scenario: Concurrent uploads don't block the API
- **WHEN** two large DXFs are uploaded back-to-back
- **THEN** both `POST /api/files` responses return promptly
- **AND** the preprocess jobs execute in parallel across the worker pool

### Requirement: RenderOutput carries source DXF $INSUNITS

`flatten_for_render` SHALL extract the source DXF's `$INSUNITS` header
value (`doc.header.get("$INSUNITS")`) and expose it on `RenderOutput`
as a nullable integer. The value SHALL be returned verbatim with no
remapping; consumers downstream are responsible for interpreting the
DXF spec enum (0 = unitless, 1 = inch, 2 = foot, 4 = mm, 5 = cm,
6 = m, …). When the header is missing or unparseable, the field SHALL
be `None`.

#### Scenario: A DXF with INSUNITS=4 (mm) is flattened
- **WHEN** a DXF whose header declares `$INSUNITS = 4` is flattened
- **THEN** `RenderOutput.insunits == 4`

#### Scenario: A DXF with no INSUNITS header is flattened
- **WHEN** a DXF whose header does not set `$INSUNITS` (or sets it to 0) is flattened
- **THEN** `RenderOutput.insunits` is `0` if explicitly set, else `None`

### Requirement: Per-file side regions persistence

The system SHALL persist, per uploaded file, three optional
axis-aligned world-space rectangles: `top_view_rect`,
`bottom_view_rect`, and `side_view_rect`. Each rectangle SHALL be
stored as JSON `{"x0":..,"y0":..,"x1":..,"y1":..}` with `x0<=x1` and
`y0<=y1` after normalisation. Any subset (including all three, any
two, any one, or none) SHALL be allowed. The rectangles SHALL be
reachable via `GET /api/files/{file_id}` (included in the file record
JSON) and writable via `PATCH /api/files/{file_id}/side-regions` with
body `{"top_view_rect": <rect|null>, "bottom_view_rect": <rect|null>, "side_view_rect": <rect|null>}`.

Switching the file's library, re-running preprocess, or editing the
selected layers SHALL NOT clear any of the side rectangles.

On first server start after this change, the `files` table SHALL be
migrated by renaming `frontside_rect` to `top_view_rect`, renaming
`bottomside_rect` to `bottom_view_rect`, and adding the new
`side_view_rect` column. The migration SHALL be idempotent.

#### Scenario: PATCH stores all three rectangles
- **WHEN** the user PATCHes `/api/files/{id}/side-regions` with all three rectangles
- **THEN** the file record returns all three rectangles on subsequent GETs
- **AND** the values are normalised so `x0<=x1` and `y0<=y1`

#### Scenario: PATCH clears one side independently
- **WHEN** the user PATCHes with `top_view_rect: null` and leaves the other two as-is
- **THEN** the `top_view` rectangle is unset
- **AND** the `bottom_view_rect` and `side_view_rect` are unchanged

#### Scenario: PATCH sets only side_view
- **WHEN** the user PATCHes with only `side_view_rect` populated and the other two as `null`
- **THEN** the file record returns `side_view_rect` populated and the other two as `null`

#### Scenario: Library swap preserves regions
- **WHEN** the user changes the file's library
- **THEN** the file's three rectangles are unchanged

#### Scenario: Migration renames pre-existing columns
- **WHEN** the server starts against a DB whose `files` table has the old `frontside_rect` and `bottomside_rect` columns and no `side_view_rect`
- **THEN** after migration the table has `top_view_rect`, `bottom_view_rect`, and `side_view_rect`
- **AND** any existing values in `frontside_rect` are now under `top_view_rect`
- **AND** any existing values in `bottomside_rect` are now under `bottom_view_rect`

### Requirement: Side-prefixed match JSON keys

The system SHALL emit each match instance into
`data/match/{file_id}.json` (written via `POST
/api/files/{file_id}/match-json`) under a key derived from its
bbox-center position relative to the file's three side rectangles,
using the following deterministic priority:

- center inside `top_view_rect` → key `top_view.<class>.<index>`
- else center inside `bottom_view_rect` → key `bottom_view.<class>.<index>`
- else center inside `side_view_rect` → key `side_view.<class>.<index>`
- else (center inside none, or all three rectangles unset) → key
  `<class>.<index>` (no prefix)

The `<class>` token SHALL be the snake_case match-JSON key form
defined by `library.CLASS_JSON_KEY` (see "Per-file Match JSON
export" and the `template-library` capability). The view-prefix
segments (`top_view`, `bottom_view`, `side_view`) are themselves
already snake_case and are unaffected by this change.

The bbox center SHALL be computed from the combined bounding box of
all entity point arrays in the match instance.

Instances of the same `<class>.<index>` template that fall in
different rectangles SHALL be split into the corresponding view-
prefixed keys in the same output file.

**Class-view constraint filter.** Before a key is emitted, the
serialiser SHALL consult `library.is_allowed_view(class_name,
view)` (see `template-library` capability) where `class_name` is
the **display ID** and `view` is one of `"top_view"`,
`"bottom_view"`, `"side_view"`, or `None` (the unassigned position
above). When the helper returns `False`, the instance SHALL be
dropped: not emitted under any key. Surviving counts SHALL be
exposed in the endpoint response as
`side_counts = {"top_view", "bottom_view", "side_view",
"unassigned", "dropped"}` where `"dropped"` is the aggregate count
of class-view-constraint violations.

**Skip-when-impossible optimisation.** For any class with an entry
in `library.CLASS_VIEW_CONSTRAINTS`, when *every* allowed view
rectangle for that class is `None` on the file
(e.g., `C4Ball` on a file whose `top_view_rect is None`), the
serialiser MAY skip the call to `find_matches_from_pointsets` for
that class's templates entirely, because every produced instance
would be dropped. This SHALL be a pure performance optimisation:
the response SHALL be byte-identical to a run that did not skip,
and the test surface SHALL verify both paths agree.

#### Scenario: Top-view-only file with all three regions drawn
- **WHEN** the file has three valid rectangles and all `smd_2t.0` match instances' bbox centers lie inside `top_view_rect`
- **THEN** the saved JSON contains key `top_view.smd_2t.0` with every instance
- **AND** the JSON does not contain `bottom_view.smd_2t.0`, `side_view.smd_2t.0`, or unprefixed `smd_2t.0`

#### Scenario: Instances split across all three views
- **WHEN** a class `smd_2t.0` has 15 instances: 7 in `top_view_rect`, 5 in `bottom_view_rect`, 3 in `side_view_rect`
- **THEN** the saved JSON contains `top_view.smd_2t.0` (7 instances), `bottom_view.smd_2t.0` (5), and `side_view.smd_2t.0` (3)
- **AND** the unprefixed key `smd_2t.0` does not appear

#### Scenario: Overlap priority resolves to top_view
- **WHEN** `top_view_rect` and `side_view_rect` overlap and a match instance's bbox center lies inside both
- **THEN** that instance is emitted under `top_view.<class>.<index>`

#### Scenario: Overlap priority resolves to bottom_view when top is absent
- **WHEN** `top_view_rect` is null, `bottom_view_rect` and `side_view_rect` overlap, and a match instance's bbox center lies inside both
- **THEN** that instance is emitted under `bottom_view.<class>.<index>`

#### Scenario: No regions set leaves unconstrained keys unprefixed
- **WHEN** all three of `top_view_rect`, `bottom_view_rect`, and `side_view_rect` are null
- **THEN** the saved JSON keys for **unconstrained classes** (e.g., `smd_2t`, `substrate`) are `<class>.<index>` exactly as before any side regions were introduced
- **AND** the saved JSON does NOT contain any key for **constrained classes** (`c4_ball`, `bga_ball`), because their unassigned matches are dropped

#### Scenario: Instance outside all three rectangles is unprefixed for unconstrained class
- **WHEN** the file has at least one rectangle drawn but one match instance of an **unconstrained** class has its bbox center outside all three
- **THEN** that instance is emitted under the unprefixed key `<class>.<index>`

#### Scenario: Only side_view set, instance inside it
- **WHEN** only `side_view_rect` is set and a match instance's bbox center lies inside it
- **THEN** that instance is emitted under `side_view.<class>.<index>`

#### Scenario: C4Ball in top_view is kept
- **WHEN** the file has `top_view_rect` set and a `C4Ball` match's bbox center lies inside it
- **THEN** the saved JSON contains `top_view.c4_ball.<index>` with that instance
- **AND** `side_counts["top_view"]` includes this match

#### Scenario: C4Ball in bottom_view is dropped
- **WHEN** the file has `bottom_view_rect` set and a `C4Ball` match's bbox center lies inside it
- **THEN** the saved JSON does NOT contain `bottom_view.c4_ball.<index>` for that instance
- **AND** the saved JSON does NOT contain `c4_ball.<index>` unprefixed for that instance
- **AND** `side_counts["dropped"]` includes this match

#### Scenario: C4Ball with no top_view_rect set is dropped (skip-when-impossible)
- **WHEN** the file has `top_view_rect is None` and the library contains a `C4Ball` template
- **THEN** the saved JSON contains no `c4_ball` key (prefixed or unprefixed)
- **AND** the implementation MAY skip the matcher call for that template
- **AND** the result is byte-identical to a run that did not skip

#### Scenario: BGABall in bottom_view is kept
- **WHEN** the file has `bottom_view_rect` set and a `BGABall` match's bbox center lies inside it
- **THEN** the saved JSON contains `bottom_view.bga_ball.<index>` with that instance

#### Scenario: BGABall in side_view is kept
- **WHEN** the file has `side_view_rect` set and a `BGABall` match's bbox center lies inside it
- **THEN** the saved JSON contains `side_view.bga_ball.<index>` with that instance

#### Scenario: BGABall in top_view is dropped
- **WHEN** the file has `top_view_rect` set and a `BGABall` match's bbox center lies inside it
- **THEN** the saved JSON does NOT contain `top_view.bga_ball.<index>` for that instance
- **AND** `side_counts["dropped"]` includes this match

#### Scenario: BGABall with no bottom_view_rect and no side_view_rect is dropped
- **WHEN** the file has `bottom_view_rect is None` and `side_view_rect is None` (only `top_view_rect` is set, or none of the three)
- **AND** the library contains a `BGABall` template
- **THEN** the saved JSON contains no `bga_ball` key (prefixed or unprefixed)

### Requirement: Side-region edits invalidate saved match

The server SHALL delete the cached `data/match/{file_id}.json` (if
present) and reset the file's `match_saved` flag to `0` whenever
`PATCH /api/files/{file_id}/side-regions` changes any of the three
rectangles, so the engineer re-runs Save Match. The response SHALL
include the updated `match_saved` value so the dashboard can refresh.
`data/prematch/{file_id}.json` is not side-aware (it's a flat
per-class handle list used for the viewer's colored overlay) and
SHALL be left untouched.

#### Scenario: Editing any region clears the saved match
- **WHEN** the user PATCHes the side regions (changing any of the three rectangles, including setting one to null) and the file previously had `match_saved = 1`
- **THEN** `match_saved` becomes `0`
- **AND** `data/match/{file_id}.json` no longer exists on disk

#### Scenario: Editing only side_view clears the saved match
- **WHEN** the user PATCHes with only `side_view_rect` changing (the other two are unchanged) and the file previously had `match_saved = 1`
- **THEN** `match_saved` becomes `0`
- **AND** `data/match/{file_id}.json` no longer exists on disk

### Requirement: DXF preprocessing reads tunables from live module attributes

The DXF preprocessing pipeline in `app/dxf.py` SHALL resolve its
tunable thresholds (`BASE_TOLERANCE`, `CURVE_FLATTENING_DISTANCE`,
`CIRCLE_MIN_VERTS`, `CIRCLE_RADIAL_TOL`, `MAX_PRIMS_PER_THUMB`,
`MAX_VERTICES_PER_POLYLINE`) through module-attribute lookup at the
time each helper is called, not via values captured at import time.
This SHALL enable the developer-parameter override store to take
effect on subsequent preprocess calls without restart.

The change SHALL be a no-op at compiled default values: the rendering,
flattening, and circle-detection behaviour of an unmodified server
SHALL remain bit-identical to the prior implementation.

#### Scenario: Default behaviour unchanged
- **WHEN** the override store has not been touched since startup
- **THEN** preprocessing produces the same primitive payload as before this change for the same input DXF

#### Scenario: Override changes flatten tolerance for the next call
- **WHEN** the override store sets `BASE_TOLERANCE = 0.05`, then preprocess runs on a fresh DXF
- **THEN** flattened polylines use the new tolerance, and reverting the override restores the original

### Requirement: Re-preprocess all files job rebuilds primitives in-place

The pipeline SHALL expose an entry point invoked by
`POST /api/dev/reprocess-all` that re-runs preprocessing for every
file currently in storage using whatever tunables are live in the
module attribute table. For each file the job SHALL: read the
original DXF source from disk, run the same preprocess steps that
upload uses, overwrite the stored primitives and pre-match cache,
and update the file's lifecycle status the same way an upload would.
Saved Match JSONs SHALL NOT be deleted, even if their referenced
handles no longer appear in the re-extracted primitives.

#### Scenario: Re-preprocess walks every file
- **WHEN** the dev endpoint enqueues a reprocess-all job over 12 files
- **THEN** every file's stored primitives are rewritten exactly once and the job's progress counter reaches 12

#### Scenario: Saved Match JSONs survive re-preprocess
- **WHEN** a file with a saved Match JSON is re-preprocessed under a different `CIRCLE_MIN_VERTS`
- **THEN** the Match JSON file remains on disk (even if individual entries become orphaned in primitives)

### Requirement: Auto-rescale unit-suspect DXFs during preprocess

`flatten_for_render` SHALL multiply every flattened primitive
coordinate (and the recorded bbox) by a scale multiplier `M`
derived from a pure helper `detect_scale_factor(insunits,
bbox_diagonal) -> float`. `applied_scale` semantics: `rescaled_coord
= original_coord * M`. `M == 1.0` means no rescale.

`detect_scale_factor` SHALL return the first matching factor below:

| Case | Condition | Factor |
|---|---|---|
| Declared inch | `insunits == 1` | `25.4` |
| Declared cm   | `insunits == 5` | `10.0` |
| Declared m    | `insunits == 6` | `1000.0` |
| Declared mm   | `insunits == 4` | `1.0` |
| Unitless / unknown | `insunits ∈ {0, None}` | best power-of-10 (see below) |
| Otherwise     | unrecognised INSUNITS | `1.0` |

For the unitless / unknown path the function SHALL:

1. Consider candidate factors `[10**k for k in -3..+3]`. ±3 orders of
   magnitude covers every real packaging unit-misread case (μm → mm,
   mm → m, etc.) while keeping extreme misreads at `M = 1.0` for a
   human to inspect.
2. Pick the factor `M` for which `bbox_diagonal * M` falls inside
   the closed range `[10.0, 5000.0]`. When multiple factors qualify,
   pick the one giving the smallest in-range output — packaging
   designs cluster at the chip / small-package end (1–50 mm), and
   the aggressive choice is almost always right for the unit-misread
   cases this detector targets. When `M = 1.0` qualifies, always
   prefer it (no rescale).
3. Return `M` only when `|log10(M)| > 1` (i.e. `M ≤ 0.1` or `M ≥
   10` is **not** enough — must be ≤ 0.01 or ≥ 100). Marginal
   factors in `[0.1, 10]` return `1.0` so a real 5×5 mm dice
   (diagonal ≈ 7 mm) is not mistakenly rescaled ×10 to 70 mm.
4. Return `1.0` when no candidate brings the bbox into range.

When rescale fires (`M != 1.0`), all of the following SHALL reflect
the rescaled geometry:

- `RenderOutput.bbox`
- Every coordinate on every primitive in `RenderOutput.primitives`
- The per-layer thumbnail SVG produced by `render_layer_svg` for
  this file
- Anything derived downstream, including `EntityShape.points` used
  by the matcher and rule-check

`RenderOutput` SHALL gain an `applied_scale: float` field
(defaulting to `1.0`) carrying the factor. `flatten_for_render`
SHALL set it from the result of the rescale step. The DXF's source
`insunits` SHALL be recorded unmodified — it documents the input,
not the post-rescale state.

The `files` table SHALL gain an `applied_scale REAL NOT NULL
DEFAULT 1.0` column. Preprocessing SHALL persist the factor
returned by `flatten_for_render` into this column.

#### Scenario: A 1000×-too-big unitless DXF gets rescaled to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 42 000-unit bbox diagonal
- **THEN** `detect_scale_factor(0, 42000)` returns `0.001`
- **AND** `RenderOutput.applied_scale == 0.001`
- **AND** `RenderOutput.bbox` diagonal is 42 mm
- **AND** the persisted `files.applied_scale` row equals `0.001`

#### Scenario: A 1000×-too-small unitless DXF gets rescaled to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 0.05-unit bbox diagonal
- **THEN** `detect_scale_factor(0, 0.05)` returns `1000.0`
- **AND** `RenderOutput.applied_scale == 1000.0`
- **AND** `RenderOutput.bbox` diagonal is 50 mm

#### Scenario: A 100×-too-big unitless DXF gets rescaled to chip scale
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 6 000-unit bbox diagonal
- **THEN** `detect_scale_factor(0, 6000)` returns `0.01` (smallest in-range output: 60 mm rather than 600 mm)
- **AND** `RenderOutput.applied_scale == 0.01`
- **AND** `RenderOutput.bbox` diagonal is 60 mm

#### Scenario: A 100×-too-small unitless DXF gets rescaled
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 0.5-unit bbox diagonal
- **THEN** `detect_scale_factor(0, 0.5)` returns `100.0`
- **AND** `RenderOutput.applied_scale == 100.0`
- **AND** `RenderOutput.bbox` diagonal is 50 mm

#### Scenario: Declared-inch DXF is converted to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 1` (inch) and a 10-unit bbox diagonal
- **THEN** `detect_scale_factor(1, 10)` returns `25.4`
- **AND** `RenderOutput.applied_scale == 25.4`
- **AND** `RenderOutput.bbox` diagonal is 254 mm

#### Scenario: Declared-cm DXF is converted to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 5` (cm) and a 30-unit bbox diagonal
- **THEN** `detect_scale_factor(5, 30)` returns `10.0`
- **AND** `RenderOutput.applied_scale == 10.0`

#### Scenario: Declared-m DXF is converted to mm
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 6` (m) and a 0.3-unit bbox diagonal
- **THEN** `detect_scale_factor(6, 0.3)` returns `1000.0`
- **AND** `RenderOutput.applied_scale == 1000.0`

#### Scenario: Declared-mm DXF is always left alone
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 4` (mm) regardless of bbox magnitude
- **THEN** `detect_scale_factor(4, ...)` returns `1.0`
- **AND** `RenderOutput.applied_scale == 1.0`

#### Scenario: Marginal-factor unitless DXF stays at 1.0
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 7-unit bbox diagonal (a real 5 mm × 5 mm dice would only need ×10 to reach the expected range)
- **THEN** `detect_scale_factor(0, 7)` returns `1.0` (×10 is rejected by the safety guard `|log10(M)| > 1`)

#### Scenario: In-range unitless DXF stays at 1.0
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 100-unit bbox diagonal (already inside `[10, 5000]`)
- **THEN** `detect_scale_factor(0, 100)` returns `1.0`

#### Scenario: Out-of-range unitless DXF stays at 1.0
- **WHEN** preprocess runs on a DXF with `$INSUNITS = 0` and a 0.00005-unit bbox diagonal that no power-of-10 in `[-3, +3]` can bring into `[10, 5000]`
- **THEN** `detect_scale_factor(0, 0.00005)` returns `1.0`

#### Scenario: NULL insunits is treated like 0
- **WHEN** preprocess runs on a DXF with no recoverable INSUNITS header and a 42 000-unit bbox diagonal
- **THEN** `detect_scale_factor(None, 42000)` returns `0.001`

#### Scenario: Layer thumbnails reflect rescaled geometry
- **WHEN** preprocess runs and `applied_scale != 1.0`
- **THEN** the per-layer thumbnail SVGs for that file use coordinates multiplied by `applied_scale`
- **AND** the SVG viewBox dimensions match the rescaled bbox

### Requirement: Auto-rescale invalidates saved Match JSON

The server SHALL invalidate any saved Match JSON when preprocessing
produces an `applied_scale` that differs from the file row's
previously persisted `applied_scale`. Concretely, the server SHALL:

1. Delete `data/match/{file_id}.json` if present.
2. Reset the file row's `match_saved` flag to `0`.
3. Move the file's status back to `ready_to_match`.

The dashboard SHALL surface a one-line banner on the affected
product card (next dashboard tick) explaining that Match JSON was
cleared after auto-rescale so the user knows to re-run match.

`data/prematch/{file_id}.json` SHALL be rebuilt as part of the
re-preprocess pipeline (it is always derived); no separate
invalidation step is needed.

#### Scenario: Match JSON is dropped when factor changes
- **WHEN** a file's previous `applied_scale` was `1.0` and a new preprocess returns `applied_scale == 0.001`
- **AND** `data/match/{file_id}.json` existed on disk before the preprocess
- **THEN** `data/match/{file_id}.json` no longer exists
- **AND** the file row's `match_saved == 0`
- **AND** the file row's `status == "ready_to_match"`

#### Scenario: No invalidation when factor is unchanged
- **WHEN** a file is re-preprocessed and `applied_scale` stays at the same value as before (whether `1.0` or non-`1.0`)
- **AND** the file previously had `match_saved == 1`
- **THEN** `data/match/{file_id}.json` is left alone
- **AND** `match_saved` remains `1`

#### Scenario: Side-region invalidation still wins on its own
- **WHEN** a preprocess that did not change `applied_scale` also did not change side regions
- **THEN** no Match JSON invalidation fires from this requirement

### Requirement: One-shot legacy migration on startup

On app startup, the server SHALL submit a re-preprocess job for
every file row matching **both** of:

- `applied_scale == 1.0` (never rescaled before)
- `detect_scale_factor(insunits, bbox_diagonal)` evaluated against
  the persisted `insunits` and bbox diagonal returns a non-`1.0`
  factor under the current detector.

The migration SHALL reuse the existing re-preprocess job machinery
(the same code path that backs `POST /api/dev/reprocess-all`),
including its progress reporting through `_jobs`. Each matched file
SHALL go through the standard rescale + Match JSON invalidation flow
defined by the previous requirements.

The migration SHALL run exactly once per startup and SHALL be safe
to re-run (idempotent) — files that already have `applied_scale !=
1.0` are skipped because the detector returns the *current* unit
state, and a previously-rescaled file's persisted bbox is already in
mm.

#### Scenario: Legacy unit-suspect file gets rescaled on first startup
- **WHEN** the server starts and a file row has `applied_scale == 1.0`, `insunits == 0`, and persisted bbox diagonal of 42 000
- **THEN** a re-preprocess job is submitted for that file
- **AND** after the job completes, the file row has `applied_scale == 0.001`

#### Scenario: Legacy declared-inch file gets converted to mm on first startup
- **WHEN** the server starts and a file row has `applied_scale == 1.0`, `insunits == 1`, and persisted bbox diagonal of 10
- **THEN** a re-preprocess job is submitted for that file
- **AND** after the job completes, the file row has `applied_scale == 25.4`

#### Scenario: Migration is idempotent
- **WHEN** the server restarts after the migration ran once
- **THEN** the now-rescaled files are not re-submitted
- **AND** any newly-uploaded legacy-style files that haven't been preprocessed yet are still picked up


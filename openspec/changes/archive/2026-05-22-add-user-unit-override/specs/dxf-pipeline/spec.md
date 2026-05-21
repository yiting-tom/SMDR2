## ADDED Requirements

### Requirement: User unit override overrides the auto-rescale detector

The `files` table SHALL gain a `user_unit_override TEXT NULL` column
whose value (when not `NULL`) is one of the literal strings
`"mm" | "cm" | "m" | "inch" | "μm"`. The column defaults to `NULL`
on every existing and newly inserted row. App-layer code SHALL be
the sole validator of the enumerated set; the database column itself
is unconstrained `TEXT`.

When `flatten_for_render` runs on a file row whose
`user_unit_override` is **not** `NULL`, the function SHALL derive the
scale multiplier `M` from the override using this table and SHALL
**skip** the `detect_scale_factor` heuristic entirely for that
invocation:

| `user_unit_override` | `M` |
|---|---|
| `"mm"`   | `1.0`    |
| `"cm"`   | `10.0`   |
| `"m"`    | `1000.0` |
| `"inch"` | `25.4`   |
| `"μm"`   | `0.001`  |

All downstream contracts established by the existing
"Auto-rescale unit-suspect DXFs during preprocess" requirement SHALL
hold unchanged when `M` is derived from an override:
`RenderOutput.applied_scale` carries the resulting multiplier; all
primitive coordinates, the bbox, layer thumbnails, and derived
`EntityShape.points` reflect the rescaled geometry; `files.applied_scale`
persists the multiplier.

The source `insunits` SHALL still be recorded unmodified. An override
SHALL be allowed even when the file declares a recognised `INSUNITS`
(e.g. `insunits == 1` for inch with `user_unit_override == "mm"`);
the override wins. This case is informational only — no warning
gating, no rejection.

The existing "Auto-rescale invalidates saved Match JSON" requirement
SHALL fire on override-driven `applied_scale` changes the same way it
fires on detector-driven changes — its trigger condition ("`applied_scale`
that differs from the file row's previously persisted `applied_scale`")
already covers both.

#### Scenario: Override to inch on a unitless DXF yields ×25.4
- **WHEN** a file row has `user_unit_override == "inch"` and a stored `insunits == 0`
- **AND** `flatten_for_render` runs for that file
- **THEN** the function does not call `detect_scale_factor`
- **AND** `RenderOutput.applied_scale == 25.4`
- **AND** the bbox and every primitive coordinate are multiplied by `25.4`
- **AND** `files.applied_scale` is persisted as `25.4`

#### Scenario: Override to mm on a declared-inch DXF wins over the declaration
- **WHEN** a file row has `user_unit_override == "mm"` and `insunits == 1` (inch)
- **AND** `flatten_for_render` runs for that file
- **THEN** `RenderOutput.applied_scale == 1.0` (no rescale)
- **AND** the stored `insunits` row value remains `1`
- **AND** the per-file dashboard payload still reports `insunits == 1` for transparency

#### Scenario: Override to μm rescales by 0.001
- **WHEN** a file row has `user_unit_override == "μm"`
- **AND** `flatten_for_render` runs for that file
- **THEN** `RenderOutput.applied_scale == 0.001`

#### Scenario: NULL override falls through to the detector
- **WHEN** a file row has `user_unit_override IS NULL`
- **AND** `flatten_for_render` runs for that file
- **THEN** `M` is derived from `detect_scale_factor(insunits, bbox_diagonal)` per the existing requirement
- **AND** every existing scenario for the detector continues to hold

### Requirement: Setting the picker to the detector's natural choice clears the override

When the operator-driven override-set flow receives a unit whose
implied multiplier equals the multiplier `detect_scale_factor`
would return for the same file's `(insunits, pre_rescale_bbox_diagonal)`,
the server SHALL write `user_unit_override = NULL` rather than store
the redundant string. The operator MAY still trigger this code path
to force a recompute; that is acceptable, but the persistent state
SHALL reflect "no override" so future detector improvements continue
to apply to this file.

#### Scenario: Operator picks "mm" when the detector also picks 1.0
- **WHEN** a file with `insunits == 4` (mm) and `applied_scale == 1.0` has its override set to `"mm"` via the override endpoint
- **THEN** the persisted `user_unit_override` is `NULL`
- **AND** the persisted `applied_scale` remains `1.0`

#### Scenario: Operator picks "inch" when the detector also picks 25.4
- **WHEN** a file with `insunits == 1` (inch) has its override set to `"inch"`
- **THEN** the persisted `user_unit_override` is `NULL`
- **AND** the persisted `applied_scale` is `25.4` (unchanged from detector path)

#### Scenario: Operator picks "mm" when the detector would pick 0.001 — override is recorded
- **WHEN** a file with `insunits == 0` and pre-rescale bbox diagonal 42 000 has its override set to `"mm"`
- **AND** the detector would have returned `0.001` for this file
- **THEN** the persisted `user_unit_override` is `"mm"`
- **AND** the persisted `applied_scale` is `1.0`

### Requirement: Unit-override endpoint and recompute

The server SHALL expose `POST /api/files/{file_id}/unit-override`
accepting a JSON body `{"unit": <one of "mm"|"cm"|"m"|"inch"|"μm">}`.
The endpoint SHALL:

1. Validate `unit` against the enumerated set and return `400` for any
   other value (including `null`, missing field, or unknown string).
2. Enqueue a re-preprocess job for `file_id` that, as its first step,
   writes the override (or `NULL` per the clear-on-match requirement)
   into the file row, then runs the standard preprocess pipeline.
3. Return `202 Accepted` with `{"job_id": <id>}` so the viewer can
   poll for completion using the existing job-status endpoint.

The endpoint SHALL be idempotent at the override-value level: a
second POST with the same `unit` value on a file row that already
holds that override (or that maps to the same effective `applied_scale`)
SHALL still enqueue the job and recompute, because the operator may
legitimately use the picker to force a recompute even when nothing
about the override value changed. The persisted override row state
after the job completes is governed by the
"Setting the picker to the detector's natural choice clears the
override" requirement.

While a recompute job is in flight for a given `file_id`, subsequent
POSTs to the same endpoint for the same `file_id` SHALL return `409
Conflict` with the in-flight job id. The viewer is responsible for
displaying this state.

When the recompute completes and the resulting `applied_scale`
differs from the file row's prior `applied_scale`, the
"Auto-rescale invalidates saved Match JSON" requirement governs the
cache-drop and product-banner behaviour — no second invalidation
mechanism is introduced.

#### Scenario: POST with a valid unit returns 202 with a job id
- **WHEN** a client POSTs `{"unit": "inch"}` to `/api/files/{file_id}/unit-override` for a file currently in `ready_to_match`
- **THEN** the response is `202 Accepted` with a JSON body containing `"job_id"`
- **AND** a preprocess job for that file is enqueued

#### Scenario: POST with an unknown unit returns 400
- **WHEN** a client POSTs `{"unit": "feet"}`
- **THEN** the response is `400 Bad Request`
- **AND** no job is enqueued
- **AND** the file row is unchanged

#### Scenario: POST while a recompute is already running returns 409
- **WHEN** a preprocess job triggered by an earlier override POST is still in flight for `file_id`
- **AND** a second POST arrives for the same `file_id`
- **THEN** the response is `409 Conflict` with the in-flight `job_id` in the body
- **AND** no new job is enqueued

#### Scenario: Recompute persists the override before preprocess runs
- **WHEN** a recompute job for `file_id` with target unit `"inch"` starts running
- **THEN** the job writes `user_unit_override = "inch"` to the file row before invoking `flatten_for_render`
- **AND** `flatten_for_render` reads the persisted override and skips the detector

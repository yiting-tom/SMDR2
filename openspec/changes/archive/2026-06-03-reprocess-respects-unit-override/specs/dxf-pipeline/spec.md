## MODIFIED Requirements

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

For each file the job SHALL preserve that file's persisted scope and
unit decision exactly as a normal preprocess would: it SHALL re-apply
the file's `user_unit_override` (deriving the multiplier from the
override and skipping `detect_scale_factor`) when one is set, and SHALL
load the file's product-scoped templates for the pre-match step using
the file's `product_id`. A re-preprocess SHALL NOT re-run the
auto-detector on a file that carries an explicit `user_unit_override`.

#### Scenario: Re-preprocess walks every file
- **WHEN** the dev endpoint enqueues a reprocess-all job over 12 files
- **THEN** every file's stored primitives are rewritten exactly once and the job's progress counter reaches 12

#### Scenario: Saved Match JSONs survive re-preprocess
- **WHEN** a file with a saved Match JSON is re-preprocessed under a different `CIRCLE_MIN_VERTS`
- **THEN** the Match JSON file remains on disk (even if individual entries become orphaned in primitives)

#### Scenario: Re-preprocess re-applies a set unit override
- **WHEN** a unit-suspect file (its detector factor is non-`1.0`) has `user_unit_override == "mm"` (so `applied_scale == 1.0`) and is re-preprocessed by a reprocess-all job
- **THEN** the file's `applied_scale` remains `1.0` (the override's multiplier is re-applied, the detector is not consulted) and `user_unit_override` stays `"mm"`

### Requirement: One-shot legacy migration on startup

On app startup, the server SHALL submit a re-preprocess job for
every file row matching **all** of:

- `applied_scale == 1.0` (never rescaled before)
- `detect_scale_factor(insunits, bbox_diagonal)` evaluated against
  the persisted `insunits` and bbox diagonal returns a non-`1.0`
  factor under the current detector.
- `user_unit_override` is `NULL` (no explicit operator decision). A
  file carrying an override SHALL be excluded — the operator has
  authority over its unit and the auto-rescale migration MUST NOT
  re-evaluate or overwrite it.

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

#### Scenario: Overridden file is excluded from the migration
- **WHEN** the server starts and a file row has `applied_scale == 1.0`, `insunits == 0`, a bbox diagonal whose detector factor is non-`1.0`, and `user_unit_override == "mm"`
- **THEN** no re-preprocess job is submitted for that file and its `applied_scale` stays `1.0` across the restart

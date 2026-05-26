## ADDED Requirements

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

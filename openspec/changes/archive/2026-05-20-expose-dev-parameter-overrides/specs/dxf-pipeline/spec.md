## ADDED Requirements

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

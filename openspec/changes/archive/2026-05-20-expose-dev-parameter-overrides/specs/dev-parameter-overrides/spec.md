## ADDED Requirements

### Requirement: In-memory override store with curated allow-list

The server SHALL maintain a process-wide, in-memory override store for a
curated allow-list of tunables in `app/matching.py` and `app/dxf.py`.
The allow-list MUST be defined in `app/dev_overrides.py` and SHALL
include at minimum: `SCALE_MIN`, `SCALE_MAX`, `TOLERANCE_ABS`,
`VERTEX_COUNT_RATIO`, `PATH_LENGTH_RATIO`, `RADIUS_RATIO`,
`SIGMA_RATIO_TOL`, `RESAMPLE_N`, `BRUTE_FORCE_CUTOFF` from
`app.matching`; and `BASE_TOLERANCE`, `CURVE_FLATTENING_DISTANCE`,
`CIRCLE_MIN_VERTS`, `CIRCLE_RADIAL_TOL`, `MAX_PRIMS_PER_THUMB`,
`MAX_VERTICES_PER_POLYLINE` from `app.dxf`. Each entry SHALL declare its
expected type and an inclusive numeric range (or enum) used for
validation. Attempts to override a name outside the allow-list SHALL be
rejected with HTTP 400. Overrides SHALL NOT be persisted to disk;
restarting the server SHALL return all values to compiled defaults.

#### Scenario: Server starts with compiled defaults
- **WHEN** the server starts
- **THEN** `GET /api/dev/settings` returns each tunable with its `current` value equal to its `default` value

#### Scenario: Override outside allow-list is rejected
- **WHEN** a client POSTs an override for a name not in the allow-list (e.g. `CIRCLE_MIN_VERTS_NOCURVE`)
- **THEN** the endpoint responds 400 and no module attribute is mutated

#### Scenario: Override outside declared range is rejected
- **WHEN** a client POSTs `TOLERANCE_ABS = -1` (declared range is positive)
- **THEN** the endpoint responds 400 and the module attribute keeps its prior value

#### Scenario: Restart clears overrides
- **WHEN** a client applies overrides, then the server restarts
- **THEN** `GET /api/dev/settings` shows current values equal to defaults

### Requirement: GET /api/dev/settings returns defaults and current values

The endpoint `GET /api/dev/settings` SHALL return a JSON document with
one entry per allow-listed tunable containing: `name`, `module` (one of
`matching` or `dxf`), `default` (the compiled value), `current` (the
live module attribute value), `type`, `min`, `max`, and a short
human-readable `description`. The response SHALL be safe to call at any
time and SHALL NOT mutate state.

#### Scenario: Frontend reads current overrides on modal open
- **WHEN** the dashboard opens the dev parameter modal
- **THEN** it issues `GET /api/dev/settings` and renders one field per returned entry, pre-filled with `current`

### Requirement: POST /api/dev/settings applies overrides by mutating module attributes

The endpoint `POST /api/dev/settings` SHALL accept a JSON body of
`{ name: value, ... }` pairs covering any subset of the allow-list.
For each entry, the server SHALL validate the value against the
allow-list type/range and, on success, call
`setattr(<module>, name, value)` so subsequent matching and DXF calls
read the new value. The response body SHALL be the same shape as
`GET /api/dev/settings`, reflecting the post-apply state. A request with
zero valid entries (e.g. all keys outside the allow-list) SHALL return
400 and apply nothing.

#### Scenario: Applying a single override updates that attribute only
- **WHEN** the client POSTs `{ "TOLERANCE_ABS": 0.02 }`
- **THEN** `app.matching.TOLERANCE_ABS == 0.02` and every other allow-listed attribute is unchanged

#### Scenario: Partial-failure POST is rejected atomically
- **WHEN** the client POSTs a body where one entry passes validation and another fails
- **THEN** no module attribute is mutated and the response is 400 with a per-key error list

### Requirement: Reset action returns all tunables to compiled defaults

The override store SHALL expose a reset path so the user can revert to
compiled defaults without restarting the server. `POST /api/dev/settings`
with the body `{ "reset": true }` SHALL re-assign every allow-listed
attribute to its compiled default and respond with the same shape as
`GET /api/dev/settings`.

#### Scenario: Reset reverts in-memory mutations
- **WHEN** the client first POSTs `{ "TOLERANCE_ABS": 0.02 }`, then POSTs `{ "reset": true }`
- **THEN** `app.matching.TOLERANCE_ABS` equals its compiled default

### Requirement: POST /api/dev/reprocess-all re-runs preprocessing under current overrides

The endpoint `POST /api/dev/reprocess-all` SHALL enqueue a background
job that iterates every file in storage and re-runs the DXF
preprocessing pipeline using the now-current module attributes,
overwriting the file's stored primitives and pre-match cache. The
endpoint SHALL respond immediately with a job ID consumable by the
existing `GET /api/jobs/{job_id}` endpoint, mirroring the upload-job
contract. The job SHALL NOT delete saved Match JSONs but documentation
SHALL warn that match results may become stale if primitive payloads
change.

#### Scenario: Re-preprocess enqueues a single job covering all files
- **WHEN** the client POSTs `/api/dev/reprocess-all` with 12 files in storage
- **THEN** the endpoint returns one job ID and `GET /api/jobs/{job_id}` reports progress through all 12 files

#### Scenario: New DXF tolerance is applied to existing files
- **WHEN** the client overrides `BASE_TOLERANCE` then runs reprocess-all
- **THEN** each re-preprocessed file's primitives reflect the new tolerance

### Requirement: Overrides are dev-only and not thread-safe

The override store SHALL be documented in code and in the modal UI as
single-user, single-job-at-a-time. The system SHALL NOT take a lock to
serialise overrides with concurrent match or preprocess jobs. Behaviour
under concurrent use is undefined.

#### Scenario: Modal surfaces the dev-only contract
- **WHEN** the dev parameter modal is open
- **THEN** the body contains visible copy stating that overrides are in-memory only, lost on restart, and not safe to change while jobs are running

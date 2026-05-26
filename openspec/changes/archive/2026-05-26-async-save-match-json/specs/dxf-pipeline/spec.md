## MODIFIED Requirements

### Requirement: Per-file Match JSON export

`POST /api/files/{file_id}/match-json` SHALL submit a Match JSON
build job to the shared `ProcessPoolExecutor` and return **HTTP 202
with body `{"job_id": "<uuid>", "file_id": "<file_id>"}`** as soon
as the job is queued. Fast pre-flight checks (file record exists,
status is past `awaiting_layers`, `data/parsed/{file_id}.json`
exists on disk, the file's library is registered) SHALL run inside
the request handler so unrecoverable inputs still return a
synchronous 4xx/5xx without producing a job. The handler SHALL NOT
mutate `file.match_saved` itself.

The worker SHALL produce a Match JSON of the form
`{"<class>.<template-index>": [[handle, ...], ...]}` over the
file's library and SHALL persist it to `data/match/{file_id}.json`.
The on-disk shape and key form are unchanged from the previous
synchronous behaviour.

The `<class>` token in every key SHALL be the **match-JSON key**
form defined by `library.CLASS_JSON_KEY` (see the `template-library`
capability), i.e. the snake_case / identifier-safe form derived
from the class's display ID. The viewer's per-class display label
(which uses the CamelCase display ID) SHALL be unaffected — only
the persisted JSON key changes. For a class without an entry in
`CLASS_JSON_KEY` (custom classes added by the user), the `<class>`
token SHALL be the display ID verbatim.

When the worker completes successfully, the job's done callback
SHALL set `FILE_STORE.set_match_saved(file_id, True)` and SHALL
store the summary payload — `template_keys`, `total_matches`,
`side_counts`, `arbitration_counts`, `saved_to`, `file_id`,
`library_id`, `match_saved` — under `job.result`. This payload's
field set SHALL match the previous synchronous response body 1:1
so callers that already consume those fields work after they
switch from reading the POST body to reading `GET /api/jobs/{job_id}`.

When the worker raises, the job's done callback SHALL record
`job.status = "error"` and `job.error` SHALL be a non-empty
diagnostic string. `file.match_saved` SHALL NOT flip and
`data/match/{file_id}.json` SHALL NOT be considered valid; any
partial file written during the failed run is treated as absent
by the rule-check submit gate (which checks `match_saved`).

The existing read endpoint `GET /api/files/{file_id}/match-json`
SHALL continue to serve the persisted JSON from disk (unchanged).

#### Scenario: POST returns 202 with a job id
- **WHEN** a file's library is non-empty and the user invokes
  `POST /api/files/{id}/match-json`
- **THEN** the response status is `202`
- **AND** the response body is `{"job_id": "<uuid>", "file_id": "<id>"}`
- **AND** the in-memory job dict carries an entry with status
  `queued` or `running`
- **AND** no `data/match/{id}.json` has been written yet
- **AND** the file's `match_saved` flag remains its prior value

#### Scenario: Job result mirrors the prior synchronous body
- **WHEN** the submitted job for a file reaches status `done`
- **THEN** `GET /api/jobs/{job_id}` returns a body where
  `result.template_keys`, `result.total_matches`,
  `result.side_counts`, `result.arbitration_counts`,
  `result.saved_to`, `result.match_saved` are present
- **AND** the field shapes match the prior synchronous response
  body
- **AND** `data/match/{file_id}.json` exists on disk
- **AND** the file's `match_saved` flag is `true`

#### Scenario: Worker error keeps match_saved false
- **WHEN** the submitted job for a file reaches status `error`
- **THEN** `GET /api/jobs/{job_id}` returns a body where `error`
  is a non-empty string
- **AND** the file's `match_saved` flag remains `false`
- **AND** the rule-check submit gate
  (`POST /api/products/{pid}/rule-check`) for any product binding
  this file as a role still rejects with the
  `"these roles still need Save Match"` 400 error

#### Scenario: Pre-flight failure short-circuits without a job
- **WHEN** the user invokes `POST /api/files/{id}/match-json` for
  a file whose `parsed/{file_id}.json` is missing on disk
- **THEN** the response status is `4xx`/`5xx` (as today)
- **AND** no entry is added to the in-memory job dict
- **AND** the user receives the failure synchronously, without
  needing to poll

#### Scenario: Single-entity template export
- **WHEN** a file's library has a `BGABall` template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json` and
  the resulting job completes successfully
- **THEN** `result.template_keys` includes the key `bga_ball.0`
- **AND** every match in `bga_ball.0` is a single-handle list

#### Scenario: Multi-entity template export
- **WHEN** a file's library has a `SMD-2T` template composed of 3
  entities at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json` and
  the resulting job completes successfully
- **THEN** `result.template_keys` includes the key `smd_2t.0`
- **AND** every match in `smd_2t.0` is a 3-handle list

#### Scenario: Substrate export uses snake_case key
- **WHEN** a file's library has a `Substrate` template at index 0
  and the file has no side regions drawn
- **AND** the user invokes `POST /api/files/{id}/match-json` and
  the resulting job completes successfully
- **THEN** `result.template_keys` includes the key `substrate.0`
- **AND** `result.template_keys` does NOT include the key
  `Substrate.0`

#### Scenario: Custom class key passes through verbatim
- **WHEN** a library has a user-added class `MyMarker` with one
  template at index 0
- **AND** the user invokes `POST /api/files/{id}/match-json` and
  the resulting job completes successfully
- **THEN** `result.template_keys` includes the key `MyMarker.0`
  (no case-folding)

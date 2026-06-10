# design-rule-checking Specification (delta)

## MODIFIED Requirements

### Requirement: Rule check API and persistence

`POST /api/versions/{version_id}/rule-check` SHALL validate that the
version is not signed off in the past-tense sense of frozen
re-runs (signed-off versions reject submission with HTTP 409), that
the version is ready (every bound role-bearing file has `match_saved`
true for this version and its persisted Match JSON exists on disk),
submit a background job to the existing worker pool, and return
**`202 Accepted`** with a JSON body containing the `job_id`. The
handler SHALL NOT load Match JSON files or invoke `check_rules`
itself; that work runs in a worker process so the FastAPI event loop
is never blocked by DRC.

The background worker SHALL materialise the version's DRC handoff
bundle on disk (the same layout `app/drc_bundle.py:build_bundle`
writes inside its zip — `manifest.json` at the bundle root plus
`dxfs/<file_id>.dxf` and `match/<file_id>.json` per role-bound
file of the version), invoke `check_rules(product_id, bundle_dir)`
(the rule contract stays keyed by product — rules are product-level
and version-independent), and persist the result to
`data/rule_check/{version_id}.json`. The worker SHALL remove the
temporary bundle directory after `check_rules` returns (success or
failure). The worker SHALL NOT pre-merge per-role Match JSONs or
apply any handle prefix — the bundle ships per-file, unprefixed
handles per the existing handoff-bundle requirement.

`GET /api/jobs/{job_id}` SHALL serve the job's status. While the job
is queued or running, the response SHALL contain `kind:
"rule_check"`, `status`, `submitted_at`, `started_at`, and a null
`completed_at`. Once the job completes successfully, the response
SHALL include `status: "done"`, `completed_at`, and a `result`
object with `saved_to`, `rule_count`, `pass_count`, `fail_count`,
and `roles_covered`. On worker failure, the response SHALL include
`status: "error"` and a human-readable `error` string.

`GET /api/versions/{version_id}/rule-check` SHALL return the most
recently persisted `rule_check/{version_id}.json` — independent of
the job system, and readable indefinitely for signed-off versions.

`GET /api/products` and `GET /api/products/{product_id}` SHALL
include a `latest_rule_check_job` field per version (keyed in the
version listing). When no rule check job has ever been submitted for
that version within the server's current lifetime, the field SHALL be
`null`. Otherwise the field SHALL contain `{ job_id, status,
submitted_at, completed_at, error, result }` mirroring the most
recent job's state, where `result` carries the same summary shape as
the job-status endpoint when `status` is `done`.

#### Scenario: Submit rule check after Save Match
- **WHEN** every bound role-bearing file of version `v1` has
  `match_saved` true for `v1`
- **AND** the client invokes `POST /api/versions/{v1}/rule-check`
- **THEN** the response status is `202 Accepted`
- **AND** the response body contains a `job_id`
- **AND** the response is returned before `check_rules` runs

#### Scenario: Poll a finished rule check job
- **WHEN** the worker finishes successfully
- **AND** the client calls `GET /api/jobs/{job_id}`
- **THEN** the response contains `status: "done"`
- **AND** `result.saved_to` references the written
  `data/rule_check/{version_id}.json`
- **AND** the persisted result is readable via
  `GET /api/versions/{version_id}/rule-check`

#### Scenario: Rule check before Save Match fails clearly
- **WHEN** the user invokes rule check on a version where at
  least one role-bearing binding has not had Save Match performed
- **THEN** the API returns `400` with a message listing the roles
  still missing Save Match
- **AND** no job is created

#### Scenario: Rule check on a signed-off version is rejected
- **WHEN** version `v1` is signed off
- **AND** the client posts `POST /api/versions/{v1}/rule-check`
- **THEN** the response is HTTP 409
- **AND** the persisted `rule_check/{v1}.json` is unchanged

#### Scenario: Two versions keep independent results
- **WHEN** version `v1` has a persisted rule-check result
- **AND** version `v2` of the same product runs rule-check to completion
- **THEN** `rule_check/{v2}.json` is written
- **AND** `rule_check/{v1}.json` is byte-for-byte unchanged

#### Scenario: Worker error surfaces via job status
- **WHEN** the rule check worker raises an exception
- **THEN** the job record transitions to `status: "error"` with a
  human-readable `error` string
- **AND** the persisted `rule_check/{version_id}.json` is not overwritten

#### Scenario: Bundle directory is removed after the job ends
- **WHEN** `check_rules` returns or raises in the worker
- **THEN** the temporary bundle directory the worker materialised
  is removed before the job transitions out of `running`

#### Scenario: Event loop stays responsive during long DRC
- **WHEN** a rule check job is running on the worker pool
- **AND** another client issues a concurrent request to any
  unrelated endpoint
- **THEN** that unrelated request is served without waiting for
  `check_rules` to finish

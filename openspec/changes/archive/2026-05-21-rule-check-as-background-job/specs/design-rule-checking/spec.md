## MODIFIED Requirements

### Requirement: Rule check API and persistence

`POST /api/products/{product_id}/rule-check` SHALL validate that the
product is ready (every uploaded role-bearing file has `match_saved`
true and its persisted Match JSON exists on disk), submit a
background job to the existing worker pool, and return
**`202 Accepted`** with a JSON body containing the `job_id`. The
handler SHALL NOT load Match JSON files or invoke `check_rules`
itself; that work runs in a worker process so the FastAPI event loop
is never blocked by DRC.

The background worker SHALL build the per-role payload (merge each
role's Match JSON and entity shapes across all of its contributing
files, applying the existing `<short_file_id>:` handle-namespacing
rule when a role has 2+ files), invoke `check_rules(product_id,
dxfs_by_role)`, and persist the result to
`data/rule_check/{product_id}.json` in the same schema the
synchronous path used.

`GET /api/jobs/{job_id}` SHALL serve the job's status. While the job
is queued or running, the response SHALL contain `kind:
"rule_check"`, `status`, `submitted_at`, `started_at`, and a null
`completed_at`. Once the job completes successfully, the response
SHALL include `status: "done"`, `completed_at`, and a `result`
object with `saved_to`, `rule_count`, `pass_count`, `fail_count`,
and `roles_covered`. On worker failure, the response SHALL include
`status: "error"` and a human-readable `error` string.

`GET /api/products/{product_id}/rule-check` SHALL continue to
return the most recently persisted `rule_check.json` for the
product — independent of the job system.

`GET /api/products` and `GET /api/products/{product_id}` SHALL
include a `latest_rule_check_job` field per product. When no rule
check job has ever been submitted for that product within the
server's current lifetime, the field SHALL be `null`. Otherwise the
field SHALL contain `{ job_id, status, submitted_at,
completed_at, error, result }` mirroring the most recent
job's state, where `result` carries the same summary shape as the
job-status endpoint (`saved_to`, `rule_count`, `pass_count`,
`fail_count`, `roles_covered`) when `status` is `done`. This
allows a dashboard reloaded after the user has navigated away to
resume polling for an in-flight job, or surface the completed
result of a job that finished while they were elsewhere.

#### Scenario: Submit rule check after Save Match
- **WHEN** every uploaded role-bearing file for the product has
  `match_saved` true
- **AND** the client invokes `POST /api/products/{product_id}/rule-check`
- **THEN** the response status is `202 Accepted`
- **AND** the response body contains a `job_id`
- **AND** the response is returned before `check_rules` runs

#### Scenario: Poll a running rule check job
- **WHEN** the client calls `GET /api/jobs/{job_id}` for a rule
  check job that has been submitted but not yet finished
- **THEN** the response contains `kind: "rule_check"`
- **AND** `status` is either `"queued"` or `"running"`
- **AND** `completed_at` is null
- **AND** no `result` field is present

#### Scenario: Poll a finished rule check job
- **WHEN** the worker finishes successfully
- **AND** the client calls `GET /api/jobs/{job_id}`
- **THEN** the response contains `status: "done"`
- **AND** `result.saved_to` references the written
  `data/rule_check/{product_id}.json`
- **AND** `result.rule_count`, `result.pass_count`,
  `result.fail_count` describe the run
- **AND** `result.roles_covered` lists the roles the run consumed
- **AND** the persisted `rule_check.json` exists on disk and is
  readable via `GET /api/products/{product_id}/rule-check`

#### Scenario: Rule check before Save Match fails clearly
- **WHEN** the user invokes rule check on a product where at
  least one role-bearing file has not had Save Match performed
- **THEN** the API returns `400` with a message listing the roles
  still missing Save Match
- **AND** no job is created

#### Scenario: Worker error surfaces via job status
- **WHEN** the rule check worker raises an exception (e.g., a
  required Match JSON file is missing on disk by the time the
  worker reads it)
- **THEN** the job record transitions to `status: "error"` with a
  human-readable `error` string
- **AND** `GET /api/jobs/{job_id}` returns that error message
- **AND** the persisted `rule_check.json` is not overwritten

#### Scenario: Event loop stays responsive during long DRC
- **WHEN** a rule check job is running on the worker pool
- **AND** another client issues a concurrent request to any
  unrelated endpoint (for example, dashboard polling or viewer
  highlight lookup)
- **THEN** that unrelated request is served without waiting for
  `check_rules` to finish

#### Scenario: Dashboard reload picks up an in-flight job
- **WHEN** the user submits a rule check job and then reloads
  the dashboard (or navigates away and back) before the job
  finishes
- **AND** the dashboard fetches `GET /api/products`
- **THEN** the response includes `latest_rule_check_job` for that
  product with the live `status` (`queued` or `running`) and
  `job_id`
- **AND** the dashboard can resume polling `GET /api/jobs/{job_id}`
  without any local browser state having survived the reload

#### Scenario: Result of a completed job is recoverable after navigation
- **WHEN** the user submits a rule check job, navigates away, and
  returns after the worker has completed
- **AND** the dashboard fetches `GET /api/products`
- **THEN** the response includes `latest_rule_check_job` with
  `status: "done"`, `completed_at`, and a non-null `result`
  summary
- **AND** the dashboard can render the completion without polling
  the job-status endpoint again

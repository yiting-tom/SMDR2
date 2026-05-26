## ADDED Requirements

### Requirement: Save Match button is non-blocking

The viewer's Save Match button SHALL submit
`POST /api/files/{file_id}/match-json`, expect an **HTTP 202**
response carrying `{"job_id": "<uuid>", ...}`, and SHALL poll
`GET /api/jobs/{job_id}` until the job reaches a terminal state
(`done` or `error`). The button SHALL remain disabled and a
saving-in-progress status SHALL be visible from the moment the
POST fires until the terminal state is reached. While a Save
Match job is in flight the viewer SHALL suppress further
invocations of Save Match against the same file — clicking the
button is a no-op until the in-flight job resolves.

On `done`, the status line SHALL summarise the result using fields
from `job.result` (at minimum `template_keys.length` and
`total_matches`, and the `saved_to` relative path), the local
`currentFileInfo.match_saved` flag SHALL be set to `true`, and the
role switcher's per-file readiness indicator SHALL be refreshed.
On `error`, the status line SHALL surface `job.error` (or a
generic message if missing) and the in-flight guard SHALL be
released without flipping `match_saved`. In both terminal cases
the button SHALL re-enable.

Polling SHALL run at a cadence of approximately 500 ms — fast
enough that the operator perceives the completion as immediate,
slow enough to avoid request flooding. Transient `GET /api/jobs/`
failures SHALL NOT abort the poll loop; the loop SHALL retry on
the next tick until the underlying job is observed or the user
navigates away.

#### Scenario: POST locks the button and starts polling
- **WHEN** the operator clicks Save Match while no job is in
  flight for the current file
- **THEN** the viewer fires `POST /api/files/{file_id}/match-json`
- **AND** on a `202` response the button becomes `disabled`
- **AND** the status line shows a saving-in-progress message
- **AND** the viewer begins polling `GET /api/jobs/{job_id}`

#### Scenario: Double-click while saving is suppressed
- **WHEN** a Save Match job for the current file is already in
  flight
- **AND** the operator clicks Save Match again
- **THEN** no additional `POST /api/files/{file_id}/match-json` is
  fired
- **AND** the polling loop continues against the original
  `job_id`

#### Scenario: Job done updates status and unlocks the button
- **WHEN** the polled job transitions to `status: "done"`
- **THEN** the status line summarises the result using
  `job.result.template_keys`, `job.result.total_matches`, and
  `job.result.saved_to`
- **AND** `currentFileInfo.match_saved` is set to `true`
- **AND** the role switcher is refreshed
- **AND** the Save Match button is no longer `disabled`

#### Scenario: Job error surfaces error and unlocks the button
- **WHEN** the polled job transitions to `status: "error"`
- **THEN** the status line surfaces `job.error`
- **AND** `currentFileInfo.match_saved` is not changed
- **AND** the Save Match button is no longer `disabled`

#### Scenario: Transient poll failure does not abort the loop
- **WHEN** `GET /api/jobs/{job_id}` returns a transient network
  failure during polling
- **AND** the next poll succeeds
- **THEN** the polling loop continues until the job reaches a
  terminal state
- **AND** the button remains disabled across the transient
  failure

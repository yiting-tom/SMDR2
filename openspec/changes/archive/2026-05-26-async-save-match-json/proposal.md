## Why

The Save Match button currently invokes a synchronous endpoint
(`POST /api/files/{file_id}/match-json`) that runs the full
per-template matching loop in the request handler. On real libraries
this takes several seconds (the same loop the scan-all path uses,
which measures ~7 s for 51 templates) — during that window the
viewer is unresponsive: the operator cannot pan, zoom, frame-select,
or switch role tabs. The fix is to push the work into the existing
`ProcessPoolExecutor` and let the front-end poll for completion, the
same pattern `submit_rule_check` already uses.

## What Changes

- `POST /api/files/{file_id}/match-json` returns **202 + `{job_id, file_id}`**
  instead of the full result. The fast pre-flight checks (file
  resolves, library exists, `parsed/{file_id}.json` present) stay
  in-handler so callers still get a synchronous 4xx for obviously
  bad input.
- A new worker (`_save_match_worker`) executes the existing
  per-class / per-template loop, arbitration, and JSON dump inside
  the `ProcessPoolExecutor`. The on-disk shape of
  `data/match/{file_id}.json` is byte-identical to today.
- The final summary (`template_keys`, `total_matches`, `side_counts`,
  `arbitration_counts`, `saved_to`) is surfaced on
  `GET /api/jobs/{job_id}` as `job.result` once the job's status is
  `done`. The shape matches the current synchronous response 1:1.
- `FILE_STORE.set_match_saved(file_id, True)` moves into the
  job-done callback (`_on_save_match_done`) so the flag flips only
  after the JSON is actually on disk.
- The viewer's Save Match button enters a job-in-flight state on
  POST: disabled, status text shows progress, polls
  `/api/jobs/{job_id}` until `done` / `error`. Same polling shape as
  the dashboard's rule-check tick.

## Capabilities

### New Capabilities
<!-- None; this change reshapes existing capabilities. -->

### Modified Capabilities
- `dxf-pipeline`: the `Per-file Match JSON export` requirement
  changes from synchronous to async — the response shape becomes
  `202 + {job_id}`, the result moves to `GET /api/jobs/{job_id}`
  once the job is done. Persisted JSON shape and key form are
  unchanged.
- `viewer-ui`: gains a `Save Match button is non-blocking`
  requirement modelled on the existing unit-override
  job-in-flight pattern (button disabled, polls, status surfaces
  the resulting `saved_to` / `total_matches`).

## Impact

- **Code**:
  - `app/jobs.py` — add `_save_match_worker`, `submit_save_match`,
    `_on_save_match_done`.
  - `app/main.py` — `POST /api/files/{file_id}/match-json` returns
    202; pre-flight validation kept in-handler.
  - `app/static/canvas.js` — `saveMatchJson` polls
    `/api/jobs/{job_id}`; track an in-flight guard to suppress
    double-clicks.
- **APIs**: response status of `POST /api/files/{file_id}/match-json`
  changes from 200 to 202; body changes from the full summary to
  `{job_id, file_id}`. The summary lives at `GET /api/jobs/{job_id}`
  under `result`. Existing GET `/api/files/{file_id}/match-json`
  (reads the persisted JSON) is unchanged. **BREAKING** for any
  external caller that parsed the POST response body.
- **Tests**: existing `tests/test_match_json_constraints.py`
  scenarios that POST the endpoint synchronously must be adjusted to
  drive the worker function directly (or to await the resulting
  job); add a job-lifecycle test that asserts
  `match_saved` flips in the callback and the persisted JSON exists.
- **Downstream**: DRC bundle materialisation still reads
  `data/match/{file_id}.json` from disk — no change. The 400
  "still need Save Match" gate on `/rule-check` still works because
  `match_saved` is the same flag, just set later.
- **Concurrency**: shares the `MAX_WORKERS = 2` pool with
  preprocess / rule-check / unit-override jobs; rapid repeated
  clicks on Save Match are suppressed by the front-end in-flight
  guard.

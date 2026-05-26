## Context

The Save Match flow today is a single synchronous request handler at
`app/main.py:1125` (`save_match_json`). The handler resolves the
file record, then iterates every class × template in the library,
runs `find_matches_from_pointsets`, splits by side rect, arbitrates
across class groups, and finally writes `data/match/{file_id}.json`.
On a real library (≈ 51 templates) the loop runs sequentially and
takes seconds — long enough for the viewer to feel frozen because
the FastAPI event loop is awaiting that handler.

The codebase already has the right primitive for this: a process
pool that runs three other long jobs (`_preprocess_worker`,
`_rule_check_worker`, `_discover_layers_worker`) with the same
"submit, return 202 + job_id, poll `/api/jobs/{job_id}`" contract.
The viewer also already implements that polling loop on the
dashboard (`_stepRuleCheckJob`) and the unit picker. So this change
is a small structural refactor — there is no new infrastructure.

## Goals / Non-Goals

**Goals:**

- POST `/api/files/{file_id}/match-json` returns 202 immediately;
  the long work runs on the existing `ProcessPoolExecutor`.
- The Save Match button stops blocking the viewer. The button is
  disabled and the status line shows progress until the job
  finishes.
- On success, the persisted `data/match/{file_id}.json` and the
  `file.match_saved` flag are both set, and the response summary
  (`template_keys`, `total_matches`, `side_counts`,
  `arbitration_counts`, `saved_to`) is surfaced under
  `job.result` — same fields as the current synchronous body.
- The DRC submit gate ("these roles still need Save Match: …")
  keeps working without change.

**Non-Goals:**

- Speeding up the matching loop itself. Per the [[project_smdr2_scan_all_perf]]
  memory there is a real win to be had by parallelising across
  templates inside the worker, but that is a separate change.
- Cancellation. There is no Cancel button; an in-flight job runs
  to completion (or process-pool shutdown).
- Job persistence across server restarts. `_jobs` is in-memory like
  every other job kind; if the server restarts mid-job the
  front-end's poll returns 404 and the operator re-clicks. Same
  behaviour as rule-check today.
- Progress reporting (per-template % done). The viewer just shows
  "saving…" until the job resolves.

## Decisions

### Decision 1: Reuse `_get_executor()`, do not spawn a dedicated pool

The pool is sized `MAX_WORKERS = 2` and is shared with preprocess,
rule-check, and unit-override jobs. Save Match contention is real
but rare in practice (one operator per file), and adding a second
pool would double the worker-import cost without solving the
underlying competition with preprocess. If contention becomes a
problem the lever to pull is `MAX_WORKERS`, not pool count.

**Alternative considered:** a per-kind pool. Rejected — the existing
job kinds already share one pool and the project has not hit a
contention ceiling that justifies the complexity.

### Decision 2: Pre-flight validation stays in the request handler

`_resolve_file`, the `LIBRARIES.get(rec.library_id)` lookup, and
the `parsed_path(file_id).exists()` check are all sub-millisecond
filesystem / dict reads. Running them synchronously means obvious
bad input (404, 425, 500) still gets a synchronous 4xx instead of
forcing the caller to poll only to discover the job errored
immediately. The worker re-validates inside the subprocess (it has
to — `FILE_STORE` is per-process) so we are not relying on the
handler's checks for correctness; they exist for caller ergonomics.

### Decision 3: `set_match_saved` flips in the job-done callback

`FILE_STORE` is per-process in-memory state. The worker subprocess
can write `data/match/{file_id}.json` and that survives because the
file lives on disk, but the worker cannot mutate the parent
process's `FILE_STORE`. The `_on_save_match_done` callback runs
in the parent process and is the right place to set the flag —
this mirrors how `_on_discover_done` calls
`FILE_STORE.update_status(file_id, AWAITING_LAYERS)` after the
discover worker returns.

Crucially the flag flips **only on success**. On worker error the
JSON is not on disk and `match_saved` stays false, which keeps the
DRC submit gate honest.

### Decision 4: Response body shape — `{job_id, file_id}` only

This matches the rule-check submit response (`{job_id,
product_id}`). The full summary is reachable via the standard job
GET, no second endpoint required. Returning the full summary on
202 would tempt callers to skip polling.

**Alternative considered:** embed the summary fields as `null`
placeholders in the 202 body. Rejected — invites callers to depend
on the placeholder shape and is inconsistent with sibling submit
endpoints.

### Decision 5: Front-end in-flight guard sits in `canvas.js` module scope

`saveMatchInFlight` (a module-level variable holding the active
`job_id`, or `null`) gates re-entry into `saveMatchJson`. This is
simpler than the dashboard's `Map<productId, …>` shape because the
viewer only ever shows one file at a time — the guard need not be
keyed by file. The button's `disabled` state mirrors the variable
so the UI matches the gate.

### Decision 6: Polling cadence — 500 ms, same as dashboard tick

The matching loop is seconds-scale, so a 500 ms cadence gives
roughly single-digit polls per save. Sub-second cadence makes the
UI feel live; faster than 250 ms is wasteful. No exponential
back-off — the worker either finishes quickly or errors quickly.

## Risks / Trade-offs

- **Risk:** Caller assumes the JSON is on disk immediately after
  POST returns. → **Mitigation:** the 202 body explicitly contains
  `job_id`, and `match_saved` does not flip until the job resolves.
  Any caller that checks `match_saved` (e.g. the role switcher's
  green dot) before polling will see the old value, which is
  correct — the save is genuinely not done yet.

- **Risk:** Double-submit if the operator clicks Save twice
  quickly. → **Mitigation:** the front-end guard (`saveMatchInFlight`
  plus `disabled`) blocks re-entry. The server is also idempotent
  by content — two concurrent workers writing the same JSON
  produces the same bytes — but we still want to avoid the wasted
  CPU.

- **Risk:** Worker error mid-save leaves a partial JSON file.
  → **Mitigation:** the existing worker writes the dump under
  `with open(dst, "w")`. Python truncates on open, so a crash
  before `json.dump` finishes leaves an empty file. The
  `_on_save_match_done` callback distinguishes success from error
  via `fut.result()`; on error `match_saved` stays false. Net
  effect: the operator sees an error toast and the file is treated
  as "Save Match not done" — same recovery path as before.

- **Risk:** Process-pool worker re-imports `app.main` (and
  transitively `LIBRARIES`) on first invocation. → **Mitigation:**
  this cost is already paid by `_preprocess_worker` and the
  existing pool is warm by the time the user clicks Save Match.
  We deliberately do not move the worker into a fresh module to
  avoid duplicating the per-class import block.

- **Trade-off:** the response status code changes from 200 to 202.
  → **Mitigation:** flagged as **BREAKING** in the proposal. Any
  external caller (none known) must update its 2xx success branch.

- **Trade-off:** the existing synchronous test
  (`tests/test_match_json_constraints.py`) becomes integration-
  test-shaped (submit, drive the worker, wait for callback). The
  cleaner approach is to test `_save_match_worker` directly as a
  pure function and keep the endpoint test to "POST returns 202 +
  job_id, worker eventually flips the flag".

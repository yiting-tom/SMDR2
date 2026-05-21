## Why

`POST /api/products/{product_id}/rule-check` is declared `async def` but
runs the entire DRC pipeline (read every role's match JSON, load every
file's `parsed/` shapes, merge handles, call the CPU-bound `check_rules`,
write `rule_check.json`) **synchronously inside the request handler**.
The coroutine never yields, so the event loop is blocked for the full
duration of the run — every other request (dashboard polling, viewer
highlight lookups, status polls from other tabs) waits behind it.

The codebase already has a battle-tested background-job pattern in
`app/jobs.py`: a `ProcessPoolExecutor(max_workers=2)` + in-memory job
dict + done-callback + dev-overrides snapshot for cross-process state.
Preprocessing uses it; rule check should too. The bonus: rule check is
CPU-bound (geometry, polygon distance), so a process worker actually
parallelises it instead of fighting the GIL.

## What Changes

- Split `POST /api/products/{product_id}/rule-check` into a **submit +
  poll** flow:
  - The POST validates the product is `ready_for_rule_check`, snapshots
    the per-role payload (file ids, match JSON paths, parsed paths),
    submits a job to the existing executor, and returns
    **`202 Accepted` with `{ job_id }`** instead of the result.
  - A new `GET /api/jobs/{job_id}` returns the job's current status
    (`queued` / `running` / `done` / `error`), `submitted_at`,
    `started_at`, `completed_at`, and — when done — the persisted
    `rule_check.json` location plus a small summary
    (`rule_count`, `pass_count`, `fail_count`). On `error`, returns
    the worker's error message.
- Move the heavy work to a new picklable `_rule_check_worker` in
  `app/jobs.py`: it does the per-role payload merge (reading match JSON
  + parsed shapes, applying the namespaced-handle rule), calls
  `check_rules`, writes `rule_check.json`, and returns the summary.
- Add `submit_rule_check(product_id)` next to `submit_preprocess`,
  following the same conventions (job dict entry, executor submit,
  done-callback, dev-overrides snapshot).
- Generalise the job-state surface so both job kinds share one polling
  endpoint (`kind: "preprocess" | "rule_check"` in the job record).
  Internal preprocess callers stay unchanged.
- Update the dashboard's "Run rule check" button: POST → take `job_id`
  → poll `GET /api/jobs/{job_id}` on the existing dashboard timer →
  navigate to the report page when status flips to `done`. Show error
  inline when status flips to `error`.
- **GET `/api/products/{product_id}/rule-check`** keeps its current
  behaviour (returns the persisted `rule_check.json` for the product)
  — it stays the read-side endpoint, independent of jobs.

## Capabilities

### New Capabilities
(none — this extends existing capabilities)

### Modified Capabilities
- `design-rule-checking`: the "Rule check API and persistence"
  requirement changes from a synchronous POST-returns-result contract
  to an asynchronous submit + poll contract. New requirement covering
  the shared job-status endpoint and the worker error surface.

## Impact

- **Backend (`app/jobs.py`)**: new `_rule_check_worker`, new
  `submit_rule_check`, new `_on_rule_check_done` callback. The
  `_jobs` dict gains a `kind` discriminator and rule-check-specific
  result fields (`saved_to`, `rule_count`, `pass_count`, `fail_count`).
- **Backend (`app/main.py`)**: `POST /api/products/{product_id}/rule-check`
  becomes thin (validate → submit → 202); new `GET /api/jobs/{job_id}`
  endpoint serves status for both preprocess and rule-check jobs.
- **Frontend (`app/static/dashboard.js`)**: the run-rule-check call site
  switches from "POST returns result" to "POST returns job_id, poll
  until done, then navigate to report". No new UI surface.
- **No change to**: `check_rules` itself, `rule_check.json` format,
  `GET /api/products/{product_id}/rule-check` read endpoint, drc-bundle
  export, report page rendering. The migration is transport-only.
- **Tests**: existing `tests/test_rule_check.py` (which calls
  `check_rules` directly) is unaffected. New integration test covers
  the submit → poll → done flow against the FastAPI app.

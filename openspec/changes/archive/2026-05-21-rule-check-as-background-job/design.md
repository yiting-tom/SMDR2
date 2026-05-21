## Context

`POST /api/products/{product_id}/rule-check` is `async def` but does all
its work synchronously: read every role's match JSON from disk, load
every file's `parsed/{file_id}.json` shapes, merge handles (with the
multi-file namespacing rule), call CPU-bound `check_rules()`, write
`data/rule_check/{product_id}.json`, return the full result in the
response body. The coroutine never awaits, so the FastAPI event loop is
blocked for the whole run; concurrent dashboard polling, viewer
highlight, and status endpoints sit behind it.

`app/jobs.py` already runs preprocessing on a
`ProcessPoolExecutor(max_workers=2)` with an in-memory `_jobs` dict and
a done-callback that pushes results back into `FILE_STORE`. The status
endpoint `GET /api/jobs/{job_id}` (`app/main.py:519`) already returns
the raw job dict and is reused by Phase 1 (`discover`) and Phase 2
(`preprocess`). The `kind` field on the job record is the existing
discriminator — adding a third kind is a small extension, not a new
surface.

Rule check is CPU-bound geometry; running it on a process worker also
unlocks real parallelism vs. the GIL-bound alternative of a thread.

## Goals / Non-Goals

**Goals:**
- POST returns within milliseconds (validation + submit only); the
  rule-check pipeline runs in a worker process.
- Event loop is never blocked by DRC, even on large products.
- Front-end gets a uniform `GET /api/jobs/{job_id}` poll surface that
  works the same shape for preprocess and rule-check jobs.
- Worker errors (exception, missing file mid-run, etc.) surface as
  `status: "error"` with a readable message — never as a hung POST.
- Zero change to `check_rules` logic, `rule_check.json` schema, the
  read-side `GET /api/products/{product_id}/rule-check`, the DRC bundle
  export, or the report page.

**Non-Goals:**
- Persisting jobs across server restarts. The in-memory `_jobs` dict
  loses queued/running jobs on restart — same as preprocess today.
  `rule_check.json` is the durable artifact; a lost job mid-run means
  "press the button again."
- Multiple concurrent rule-check runs per product. Pressing the button
  twice is harmless (second run overwrites the same `rule_check.json`),
  so we don't add a per-product mutex — the same casual policy
  preprocess uses.
- Progress reporting (sub-rule X of Y). `check_rules` runs short
  enough that a binary running/done is sufficient for now; can be
  layered on later via worker → parent IPC if needed.
- A separate queue / Celery / Redis. The existing in-process executor
  is sufficient for this single-user desktop-style deployment.

## Decisions

### D1. Run rule check on the same `ProcessPoolExecutor` as preprocess

**Choice**: reuse `app/jobs.py`'s pool, add `_rule_check_worker` and
`submit_rule_check` alongside the existing preprocess helpers.

**Why**:
- Existing pool already handles the dev-overrides snapshot, the job
  dict lifecycle, the done-callback pattern, and `shutdown()`. Adding
  a third worker is mechanical.
- One pool with `MAX_WORKERS=2` means a long DRC run won't starve a
  pending preprocess (and vice versa) under typical workloads — they
  share concurrency budget. If contention becomes real we can bump
  `MAX_WORKERS` or split pools later; today there's no evidence.

**Alternative considered**: `asyncio.to_thread(check_rules, ...)`.
Cheaper (one-line patch) but keeps the request waiting for the full
result and runs on a thread (less ideal for CPU-bound code under
GIL). Rejected because the user explicitly chose the full job model.

### D2. POST returns 202 + `{ job_id }`; reuse `GET /api/jobs/{job_id}`

**Choice**: `POST /api/products/{product_id}/rule-check` becomes
"validate + submit + return job_id". Polling uses the **already
existing** `GET /api/jobs/{job_id}` endpoint — no new route.

**Why**:
- The existing job-status endpoint returns the raw `_jobs[job_id]`
  dict, which already carries `kind`, `status`, `submitted_at`,
  `started_at`, `completed_at`, `error`. Adding rule-check-specific
  fields to the dict (worker result: `saved_to`, `rule_count`,
  `pass_count`, `fail_count`, `roles_covered`) flows through that
  endpoint for free.
- Unifies front-end polling: dashboard's existing job-poll loop
  already handles preprocess jobs; rule check piggybacks on the same
  loop.

**Alternative considered**: dedicated
`GET /api/products/{product_id}/rule-check/job/{job_id}`. Rejected —
the job status is product-agnostic by design, and a per-product route
would duplicate the existing surface.

### D3. The worker — not the request handler — does the payload merge

**Choice**: move the entire per-role payload build (read match JSON,
load shapes, apply the namespaced-handle rule for multi-file roles)
out of `main.py:1014-1063` and into `_rule_check_worker`. The handler
keeps only the cheap validation (`PRODUCT_STORE.get`, list files,
check `match_saved`, check match JSON files exist on disk).

**Why**:
- File I/O inside the request handler today reads several JSONs
  blocking-style; pushing it into the worker keeps the event loop
  fully free.
- The handler stays a thin policy gate ("can this product run DRC
  now?") matching how `submit_preprocess` is called today.
- Workers are picklable functions: pass primitives (file_ids,
  match_json paths, parsed paths, role names, dev-overrides
  snapshot) — not objects. Same calling convention as
  `_preprocess_worker`.

**Alternative considered**: handler builds the merged
`dxfs_by_role` payload (current behaviour), worker only runs
`check_rules`. Rejected — keeps slow I/O on the event loop and
splits the algorithm awkwardly across two processes.

### D4. Job dict gains `kind: "rule_check"` and result fields

**Choice**: when the worker returns, the done-callback writes the
worker's summary into `_jobs[job_id]["result"]` (same pattern as
preprocess). The result fields are:
- `saved_to`: relative path of the written `rule_check.json`
- `rule_count`, `pass_count`, `fail_count`: same as today's response
- `roles_covered`: sorted role list

Front-end reads `result` once `status === "done"` and then navigates
to the report page (which fetches via the existing read endpoint).

**Why**:
- Mirrors the preprocess job-record shape (`result` field already
  in the contract).
- The persisted artifact (`rule_check.json`) is still the source of
  truth — the job dict's `result` is a small summary for UI.

### D5. Errors surface as `status: "error"` + `error` string

**Choice**: any exception in `_rule_check_worker` is captured by
`_on_rule_check_done` (same shape as `_on_preprocess_done`):
`status = "error"`, `error = str(e)`, `completed_at` set. No
file-store side-effect (DRC isn't per-file, so there's nothing to
mark on `FileRecord`).

**Why**: matches the preprocess error contract; front-end's existing
poll loop already knows how to render `status === "error"` with the
`error` message.

## Risks / Trade-offs

- [In-memory `_jobs` dict survives only for the process lifetime] →
  Mitigation: `rule_check.json` is the durable artifact. After a
  server restart the read-side endpoint still works; the user simply
  presses the rule-check button again if the prior run hadn't
  finished. Same trade-off preprocess accepted; not worth Redis for
  this app.
- [`MAX_WORKERS=2` could be saturated by parallel preprocess +
  rule-check] → Mitigation: jobs queue when the pool is full
  (`ProcessPoolExecutor.submit` returns immediately and queues),
  status stays `running` until a worker is free. If saturation
  becomes a real complaint, bump the worker count — single config
  change.
- [Submitting a second rule-check job while the first is running
  produces two simultaneous overwrites of the same
  `rule_check.json`] → Mitigation: same casual policy preprocess
  uses (last-write-wins). The dashboard button is the only entry
  point; debounce there if needed.
- [Worker process forks pick up a stale dev-overrides snapshot] →
  Mitigation: reuse the existing `_current_dev_overrides()` snapshot
  helper from preprocess. Same handoff path, same guarantees.
- [Pickling cost of passing match-JSON payload to worker] →
  Mitigation: pass *file paths*, not loaded JSON. The worker reads
  them itself. This is how preprocess already passes `src` / `dst`.

## Migration Plan

- Backwards compatibility: the POST currently returns the result
  body. Front-end is the only client of that body. Migration is
  atomic: ship the new handler, the new worker, and the front-end
  change together. No on-disk format changes; existing
  `rule_check.json` files remain readable.
- Rollback: revert the change set; the old synchronous path is
  unchanged in terms of inputs/outputs, so reverting is safe even if
  some `rule_check.json` files were written by the new path
  (identical format).

## Open Questions

- Should the dashboard show the rule-check job in the same "Active
  jobs" tray it shows for preprocess jobs today, or only inline on
  the product card? Defer to implementation; both are cheap with the
  unified status endpoint.

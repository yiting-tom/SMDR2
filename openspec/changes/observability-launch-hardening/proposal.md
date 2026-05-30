## Why

SMDR2 is about to go live as an internal tool, but a production-readiness
assessment (2026-05-30) found its operational weak spot is **observability,
not correctness**: the background worker callbacks emit zero logs, so when a
job fails or hangs in production there is no trace to diagnose it; several
route handlers `json.load()` persisted pipeline files with no guard, turning a
corrupt file into an opaque 500 with no file context; and file upload has no
size limit, so one oversized DXF can freeze the single-box deployment. None of
these are ship-blockers, but each one turns a five-minute incident into an
undebuggable one for the next maintainer. This change adds proportionate
hardening so the team can *see* and *bound* failures after launch.

## What Changes

- **Structured logging in the job layer.** Add a module logger to `app/jobs.py`
  (mirroring the existing `app/dxf.py` pattern) and emit success milestones and
  failure details from the three worker callbacks (`_on_preprocess_done`,
  `_on_save_match_done`, `_on_rule_check_done`). (ERR-005)
- **Crash-safe callbacks.** Wrap each done-callback body so an exception inside
  the callback is logged and transitions the job to `error` instead of leaving
  it stuck at `running` forever (frontend polls indefinitely today). (ERR-009)
- **Guarded JSON reads in route handlers.** Wrap the unguarded `json.load()`
  calls in `app/main.py` (parsed primitives, prematch, match-json, rule-check)
  so a corrupt/truncated file returns a clean error carrying the file path
  rather than an opaque 500. (ERR-001)
- **Re-validate rule-check JSON on read.** Call the existing
  `rule_check._validate_envelope()` when reading persisted rule-check results,
  so an on-disk corruption surfaces loudly instead of as silent wrong
  pass/fail counts. (ERR-004)
- **Upload size limit.** Reject uploads above a configurable byte ceiling with
  HTTP 413. The single upload handler is `POST /api/products/{product_id}/files`
  (`upload_product_file`); the legacy `POST /api/files` no longer exists, so it
  is not in scope. The per-file limit is enforced on the buffered body
  (`len(await file.read())`); an optional request-level `Content-Length`
  pre-check is a weaker early-reject only. (SEC-001)
- **Env-tunable worker count.** Make `jobs.MAX_WORKERS` read
  `SMDR2_MAX_WORKERS` (default 2), mirroring the existing `SMDR2_N_JOBS` /
  `SMDR2_MAX_UPLOAD_MB` convention, so concurrency can be tuned without a code
  edit. (folds in the launch-readiness `magic-number-max-workers` item)
- **Worker store-access rule made discoverable.** Promote the in-function
  comment that warns "workers MUST reload via `Store.load_library`, never the
  per-process `LIBRARIES` cache" to the `jobs.py` module docstring, and add a
  test that fails if any worker entrypoint references `LIBRARIES.get` — guarding
  against a future silent stale-cache regression. (folds in the
  `implicit-store-fresh-load` + `STALE_LIBRARIES_RESIDUAL_RISK` items)
- Tests for each: corrupt-JSON route → clean 400 with file context; oversized
  upload → 413; callback-that-raises → job `error`, never left non-terminal;
  logging asserted via `caplog`; a guard test that no worker calls
  `LIBRARIES.get`.

Status-code convention: corruption of a server-written artifact and envelope
violations are surfaced as HTTP **400** with a contextual detail, consistent
with the existing guarded read in `upload_product_rule_check` (which uses 400).

Behaviour-preserving on the happy path: all existing tests still pass; no new
runtime dependency (stdlib `logging` only); no auth/web-scale machinery.

Out of scope (deferred to post-launch backlog): the broader concurrency lock
hardening — `_on_preprocess_done` mutates `FILE_STORE` outside `_lock`
(`jobs.py` ~413-424); this change makes callbacks *fail-safe* (always reach a
terminal state) but not *race-free*. It touches stable concurrency code and is
tracked separately.

## Capabilities

### New Capabilities
- `operational-observability`: the logging and failure-surfacing contract for
  background jobs and persisted-artifact reads — what the system SHALL log,
  how worker/route failures SHALL be surfaced (job `error` status, clean HTTP
  400 errors with context) rather than swallowed, and the worker store-access
  invariant (workers reload via `Store.load_library`, never the `LIBRARIES`
  cache) with a regression guard.

### Modified Capabilities
- `dxf-pipeline`: the `Multi-file upload with deterministic file IDs`
  requirement gains an upload size-limit rule (reject oversized uploads with
  413, limit env-configurable).

## Impact

- Code: `app/jobs.py` (module logger + callback logging + crash-safe wrapping;
  `MAX_WORKERS` env-tunable; module-docstring store-access rule), `app/main.py`
  (guarded JSON reads at the route-handler level, rule-check envelope
  re-validation in `get_product_rule_check`, upload size check + limit
  constant), `app/rule_check.py` (no change — reuse `_validate_envelope`).
- Tests: `tests/` — new cases for guarded reads (→400), oversized upload (→413),
  crashing callback (→ job `error`), log emission, and a guard that no worker
  calls `LIBRARIES.get`.
- Config: a new upload-size-limit constant `SMDR2_MAX_UPLOAD_MB` and
  `SMDR2_MAX_WORKERS`, both env-overridable, consistent with the existing
  `SMDR2_*` env pattern; both documented in README §10.
- No on-disk format change, no API contract change on the happy path, no new
  dependency.

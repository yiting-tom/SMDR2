## Context

Background work runs on a `ProcessPoolExecutor` in `app/jobs.py`; each
submission registers a `_jobs[job_id]` dict (guarded by `_lock`) and attaches a
done-callback (`_on_preprocess_done`, `_on_save_match_done`,
`_on_rule_check_done`) that runs on a pool thread, reads `fut.result()`, mutates
`FILE_STORE`, and flips the job status. Today these callbacks emit **no logs**
(`jobs.py` has no `import logging`). The `fut.result()` call IS already guarded
(sets the job to `error` on a worker exception), but the **post-result work**
that runs afterward — the `FILE_STORE` mutations at ~`jobs.py:413-424`
(`_on_preprocess_done`) and ~`jobs.py:873` (`_on_save_match_done`) — is
unguarded: an exception there leaves the job at `done` (status already flipped)
with the exception **unlogged and swallowed**, so the file silently fails to
finish its post-processing. Several `app/main.py` route handlers read persisted
pipeline JSON with bare `json.load()`: `_cached_parsed` (line ~86, behind an
`@lru_cache`), `prematch` (~1246), `get_match_json` (~1288),
`get_product_rule_check` (~1346); a corrupt file yields an uncaught
`JSONDecodeError` → opaque 500. The single upload handler `upload_product_file`
(`POST /api/products/{product_id}/files`, line 380) reads the whole body
(`await file.read()` at line 414) with no size ceiling. `app/dxf.py` already
establishes the house logging pattern (`logger = logging.getLogger(__name__)`
at line 35, used at 240/253/538/577) to mirror.

## Goals / Non-Goals

**Goals:**
- A maintainer can diagnose a launch-day job failure from logs alone (which
  file/product, which stage, exception type + detail).
- No callback exception is silently swallowed; every callback drives the job to
  a terminal state and logs any failure.
- A corrupt persisted artifact produces a clean, contextual HTTP 400, not a
  bare 500 or silent wrong counts.
- One oversized upload cannot freeze the box.
- The worker store-access invariant is discoverable and regression-guarded.
- Zero happy-path behaviour change; no new dependency.

**Non-Goals:**
- No metrics/tracing stack, no log aggregation, no OpenTelemetry — stdlib
  `logging` only, proportionate to an internal tool.
- No auth, rate limiting, or multi-tenant isolation.
- The broader concurrency fix — `FILE_STORE` mutation outside `_lock`
  (`jobs.py` ~413-424) — is deferred; this change makes callbacks *fail-safe*
  (always terminal, always logged) but not *race-free*.
- Streaming/pre-buffer upload rejection: the per-file size guard runs after the
  body is buffered (see D5); true pre-stream rejection would need custom
  multipart parsing and is out of scope.

## Decisions

**D1 — Module logger in jobs.py, mirroring dxf.py.** Add `import logging` +
`logger = logging.getLogger(__name__)`. INFO on success milestones
(`preprocess_done file_id=… primitive_count=…`, `save_match_done file_id=…`,
`rule_check_done product_id=… pass_count=…`); WARNING/ERROR on failure with
`type(exc).__name__` + detail. No logging *config* is imposed (no
`basicConfig`) — libraries should not hijack root config; uvicorn/CLI owns
handlers, and tests capture via `caplog`. Logs therefore flow to whatever the
host configures (uvicorn logs to stderr by default), satisfying "survives
worker-process exit" because callbacks run in the **parent** process.
- *Alternative considered:* a custom structured-JSON formatter. Rejected as
  over-engineered for an internal tool; key=value INFO lines are greppable and
  dependency-free.

**D2 — Crash-safe callback wrapper.** Wrap each callback body in
`try/except Exception`; on exception, log at ERROR with `job_id` and set the
job to `error` with the exception detail under `_lock`. The existing
worker-failure path (where `fut.result()` raises) already sets `error`; D2
extends that guarantee to exceptions in the *callback's own* post-result work.
Today such an exception is swallowed *after* the status was flipped to `done`
(`_on_preprocess_done` mutates `FILE_STORE` at ~413-424, `_on_save_match_done`
at ~873) — the job reports success while its post-processing silently failed.
D2 makes that case visible (ERROR log) and honest (job → `error`). Invariant
after this change: **every callback drives the job to a terminal state and logs
any exception; no callback exception is ever swallowed.**

**D3 — Guarded JSON reads via a small helper, called at the handler level.**
Add one helper (`_load_json_or_http(path, *, kind)`) that catches
`json.JSONDecodeError` and `OSError` and raises `HTTPException(status_code=400,
detail=f"{kind} file is unreadable/corrupt: {path}")`. Status code **400**,
matching the existing guarded read in `upload_product_rule_check`
(`main.py:1371,1375`, which uses 400 for both decode and envelope errors) — so
the codebase has one consistent convention for "a persisted JSON artifact is
malformed." The key win is the contextual message (kind + path), not the code.
- **lru_cache placement (critical):** the helper SHALL be invoked at the
  *route-handler* level, NOT inside `_cached_parsed` (which is wrapped in
  `@lru_cache`, `main.py:83`). `lru_cache` caches return values *and raised
  exceptions* keyed by args, so wrapping the raise inside the cached function
  would memoize an `HTTPException` and re-raise it on later calls with the same
  key. Guard outside the cache so only valid parses are cached. The four sites
  are `_cached_parsed`'s caller (the primitives route), `prematch`,
  `get_match_json`, `get_product_rule_check`. (Note: `_cached_shapes` →
  `_shapes_for` is *not* among the four hardened routes and is left as-is.)
- *Alternative considered:* 500 for server-written artifacts. Rejected: it
  splits the convention (upload path already uses 400) for no operator benefit
  in an internal tool — the message is what matters.

**D4 — Rule-check envelope re-validation on read.** In `get_product_rule_check`
(`main.py:~1346`), after `json.load`, call `rule_check._validate_envelope(result)`
and map a raised `RuleCheckOutputError` to `HTTPException(status_code=400,
detail=f"rule-check JSON failed envelope validation: {exc}")` — the same
try/except pattern already in `upload_product_rule_check` (`main.py:1372-1375`).
Reuses a pure, battle-tested function — no new logic. 400 here matches D3.

**D5 — Upload size limit; per-file guard on the buffered body.** Add
`MAX_UPLOAD_BYTES = int(os.environ.get("SMDR2_MAX_UPLOAD_MB", "300")) * 1024 * 1024`
(consistent with the `SMDR2_*` env convention). The **primary** guard is on the
actual buffered body: in `upload_product_file`, right after `content = await
file.read()` (line 414, where the existing empty-upload check lives), reject
with `HTTPException(status_code=413, ...)` when `len(content) > MAX_UPLOAD_BYTES`.
- **Why not Content-Length pre-check as the primary defense:** in
  `multipart/form-data` the request `Content-Length` covers the *entire* body
  (all form fields + boundaries), not the individual file part — FastAPI's
  `UploadFile` does not expose a per-part length, and by the time the handler
  runs the body is already buffered into a `SpooledTemporaryFile`. So a
  Content-Length check can only catch "the whole request is too big" and cannot
  reliably bound a single file. We MAY add it as a cheap early-reject
  (request-level), but the `len(content)` check is the real per-file guarantee.
- **Timing honesty:** because the body is buffered before the handler runs, this
  guard is defense against *accidental* huge uploads (wrong file, corrupted
  export — the realistic internal-tool case), not against an adversary who wants
  to exhaust memory before the check. True pre-stream rejection is out of scope
  (Non-Goals). 300 MB is generous for packaging DXFs.

**D6 — `MAX_WORKERS` env-tunable.** Change `jobs.py:42` from `MAX_WORKERS = 2`
to `MAX_WORKERS = int(os.environ.get("SMDR2_MAX_WORKERS", "2"))` (add
`import os`), mirroring `SMDR2_N_JOBS` (`matching.py`) and `SMDR2_MAX_UPLOAD_MB`
(D5). One line; documented in README §10. Folded in because it is the identical
pattern to D5 and omitting it would be inconsistent.

**D7 — Worker store-access rule: docstring + regression guard.** The 40-line
in-function comment in `_save_match_worker` (`jobs.py` ~705-717) explains that
workers MUST reload via `Store.load_library` and never the per-process
`LIBRARIES` cache (which goes stale across jobs in the same worker). Promote a
concise version of that rule to the `jobs.py` **module docstring** so a future
maintainer adding a job type sees it. Add a test that scans the worker
entrypoints in `app/jobs.py` and fails if `LIBRARIES.get` appears in worker
code — a zero-cost guard against silent stale-cache data loss. Folded in
because both are documentation/test-only, zero runtime risk, same theme.

## Risks / Trade-offs

- **[Logging too chatty]** → INFO milestones are one line per job stage; at this
  tool's job volume that is negligible. Failures are WARNING/ERROR.
- **[Guarded-read status code]** → today these paths emit an uncaught 500 on
  corruption; switching to a contextual 400 is a strict improvement and matches
  the existing `upload_product_rule_check` convention, so there is one rule
  across the codebase. No happy-path response changes.
- **[Upload guard buffers before checking]** → accepted and documented: this
  bounds accidental huge uploads, not adversarial memory exhaustion (an internal
  trusted-user tool). Pre-stream rejection is deferred (Non-Goals).
- **[Size limit set too low]** → env-overridable and caught in smoke testing;
  default 300 MB is well above real packaging DXFs.
- **[caplog/log assertions brittle]** → assert on a stable substring + level,
  not exact format.
- **[Store-access grep guard false-negative]** → the test matches `LIBRARIES.get`
  textually in worker functions; an alias could slip past, but it catches the
  obvious regression at zero cost and documents intent.

## Migration Plan

In-process hardening only — no data/schema/format migration. Deploy is a normal
merge; rollback is `git revert`. Operators may set `SMDR2_MAX_UPLOAD_MB` and
`SMDR2_MAX_WORKERS` to tune the upload ceiling and worker concurrency.

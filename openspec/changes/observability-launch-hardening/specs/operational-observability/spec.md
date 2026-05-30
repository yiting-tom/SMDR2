## ADDED Requirements

### Requirement: Background jobs emit structured logs

The background job layer (`app/jobs.py`) SHALL log the lifecycle of every
worker job through the standard library `logging` module, using a module-level
logger (`logging.getLogger(__name__)`), mirroring the existing `app/dxf.py`
convention. The module SHALL NOT call `logging.basicConfig` or otherwise
reconfigure the root logger — handler configuration is the host process's
responsibility (uvicorn / CLI).

On successful completion, each worker done-callback SHALL emit an INFO record
identifying the job and a stage-appropriate summary: preprocess logs the
`file_id` and resulting `primitive_count`; save-match logs the `file_id`;
rule-check logs the `product_id` and `pass_count`.

On failure (the worker future raised, or the callback's own post-result work
raised), the callback SHALL emit a WARNING-or-higher record carrying the
`job_id`, the failing `file_id`/`product_id`, and the exception type name plus
detail.

#### Scenario: Successful preprocess is logged at INFO
- **WHEN** a preprocess job completes successfully
- **THEN** an INFO log record is emitted containing the `file_id` and the
  `primitive_count`

#### Scenario: Worker failure is logged with exception context
- **WHEN** a worker future raises an exception
- **THEN** a WARNING-or-higher log record is emitted containing the `job_id`
  and the exception type name and detail

### Requirement: Job callbacks always reach a terminal state

A worker done-callback SHALL NOT swallow an exception raised by its own
post-result work. Today such an exception is lost *after* the job status has
already been flipped to `done`, so the job falsely reports success while its
post-processing (e.g. `FILE_STORE` mutations) silently failed. Each callback
SHALL guard its body so that any exception raised inside the callback
(including failures of `FILE_STORE` mutations) is logged at ERROR with the
`job_id` and transitions the job to `error` with the exception detail recorded.
After any callback returns, the job SHALL be in a terminal state
(`ready`/`report`/`error` as appropriate for that job kind) — never
non-terminal, and never `done` with a swallowed exception.

#### Scenario: Callback exception transitions the job to error
- **WHEN** a worker future succeeds but the done-callback raises while doing
  its post-result work (e.g. a `FILE_STORE` update throws)
- **THEN** the job status becomes `error` and the error detail is recorded
- **AND** the job is not left reporting `done`

#### Scenario: An ERROR log accompanies a callback crash
- **WHEN** a done-callback raises during its post-result work
- **THEN** an ERROR log record is emitted carrying the `job_id`

### Requirement: Workers reload library state from the store, never the cache

Worker entrypoints in `app/jobs.py` SHALL load library/template state via
`Store.load_library` (a fresh read), and SHALL NOT read the process-level
`LIBRARIES` cache, which is seeded only by in-process mutations and goes stale
across jobs handled by the same reused worker process. This invariant SHALL be
recorded in the `app/jobs.py` module docstring so it is visible to anyone
adding a new job type, and SHALL be protected by a regression test that fails
if `LIBRARIES.get` appears in worker code.

#### Scenario: No worker entrypoint references the LIBRARIES cache
- **WHEN** the test suite scans the worker functions in `app/jobs.py`
- **THEN** none of them reference `LIBRARIES.get`
- **AND** the rule is documented in the module docstring

### Requirement: Persisted-artifact reads surface corruption with context

The system SHALL guard every route handler that reads a persisted pipeline
artifact from disk (`parsed/{file_id}.json`, `prematch/{file_id}.json`,
`match/{file_id}.json`, `rule_check/{product_id}.json`) so that a malformed
file never propagates as an uncaught exception. When a read encounters invalid
JSON or an OS read error, the handler SHALL raise `HTTPException` with status
**400** whose detail identifies the artifact kind and path, consistent with the
existing guarded read in `upload_product_rule_check`. The guard SHALL be applied
at the route-handler level, NOT inside the `@lru_cache`-wrapped `_cached_parsed`
helper, so that a raised exception is never memoized and re-raised on a later
cache hit.

For rule-check results specifically, the `get_product_rule_check` read path
SHALL additionally re-validate the loaded payload against the RuleChecking
envelope contract (via `rule_check._validate_envelope`); a raised
`RuleCheckOutputError` SHALL be mapped to an `HTTPException` with status **400**
and context, so a structurally-corrupt-but-parseable file surfaces loudly
instead of producing silent wrong pass/fail counts.

#### Scenario: Corrupt parsed JSON returns a contextual 400
- **WHEN** a route reads `parsed/{file_id}.json` and the file is not valid JSON
- **THEN** the response is HTTP 400 whose detail names the artifact and path
- **AND** the response is not an unannotated generic 500

#### Scenario: A cached read does not memoize a corruption error
- **WHEN** a corrupt `parsed/{file_id}.json` is later replaced by a valid file
- **THEN** a subsequent read succeeds rather than re-raising the earlier error
  from a cached exception

#### Scenario: Structurally invalid rule-check JSON is rejected on read
- **WHEN** `get_product_rule_check` reads a persisted result that parses as
  JSON but violates the RuleChecking envelope contract
- **THEN** the handler raises HTTP 400 rather than returning miscounted
  pass/fail values

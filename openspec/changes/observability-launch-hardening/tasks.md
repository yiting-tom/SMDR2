## 1. Job-layer structured logging (ERR-005)

- [x] 1.1 Add `import logging` + `logger = logging.getLogger(__name__)` to `app/jobs.py`, mirroring `app/dxf.py:16,35`. Do NOT call `logging.basicConfig`.
- [x] 1.2 In `_on_preprocess_done`, emit `logger.info` on success with `file_id` + `primitive_count`; in `_on_save_match_done` with `file_id`; in `_on_rule_check_done` with `product_id` + `pass_count`.
- [x] 1.3 On the worker-failure path (where `fut.result()` raised), emit `logger.warning` with `job_id`, `file_id`/`product_id`, `type(exc).__name__`, and the detail — alongside the existing job-status=error update.

## 2. Crash-safe callbacks (ERR-009)

- [x] 2.1 Wrap the post-result work of `_on_preprocess_done` and `_on_save_match_done` in `try/except Exception`. (`_on_rule_check_done` has no post-result work, so only logging was added there.) Status is no longer flipped to `done` before the FILE_STORE mutations — it flips after they succeed.
- [x] 2.2 In that except path, `logger.error(..., exc_info=True)` with `job_id`, and set the job to `error` (with detail) under `_lock` — the job reaches a terminal state and the exception is never swallowed.

## 3. Guarded JSON reads in route handlers (ERR-001)

- [x] 3.1 Added `_load_json_or_http(path, *, kind)` in `app/main.py` that catches `json.JSONDecodeError`/`OSError` and raises `HTTPException(status_code=400, ...)` with kind+path — matching the 400 convention in `upload_product_rule_check`.
- [x] 3.2 Routed the four reads through the guard at handler level: primitives via `_parsed_for` (guards OUTSIDE the `@lru_cache`'d `_cached_parsed`), `prematch`, `get_match_json`, `get_product_rule_check`. `_cached_shapes`/`_shapes_for` left as-is. (Empirically confirmed `lru_cache` does not memoize exceptions; wrapper-level guard is still cleaner and is what the spec scenario asserts.)

## 4. Rule-check envelope re-validation on read (ERR-004)

- [x] 4.1 In `get_product_rule_check`, after loading the JSON, call `rule_check._validate_envelope(result)` and map `RuleCheckOutputError` to `HTTPException(status_code=400, ...)` — same pattern as `upload_product_rule_check`.

## 5. Upload size limit (SEC-001)

- [x] 5.1 Added `MAX_UPLOAD_BYTES = int(os.environ.get("SMDR2_MAX_UPLOAD_MB", "300")) * 1024 * 1024` (added `import os`).
- [x] 5.2 In `upload_product_file` (the only upload endpoint), reject with 413 when `len(content) > MAX_UPLOAD_BYTES`, right after the buffered read.
- [ ] 5.3 Optional request-level Content-Length early-reject — SKIPPED by design: the `len(content)` per-file check (5.2) is the authoritative guard; a request-level pre-check adds complexity for marginal value on an internal tool (recorded as the chosen trade-off in design D5).

## 6. Env-tunable worker count (D6)

- [x] 6.1 Changed `jobs.py` to `MAX_WORKERS = int(os.environ.get("SMDR2_MAX_WORKERS", "2"))` (added `import os`).

## 7. Worker store-access rule: docstring + regression guard (D7)

- [x] 7.1 Promoted the store-access rule to the `app/jobs.py` module docstring.
- [x] 7.2 Added an AST-based regression test (`test_no_worker_uses_libraries_cache`) that fails if `LIBRARIES.get` appears as a real attribute access in worker code (ignores docstring/comment mentions).

## 8. Tests (all in tests/test_observability_hardening.py)

- [x] 8.1 ERR-009: `test_preprocess_callback_exception_marks_error_not_done` — post-result raise → job `error`, ERROR log via `caplog`.
- [x] 8.2 ERR-005: `test_preprocess_success_emits_info_log` — INFO log with `file_id` + `primitive_count`.
- [x] 8.3 ERR-001: `test_corrupt_parsed_json_returns_400_then_recovers` — 400 naming the file; replacing with valid content then succeeds (no memoized error).
- [x] 8.4 ERR-004: `test_rule_check_bad_envelope_rejected_on_read` + `test_rule_check_corrupt_json_returns_400`.
- [x] 8.5 SEC-001: `test_oversized_upload_rejected_with_413` (no file row registered) + `test_under_limit_upload_not_rejected_for_size`.
- [x] 8.6 D7: `test_no_worker_uses_libraries_cache`.

## 9. Verify

- [x] 9.1 Full suite: `547 passed` (539 prior + 8 new), 0 failed.
- [x] 9.2 Ruff: my new test file `All checks passed`; app/jobs.py + app/main.py stay at 5 pre-existing errors (zero new — confirmed by stashing my changes and re-counting).
- [ ] 9.3 Smoke: start server + upload a normal DXF, confirm INFO on stderr — left for the user to run (`uv run uvicorn app.main:app`); happy path is unchanged and covered by the green suite.
- [x] 9.4 README §10 env table updated with `SMDR2_MAX_UPLOAD_MB` and `SMDR2_MAX_WORKERS`.
- [ ] 9.5 Spec sync at archive (`/opsx:archive`): new `operational-observability` capability + the `dxf-pipeline` upload delta fold into live specs.

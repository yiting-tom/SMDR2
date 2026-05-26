## 1. Worker

- [x] 1.1 Add `_rule_check_worker(product_id, role_specs, dst, dev_overrides_snapshot)` to `app/jobs.py`. Inputs are small picklables: `role_specs` is a list of `{role, file_ids, match_json_paths, parsed_paths, namespaced}` records; `dst` is the absolute path of `data/rule_check/{product_id}.json`.
- [x] 1.2 Inside the worker, apply `apply_snapshot(dev_overrides_snapshot)` (mirroring `_preprocess_worker`) before any rule logic touches module-level defaults.
- [x] 1.3 Inside the worker, build the merged `dxfs_by_role: dict[str, RoleBundle]` from `role_specs`: read each role's match JSONs, load entity shapes from each parsed JSON (use the same `_shapes_for` logic that `app/main.py:1046` uses today — extract a shared helper into `app/matching.py` or `app/storage.py` so both the request handler and the worker can call it without duplicating). When `namespaced` is true, prefix every handle in both the shapes dict and the match-JSON groups with `<short_file_id>:`.
- [x] 1.4 Invoke `check_rules(product_id, dxfs_by_role)`, write the result to `dst` (creating parent dirs), and return a small summary dict: `{product_id, saved_to (relative to DATA_DIR.parent), rule_count, pass_count, fail_count, roles_covered}`.
- [x] 1.5 Confirm `_rule_check_worker` only references importable modules (no closures, no FastAPI app state) and that all of its arguments are picklable — required for the `ProcessPoolExecutor` boundary.

## 2. Job orchestration

- [x] 2.1 Add `submit_rule_check(product_id, role_specs)` to `app/jobs.py` mirroring `submit_preprocess`: create a job_id, write a `_jobs[job_id]` entry with `kind: "rule_check"`, `product_id: product_id`, `status: "queued"`, timestamps, then `_executor.submit(_rule_check_worker, ...)` with `_current_dev_overrides() or None`, then transition to `running`.
- [x] 2.2 Add `_on_rule_check_done(job_id, fut)` modeled on `_on_preprocess_done`: on success, store `result = fut.result()`, set `status = "done"` and `completed_at`; on exception, capture traceback, set `status = "error"` and `error = str(e)`. Unlike preprocess, no `FILE_STORE` side-effect.
- [x] 2.3 Wire the done-callback via `fut.add_done_callback(lambda f: _on_rule_check_done(job_id, f))` inside `submit_rule_check`.

## 3. HTTP handler refactor

- [x] 3.1 In `app/main.py`, refactor `run_product_rule_check` (currently `app/main.py:996`) to: (a) keep the existing validations (`PRODUCT_STORE.get`, `FILE_STORE.list_by_product`, `match_saved` check, match-JSON existence check); (b) build the lightweight `role_specs` list (file_ids + paths only, no JSON reads, no shape loading); (c) call `jobs.submit_rule_check(product_id, role_specs)`; (d) return `{ "job_id": job_id, "product_id": product_id }` with HTTP `202`.
- [x] 3.2 Return `202` explicitly (e.g., `Response(content=json.dumps(...), status_code=202, media_type="application/json")` or `JSONResponse(content=..., status_code=202)`), so the front-end can distinguish "job submitted" from a (legacy) synchronous completion.
- [x] 3.3 Leave `GET /api/products/{product_id}/rule-check` (`app/main.py:1082`) untouched — it still serves the persisted `rule_check.json` as before.
- [x] 3.4 Verify `GET /api/jobs/{job_id}` (`app/main.py:519`) returns the rule-check job record unchanged (it already serves `_jobs[job_id]` raw, so the new `kind` and `result` fields flow through automatically).

## 4. Front-end (dashboard)

- [x] 4.1 In `app/static/dashboard.js`, locate the "Run rule check" call site and switch from "POST → expect full result in body" to "POST → expect `{ job_id }` → enqueue the job in the existing dashboard job-poll loop".
- [x] 4.2 Extend the dashboard's job-poll handler (the same one that watches preprocess jobs) to switch on `job.kind`: when `kind === "rule_check"` and `status === "done"`, navigate to the product's rule-check report page (the existing report URL).
- [x] 4.3 When `status === "error"`, surface `job.error` as a dashboard toast / inline error on the product card. Match the existing preprocess error UI.
- [x] 4.4 Disable the "Run rule check" button while a rule-check job for that product is `queued` or `running`, re-enable on `done` or `error`. (Detect via the local job-poll state, not an extra API call.)

## 5. Tests

- [x] 5.1 Unit test `_rule_check_worker` end-to-end with a small fixture product (two roles, one bare-handle, one namespaced multi-file) — assert the returned summary and that the on-disk `rule_check.json` matches what `check_rules` would have produced when called directly.
- [x] 5.2 Integration test against the FastAPI app: `POST /api/products/{product_id}/rule-check` returns `202` + `job_id`, and `_jobs[job_id]["kind"] == "rule_check"`.
- [x] 5.3 Integration test: poll `GET /api/jobs/{job_id}` until `status === "done"`; assert `result.saved_to` exists on disk; assert `GET /api/products/{product_id}/rule-check` returns the same result.
- [x] 5.4 Integration test for the error path: delete a required match JSON between submit and worker execution (or use a deliberately broken fixture); assert the job transitions to `status === "error"` with a non-empty `error` message, and `rule_check.json` from any prior successful run is not overwritten.
- [x] 5.5 Integration test for event-loop responsiveness: while a rule-check job is running, hit any unrelated endpoint (e.g., `GET /api/products`) and assert the response is served quickly (well under the DRC run duration). Use a fixture with intentionally heavy geometry, or monkeypatch `check_rules` to sleep, to make the contention measurable.

## 6. Verification & rollout

- [x] 6.1 Confirm `tests/test_rule_check.py` (which calls `check_rules` directly) still passes — no changes expected. *(All 26 existing tests still pass; 4 new tests in `tests/test_rule_check_job.py` cover the job path.)*
- [ ] 6.2 Manual smoke test in the browser: upload a real product, click "Run rule check", observe the dashboard button disables, the row shows progress, and the rule-check modal opens automatically when the job finishes. *(Not yet run — pytest can't drive the dashboard JS. Flagged for verification before merge.)*
- [x] 6.3 Update `MEMORY.md` reference `project_smdr2_pipeline` if the pipeline description names rule check as a synchronous step (re-read first to confirm; update only if needed). *(Updated step 8 to describe the async job flow; clarified that rule check is product-scoped and no longer drives a file-level `checking_rules` status.)*

## 7. Cross-session job recovery (follow-up — user reported gap)

Without this section the dashboard's `ruleCheckJobs` Map only lives in
the active tab's JS state: if the user navigates to the viewer (or
reloads) while a job is running, the dashboard on return shows a
stale "Re-run Rule Check" button instead of resuming polling or
surfacing the completed result.

- [x] 7.1 Add `latest_rule_check_job(product_id) -> dict | None` to `app/jobs.py` that searches the in-memory `_jobs` dict for the latest `kind == "rule_check"` entry with matching `product_id`. Sorted by `submitted_at`.
- [x] 7.2 Include `latest_rule_check_job` (projected to `{job_id, status, submitted_at, completed_at, error, result}`) on every product in `GET /api/products` and `GET /api/products/{product_id}`.
- [x] 7.3 In `app/static/dashboard.js`, add a `_syncRuleCheckJobsFromProducts()` step inside `refresh()`. For each product's `latest_rule_check_job`: when `queued`/`running`, ensure it's tracked in `ruleCheckJobs` and `startPollingIfBusy()`; when `done`/`error`, dedupe via a localStorage-backed `seenRuleCheckJobs` set so the modal pops at most once per finished job per browser.
- [x] 7.4 In `_stepRuleCheckJob`, also mark the job_id as seen on done/error so the cross-session sync doesn't re-pop the modal for jobs the in-tab path already handled.
- [x] 7.5 Add a regression test (`tests/test_rule_check_job.py::test_products_endpoint_exposes_latest_rule_check_job`) asserting the new field is null pre-submit, carries the live job during run, and reports `status: "done"` + a populated `result` after completion.

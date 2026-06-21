## 1. Refresh the pre-match snapshot on Save Match

- [x] 1.1 `app/jobs.py` `_save_match_worker`: before the per-class loop, init `prematch_sets: dict[str, set[str]] = {}`.
- [x] 1.2 In the per-template body, after `find_matches_from_pointsets(...)` and **before** `split_matches_by_side`, accumulate the raw handle union per display class: `if result.matches: bucket = prematch_sets.setdefault(cls_name, set()); for m in result.matches: bucket.update(m.handles)`.
- [x] 1.3 After the Match JSON is written, rewrite `prematch_path(file_id)` with `{by_class: {cls: sorted(handles)}, total}` from `prematch_sets`. Wrap best-effort: on failure `logger.warning(..., exc_info=True)` and leave the old snapshot; never fail the job.

## 2. Test

- [x] 2.1 `tests/test_match_json_constraints.py`: add `test_save_match_worker_refreshes_prematch_snapshot` — seed a stale `{by_class:{}, total:0}` at `prematch_path(fid)`, run `_save_match_worker` with a library whose (unconstrained) template matches handles, assert the snapshot is rewritten to include that class with those handles.
- [x] 2.2 `pytest tests/test_match_json_constraints.py tests/test_contained_match_suppression.py -q` — green (no regression in the save-match / prematch contract). Full suite also green: 547 passed.

## 3. Verify

- [x] 3.1 `openspec validate save-match-refreshes-prematch --strict` — valid.

## 4. Archive

- [ ] 4.1 `/opsx:archive save-match-refreshes-prematch` after verification.

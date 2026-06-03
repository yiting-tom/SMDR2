## 1. Re-preprocess honours the stored override

- [x] 1.1 In `app/jobs.py` `submit_reprocess_all`, pass each file's persisted override and product scope to `_preprocess_worker`: add `rec.user_unit_override` (the worker's 9th arg `user_unit_override`) and `rec.product_id` (10th arg `product_id`) to the `submit(...)` call, which currently stops at `overrides_snap`. Keep the existing positional order intact.
- [x] 1.2 Confirm the worker path: reprocess-all always passes `transient_primitives=None`, so `use_cache` is False and `_preprocess_worker` re-parses via `flatten_for_render(..., user_unit_override=...)` — the override multiplier is re-applied and the detector is skipped.

## 2. Startup migration excludes overridden files

- [x] 2.1 In `app/main.py` `_submit_unit_rescale_migration`, add `rec.user_unit_override is None` to the targeting filter so a file with an explicit override is never queued (alongside the existing `applied_scale == 1.0` and detector-factor checks).

## 3. Tests

- [x] 3.1 Regression: drives the real `submit_reprocess_all` dispatch (fake executor capturing the worker args) and asserts the file's stored `user_unit_override` ("mm") reaches the worker — the exact arg that was `None` before the fix. (`tests/test_reprocess_unit_override.py`)
- [x] 3.2 Migration targeting: `_submit_unit_rescale_migration` does NOT queue an overridden file (mirrors the existing migration test) while still queuing an equivalent file with no override.
- [x] 3.3 `submit_reprocess_all` passes `product_id` through (asserted in the same dispatch-capture test); a no-override file gets `None` for both args.
- [x] 3.4 Full suite green: 523 passed (`-p no:randomly`); 3 new regression tests added.

## 4. Verification & archive

- [ ] 4.1 **[USER]** Manual: override a unit-suspect file to "mm" in the viewer, restart the server, confirm the file still shows the override (picker + geometry) and was not re-rescaled; confirm a non-overridden unit-suspect file still auto-rescales on boot as before.
- [ ] 4.2 `/opsx:archive reprocess-respects-unit-override` after manual verification (folds the dxf-pipeline deltas into the live spec).

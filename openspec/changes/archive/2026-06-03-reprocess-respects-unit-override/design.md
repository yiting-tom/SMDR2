## Context

Unit handling has two authorities: the auto-detector (`detect_scale_factor`,
keyed on `insunits` + bbox diagonal) and an operator override
(`files.user_unit_override`). `flatten_for_render` honours an override and skips
the detector when one is set. The override reaches the worker via the
preprocess submit path: `submit_preprocess` falls back to `rec.user_unit_override`
when its arg is `None` (`app/jobs.py:318-322`).

The re-preprocess path does not. `submit_reprocess_all` (`app/jobs.py:950-1006`)
dispatches `_preprocess_worker` with eight positional args, stopping at
`dev_overrides_snapshot` — so the worker's `user_unit_override` (9th) and
`product_id` (10th) params both default to `None`, and the worker re-runs the
auto-detector. The startup migration `_submit_unit_rescale_migration`
(`app/main.py:212-246`) drives `submit_reprocess_all`, and its targeting
(`applied_scale == 1.0` AND detector ≠ `1.0`) matches the canonical
"override to mm on a unit-suspect file" case — so that file is re-queued every
boot and re-rescaled.

## Goals / Non-Goals

**Goals:**
- A persisted `user_unit_override` is re-applied (not re-detected) by every
  re-preprocess path: the dev `reprocess-all` and the startup migration.
- The startup migration leaves overridden files alone.
- `reprocess-all` also restores the product scope it currently drops.

**Non-Goals:**
- No change to `detect_scale_factor`, the override enum, the picker UI, or the
  override set/clear endpoints.
- No data migration. Files already clobbered by a previous boot are not
  retroactively repaired here; they self-heal on the next override-respecting
  reprocess.
- The "Setting the picker to the detector's natural choice clears the override"
  behaviour (`_maybe_clear_redundant_unit_override`) is unrelated and unchanged.

## Decisions

**D1 — Thread the stored override (and product scope) through `submit_reprocess_all`.**
Pass `rec.user_unit_override` as the worker's `user_unit_override` arg and
`rec.product_id` as `product_id`, per file. This mirrors `submit_preprocess`'s
existing fallback and is the single-point fix for *all* reprocess-all callers
(dev endpoint + boot migration). *Alternative rejected:* having the worker itself
re-read the override from the DB — the worker is deliberately DB-light and the
submit layer is already where override resolution lives.

**D2 — Exclude overridden files from the startup migration (defence in depth).**
Add `rec.user_unit_override is None` to `_submit_unit_rescale_migration`'s
targeting filter. Even with D1 making the reprocess override-safe, an
override-to-mm file would otherwise be re-queued every boot as a no-op churn;
more importantly it encodes the intent — the auto-rescale migration exists to fix
*un-decided* legacy files, and an explicit override is a decision. *Alternative
rejected:* relying on D1 alone — correct on geometry but leaves pointless
per-boot reprocessing and a confusing "why did my overridden file get a job?"
signal.

**D3 — No retroactive repair.** Rows whose override was already overwritten by a
past boot have `applied_scale` = detector factor with `user_unit_override` still
set. We do not add a migration to recompute them; the inconsistency resolves the
next time that file is reprocessed under D1 (or the operator re-picks the unit).
Keeps the change small and avoids a one-shot data fixup that could surprise.

## Risks / Trade-offs

- **Existing inconsistent rows (override set, geometry detector-rescaled)** →
  not auto-repaired (D3). Mitigation: documented; re-applying the override (or any
  reprocess) heals it. Low blast radius — only files overridden before this fix.
- **Positional-arg dispatch is brittle** → `submit_reprocess_all` passes worker
  args positionally; adding the 9th/10th must line up with the worker signature.
  Mitigation: the regression test exercises the real dispatch, not a stub.
- **Migration idempotence scenario** → the existing "Migration is idempotent"
  spec scenario still holds; the new exclusion only narrows what's targeted.

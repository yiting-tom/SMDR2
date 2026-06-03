## Why

A per-file unit override set from the viewer's unit picker is silently undone on
the next server restart. The override is persisted (`files.user_unit_override`)
and a normal preprocess honours it, but the re-preprocess path does not — so the
most common override ("this file really is mm, don't auto-rescale") is overwritten
by the auto-detector on every boot, snapping the geometry back to the rescaled
state the operator explicitly rejected.

## What Changes

- **Fix the re-preprocess path to honour the stored override.** `submit_reprocess_all`
  currently calls the preprocess worker without passing each file's
  `user_unit_override` (or `product_id`), so the worker re-runs the auto-detector
  instead of re-applying the operator's unit. It SHALL pass the persisted override
  (re-applying its multiplier, skipping the detector) and the file's product scope,
  matching what a normal `submit_preprocess` already does.
- **Exclude overridden files from the startup unit-rescale migration.** The one-shot
  boot migration re-queues files where `applied_scale == 1.0` and the detector would
  now pick a non-`1.0` factor — which is exactly an "override to mm on a unit-suspect
  file". It SHALL skip files that carry an explicit `user_unit_override`; the
  operator has authority and the migration must not touch them (also stops the
  needless re-preprocess of those files on every boot).
- Net effect: an explicit unit override survives server restarts, the dev
  `reprocess-all`, and the boot migration. No change to the auto-detector itself,
  to files without an override, or to the override-set/clear endpoints.

## Capabilities

### New Capabilities

<!-- None — this corrects existing dxf-pipeline behaviour. -->

### Modified Capabilities

- `dxf-pipeline`: re-preprocessing any file (the dev `reprocess-all` job and the
  startup unit-rescale migration) now re-applies a persisted `user_unit_override`
  and the file's product scope instead of re-running the auto-detector; and the
  startup migration excludes files that carry an explicit override.

## Impact

- **Code:** `app/jobs.py` `submit_reprocess_all` (pass `rec.user_unit_override`
  and `rec.product_id` to `_preprocess_worker`); `app/main.py`
  `_submit_unit_rescale_migration` (skip rows with a non-null `user_unit_override`).
- **Behaviour:** overridden files keep their unit across restart / reprocess-all;
  previously they reverted to the detector's factor. Files without an override are
  unaffected.
- **Data:** no schema change; no migration. (Files whose override was already
  clobbered by a prior boot self-heal once their override is re-applied — e.g. the
  operator re-confirms the unit, or the next reprocess now preserves it.)
- **Tests:** backend regression test that an override survives `submit_reprocess_all`
  and the boot-migration targeting, plus that `reprocess-all` supplies `product_id`.

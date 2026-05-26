## Why

The auto-rescale detector added in `auto-normalize-unit-suspect-dxf` catches the
common 1000×-too-big / 1000×-too-small cases, but it is a heuristic: it can
still pick the wrong power of 10 when a DXF's bbox happens to be ambiguous, and
it never overrides a DXF's `INSUNITS` declaration even when that declaration is
itself wrong. Today the engineer has no in-product way to correct either
mistake — they have to edit the DXF or wait for someone to ship a code fix.
Adding a viewer-side override turns this into a one-click correction the
operator can make the moment they see the geometry look wrong.

## What Changes

- Add a unit picker control to the top of the viewer's first column.
  Options: `mm`, `cm`, `m`, `inch`, `μm`. Default selection reflects the unit
  currently in effect for the file (derived from `applied_scale` + source
  `INSUNITS`).
- When the operator changes the selection and confirms a warning modal, the
  backend treats the file as if it had been uploaded with that unit:
  re-runs DXF preprocess, drops any cached `EntityShape`/connectivity for the
  file, and invalidates downstream Match JSON for every product the file
  belongs to.
- Allow the override to disagree with the DXF's source `INSUNITS`. The picker
  shows a soft "differs from file declaration" hint next to the control;
  it does not block confirmation.
- Persist the override on `files.user_unit_override` (nullable). `applied_scale`
  is still the single value downstream code reads — when an override is set,
  preprocess derives `applied_scale` from the override instead of from the
  detector. Setting the picker back to the detector's natural pick clears the
  override.
- Dashboard "rescaled" pill gains a `(user override)` suffix when
  `user_unit_override IS NOT NULL`.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `dxf-pipeline`: adds requirement for user-supplied unit override that
  takes precedence over `detect_scale_factor`, plus the recompute and
  cache-invalidation semantics that follow from changing `applied_scale`
  on an existing file.
- `viewer-ui`: adds requirement for the unit picker control and its
  confirm-modal flow; extends the dashboard "rescaled" pill to surface
  whether the active scale was set by the user.

## Impact

- DB: new column `files.user_unit_override TEXT NULL` (one of `mm | cm | m | inch | μm`).
  Startup migration adds the column with default NULL — no data backfill needed.
- Code:
  - `app/dxf.py` — `flatten_for_render` and/or `_maybe_rescale` accept an
    explicit override and skip detector when one is supplied.
  - `app/files.py` — preprocess pipeline reads `user_unit_override`, passes
    it through, writes back the resulting `applied_scale`.
  - `app/main.py` — new endpoint `POST /api/files/{file_id}/unit-override`
    that records the override and kicks off recompute + match-JSON
    invalidation for affected products.
  - `app/jobs.py` — recompute is a background job (same pattern as
    rule-check / preprocess).
  - `app/static/` — new picker control on viewer first column, confirm
    modal, soft hint when override disagrees with `INSUNITS`, dashboard
    pill suffix.
- Downstream: every cached Match JSON for the file's products is dropped on
  override change. Operators must re-run matching for those products —
  the confirm modal must state this explicitly.
- No API breakage: existing endpoints unchanged. New endpoint is additive.

## Why

The external rule-checking team consumes the DRC handoff `manifest.json` to
run their own checks, but it carries **no unit context**. Each DXF's unit is
known internally — the operator's unit-picker choice (`user_unit_override`) and
the DXF's declared `$INSUNITS` header — yet neither reaches the manifest, so the
downstream team cannot tell what unit a file's coordinates / tolerances are in.

## What Changes

- Add two fields to **every manifest `file_entry`** (units are per-DXF, so they
  belong on each file, not at the top level):
  - `user_unit` — the unit currently in force for the operator. The unit-override
    if one is set; otherwise the **effective** unit derived from the applied
    auto-rescale factor (decision B); `null` only when no named unit applies
    (a rare heuristic rescale to a non-standard factor).
  - `original_unit` — the DXF's declared `$INSUNITS` mapped to a unit string;
    `null` when the header is unitless / foot / unsupported / missing.
- Allowed values for both fields: **`mm`, `m`, `inch`, `cm`, `um`, `km`**. Note
  `um` uses ASCII `u` — the internal vocabulary stores micrometre as `μm`
  (Unicode U+03BC), so a translation layer emits `um`. `km` has no internal
  unit (the picker offers none) and therefore only ever appears as
  `original_unit` (from `$INSUNITS = 7`).
- Bump `bundle_version` `1.2.0` → `1.3.0` (additive minor: new file_entry
  fields).
- Update the manifest JSON Schema (`drc-manifest.schema.json`) — add the two
  properties (nullable, enum-constrained) to `file_entry` and to its `required`
  list, and bump the version example.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `design-rule-checking`: the **External DRC handoff bundle format** requirement
  gains two `file_entry` fields (`user_unit`, `original_unit`) and a
  `bundle_version` bump to `1.3.0`.

## Impact

- **Code**: `app/drc_bundle.py` — extend `_file_entry` with two helpers
  (`_user_unit`, `_original_unit`) and bump `BUNDLE_VERSION`. The needed data
  (`insunits`, `applied_scale`, `user_unit_override`) is already on the
  `FileRecord` passed to `_file_entry`, so **no new plumbing**.
- **Schema**: `openspec/specs/design-rule-checking/drc-manifest.schema.json` —
  new nullable enum fields on `file_entry`, added to `required`, version example
  bumped.
- **Downstream**: the external rule-check team gains per-file unit context. This
  is additive; consumers that pin only the major version are unaffected.
- **Tests**: `tests/test_drc_bundle.py` — assert the new fields and their values
  across override / no-override / unitless / km-and-um insunits cases, and that
  the manifest still validates against the (updated) schema.

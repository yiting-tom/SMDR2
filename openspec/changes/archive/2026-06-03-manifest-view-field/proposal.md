## Why

The external rule-checking team needs to know **which views** a DXF sheet
contains (top / bottom / side) to route and interpret its matches. The
operator already declares this by painting side-region rectangles in the
viewer, but the bundle `manifest.json` does not surface it — the views are only
implicit in the Match JSON key prefixes.

## What Changes

- Add a `view` field to **every manifest `file_entry`**: an array of the views
  the DXF carries, e.g. `["top", "bottom", "side"]`, or `["top"]`, in the
  canonical order top → bottom → side.
- A view is "present" when its **side-region rectangle is set** on the file
  (`top_view_rect` / `bottom_view_rect` / `side_view_rect`) — the operator's
  declaration of which views the sheet contains. When none are set, `view` is
  the empty array `[]`.
- Values are `"top"` / `"bottom"` / `"side"` (without the `_view` suffix); they
  correspond to the Match JSON key prefixes `top_view` / `bottom_view` /
  `side_view`.
- Bump `bundle_version` `1.3.0` → `1.4.0` (additive minor: new file_entry field).
- Update the manifest JSON Schema — add `view` to `file_entry` (array of the
  three enum values, unique items) and to its `required` list, and bump the
  version example.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `design-rule-checking`: the **External DRC handoff bundle format** requirement
  gains a `file_entry.view` array and a `bundle_version` bump to `1.4.0`.

## Impact

- **Code**: `app/drc_bundle.py` — add a `_views(rec)` helper and emit `view`
  from `_file_entry`; bump `BUNDLE_VERSION`. The side-region rects are already
  on the `FileRecord` passed to `_file_entry`, so **no new plumbing**.
- **Schema**: `openspec/specs/design-rule-checking/drc-manifest.schema.json` —
  new required `view` array on `file_entry`, version example bumped.
- **Downstream**: the external rule-check team gains the per-file view set.
  Additive; major version unchanged, so major-pinned consumers are unaffected.
- **Tests**: `tests/test_drc_bundle.py` — assert `view` reflects the set rects
  in canonical order (all three, a subset, and none), and that the manifest
  still validates against the updated schema.

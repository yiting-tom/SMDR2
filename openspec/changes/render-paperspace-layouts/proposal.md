## Why

Some DWGs organise their drawing as one view per AutoCAD **layout tab**
(paper space) rather than keeping geometry in model space. When such a
file is exported to DXF, its geometry lives in the paper-space layouts,
not modelspace. `flatten_for_render` walked only `doc.modelspace()`, so
these files came back with **zero primitives** — the viewer showed an
empty canvas and nothing could be matched. See [[project_smdr2_overview]]
and [[project_smdr2_pipeline]].

This is a real packaging-engineering workflow (the operator confirmed the
"一張一張 view" tab layout is the AutoCAD layout concept, distinct from
SMDR2's top/bottom/side product-view roles — those are untouched here).

## What Changes

- `flatten_for_render` gains a `layout_name` parameter and renders the
  resolved AutoCAD **tab** instead of hard-coding modelspace:
  - `layout_name=None` (default) **auto-resolves**: modelspace when it
    holds any entities — unchanged behaviour for normal files — otherwise
    the paper-space layout with the most entities.
  - A non-None value renders that specific tab; an unknown name degrades
    to auto-resolution (a stale persisted choice never breaks the
    pipeline).
- `RenderOutput` carries `source_layout` (the rendered tab name) and
  `source_is_paperspace`. The curve-flatten tolerance is now derived from
  the **rendered tab's** bbox diagonal (paper-space layouts use a direct
  entity-extents sweep; modelspace keeps the cheap `$EXTMIN/$EXTMAX`
  header shortcut).
- New `enumerate_layouts(dxf_path)` returns the tab inventory
  (`name`, `entity_count`, `is_paperspace`) for the discovery phase.
- **Layout (AutoCAD-tab) picker**: when a file's geometry is in model
  space, or in a single paper-space layout, nothing changes — it flows
  straight to layer discovery. Only when modelspace is empty **and more
  than one** paper-space layout has geometry does the discover worker
  render per-tab SVG thumbnails, write a layout manifest, and park the
  file in a new `awaiting_layout` lifecycle state for an operator pick.
- Picking a tab pins it on the file row (`chosen_layout`) and re-runs
  layer discovery against it, chaining straight into the existing layer
  picker. The pinned tab is threaded through every re-preprocess (library
  swap, unit override, reprocess-all) exactly like `user_unit_override`.
- Single-content-layout files auto-stamp `chosen_layout` after preprocess
  so the dashboard can show a "tab: <name>" badge.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `dxf-pipeline`: `flatten_for_render` renders a resolvable AutoCAD tab
  (modelspace or paper-space layout) rather than modelspace only; the
  flatten tolerance derives from the rendered tab's diagonal. A new
  `awaiting_layout` lifecycle state + layout-picker manifest gates files
  whose geometry spans multiple paper-space layouts.

## Impact

- `app/dxf.py`: `RenderOutput.source_layout` / `source_is_paperspace`;
  `_resolve_layout`, `enumerate_layouts`, `_enumerate_layouts_doc`,
  `_layout_diagonal`; `flatten_for_render(layout_name=...)`.
- `app/files.py`: `AWAITING_LAYOUT` status; `chosen_layout` column +
  migration + `FileRecord` field + `to_dict` + `set_chosen_layout`.
- `app/storage.py`: `layout_preview_dir` / `layout_manifest_path` /
  `layout_preview_svg_path` (under the layer-preview dir).
- `app/jobs.py`: `_discover_layers_worker(layout_name=)` + layout
  ambiguity gate + `_build_layout_picker`; `submit_discover_layers`,
  `submit_preprocess`, `_preprocess_worker`, `submit_unit_override_preprocess`,
  `submit_reprocess_all` all thread `layout_name`; `_persist_source_layout`;
  `awaiting_layout` in the reprocess skip-set.
- `app/main.py`: `GET/POST /api/files/{id}/layouts`,
  `GET /api/files/{id}/layout-preview/{safe}.svg`; `has_layout_options`
  in the product file payload; `source_layout` in the primitives response.
- `app/static/layout_modal.js` (new, single-select sibling of
  `layer_modal.js`); `app/templates/dashboard.html` (`#layout-modal`);
  `app/static/dashboard.js` (`awaiting_layout` status, "Pick view" /
  "View" actions, `promptLayoutSelection`, "tab:" badge).
- `tests/test_dxf_paperspace.py`, `tests/test_paperspace_layout_flow.py`
  (new); `tests/test_dxf_user_unit_override.py` (stub signature).
- `openspec/specs/dxf-pipeline/spec.md`: modify `Server-side DXF flatten`
  + `File lifecycle status`; add `AutoCAD layout (tab) selection`.

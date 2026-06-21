## 1. Core flatten — render a resolvable AutoCAD tab

- [x] 1.1 Add `source_layout` + `source_is_paperspace` to `RenderOutput` (`app/dxf.py`)
- [x] 1.2 Add `_resolve_layout(doc, layout_name)` (auto: modelspace if non-empty, else richest paper-space layout; unknown name → auto) and `_layout_name` helper
- [x] 1.3 Add `enumerate_layouts` / `_enumerate_layouts_doc` (tab inventory: name / entity_count / is_paperspace)
- [x] 1.4 Add `_layout_diagonal(doc, layout)` — header shortcut for modelspace, entity-extents sweep for paper-space layouts
- [x] 1.5 `flatten_for_render(layout_name=None)`: resolve tab, HATCH-strip the resolved tab, diagonal from the tab, `draw_layout(target)`, set the new fields

## 2. Persistence

- [x] 2.1 `chosen_layout TEXT` column in `FILES_SCHEMA` + idempotent ADD-COLUMN migration (`app/files.py`)
- [x] 2.2 `FileRecord.chosen_layout` + `to_dict` + tolerant `_get` read
- [x] 2.3 `FileStore.set_chosen_layout`
- [x] 2.4 `AWAITING_LAYOUT` status constant + `ALL_STATUSES`
- [x] 2.5 `layout_preview_dir` / `layout_manifest_path` / `layout_preview_svg_path` (`app/storage.py`)

## 3. Jobs orchestration

- [x] 3.1 `_discover_layers_worker(layout_name=None)`: flatten the tab; when `layout_name is None and source_is_paperspace`, enumerate and, if ≥2 content paper-space layouts, build the layout picker and return `needs_layout_pick`
- [x] 3.2 `_build_layout_picker` — per-tab SVG thumbnails + `layouts.json` in the `layouts/` subdir
- [x] 3.3 `_on_discover_done` → `awaiting_layout` when `needs_layout_pick`, else `awaiting_layers`
- [x] 3.4 Thread `layout_name` through `_preprocess_worker` (incl. transient-cache carry of `source_layout`) and `submit_preprocess` (resolve from row when None)
- [x] 3.5 `submit_discover_layers(layout_name=None)` resolves from row; `submit_unit_override_preprocess` / `submit_reprocess_all` pass the chosen tab
- [x] 3.6 `_persist_source_layout` stamps `chosen_layout` for paper-space sources (preprocess + reprocess-all callbacks); `awaiting_layout` added to reprocess skip-set

## 4. API routes

- [x] 4.1 `GET /api/files/{id}/layouts` (manifest + chosen_layout)
- [x] 4.2 `POST /api/files/{id}/layouts` (validate, pin `chosen_layout`, re-run discover with the tab)
- [x] 4.3 `GET /api/files/{id}/layout-preview/{safe}.svg`
- [x] 4.4 `has_layout_options` in the product file payload; `source_layout` in the primitives response

## 5. Frontend

- [x] 5.1 `app/static/layout_modal.js` — single-select picker (radios) reusing the layer-modal contract
- [x] 5.2 `#layout-modal` markup in `dashboard.html`
- [x] 5.3 `dashboard.js`: `awaiting_layout` status colour/label; "Pick view" CTA; "View" re-pick (gated on `has_layout_options`); exclude `awaiting_layout` from the Layers button; `promptLayoutSelection`; "tab:" badge

## 6. Tests

- [x] 6.1 `tests/test_dxf_paperspace.py` — auto-fallback, richest-wins, explicit tab, unknown→auto, modelspace precedence, enumerate, empty file, bundled-unchanged
- [x] 6.2 `tests/test_paperspace_layout_flow.py` — discover-worker gate (multi/single/explicit), `chosen_layout` round-trip + legacy migration, full API flow (multi → layout pick → layer pick → ready; single → no picker)
- [x] 6.3 Update `tests/test_dxf_user_unit_override.py` stub for the new `submit_preprocess` signature
- [x] 6.4 `uv run pytest -q` — full suite green (562 passed)

## 7. Adversarial-review fixes

- [x] 7.0a VIEWPORT root cause: `NON_RENDERED_DXFTYPES` + `_renderable_entity_count`; used in `_enumerate_layouts_doc`, `_resolve_layout` ranking, and the paper-space `_layout_diagonal` probe (a viewport-only framing tab no longer trips the picker, out-ranks a real tab, or coarsens the tolerance)
- [x] 7.0b `_build_layout_picker` flattens each candidate, drops zero-primitive tabs, commits the manifest only when ≥2 tabs actually render; worker falls through to layer discovery otherwise
- [x] 7.0c `confirm_layout` invalidates the saved Match JSON (unlink + `set_match_saved(False)`) when the tab actually changes
- [x] 7.0d `patch_file` + `post_unit_override` return 409 for `awaiting_layout` files (mirrors the reprocess-all skip)
- [x] 7.0e `layout_modal.js`: failed POST keeps the radio grid + restores the footer; in-flight guard blocks Esc/overlay close from resolving mid-confirm
- [x] 7.0f Regression tests: viewport-only no-pick (dxf + worker), viewport-heavy ranking, match invalidation on re-pick, 409 guards; full suite green (567 passed)

## 8. Spec sync

- [x] 8.1 `openspec validate render-paperspace-layouts --strict` passes
- [ ] 8.2 At archive time, merge the modified `Server-side DXF flatten` and `File lifecycle status` requirements, and the new `AutoCAD layout (tab) selection` requirement, into `openspec/specs/dxf-pipeline/spec.md`

## 1. Schema migration

- [x] 1.1 Add `dxf_view TEXT` column to `files` table in `FILES_SCHEMA` and in the in-place migration block inside `FileStore.__init__`; backfill `'multi'` for rows where `product_id IS NOT NULL AND dxf_role IS NOT NULL AND dxf_view IS NULL`.
- [x] 1.2 Drop the old `idx_files_product_role` index; create `idx_files_product_role_view` UNIQUE on `(product_id, dxf_role, dxf_view) WHERE product_id IS NOT NULL AND dxf_role IS NOT NULL AND dxf_view IS NOT NULL`. Verify idempotency by running migration twice in a test.
- [x] 1.3 Extend `FileRecord` dataclass with `dxf_view: str | None = None`, update `to_dict()` to surface it, and wire it through `register()` / `_row_to_record()` / any column-list INSERT in `app/files.py`.

## 2. View resolution module

- [x] 2.1 Create `app/product_views.py` with a `ViewSource` dataclass `(file_id: str, source: Literal['region','whole_file'], rect: dict | None)` and a pure function `resolve_views(file_rows: list[FileRecord]) -> dict[str, ViewSource]` keyed by view name (`'top' | 'bottom' | 'side'`). Conflicts SHALL raise a dedicated `ViewCoverageConflict` exception carrying the conflicting file ids and view name.
- [x] 2.2 Add a thin wrapper `resolve_for_product(product_id, dxf_role)` in `app/product_views.py` that loads rows via `FILE_STORE.list_by_product` filtered by role and invokes the pure resolver.
- [x] 2.3 Unit tests for `resolve_views` covering: pure multi with all three rects; multi + single-view split; missing-view-only-on-one-side; conflict (multi top region + split top file); empty input.

## 3. Upload API changes

- [x] 3.1 In `app/main.py upload_product_file`, accept an optional `dxf_view: str = Form('multi')` argument and validate against `{'multi','top','bottom','side'}` (HTTP 400 on bad value).
- [x] 3.2 Replace the slot-clear logic (`UPDATE files SET product_id=NULL WHERE …`) with a query scoped to the `(product_id, dxf_role, dxf_view)` triple, so split-view uploads do not evict sibling rows.
- [x] 3.3 After the new row would land, run `resolve_for_product` against the projected post-write state; on `ViewCoverageConflict` return HTTP 400 with `{detail: {error: 'view_coverage_conflict', view, file_ids: [...]}}`. Roll back the write attempt before returning.
- [x] 3.4 When `dxf_view != 'multi'`, reject upload if the file rect columns from preprocess come back non-null (defer this check to side-region update if rects only get set later — at upload they're always null, so this is an invariant guard, not new logic).

## 4. Side-region update guardrail

- [x] 4.1 In the side-region update endpoint (look up via `update_side_regions` callers in `app/main.py`), refuse the write with HTTP 400 if the target row has `dxf_view != 'multi'`.
- [x] 4.2 Run `resolve_for_product` against the projected post-update state for `multi` rows; on conflict (a new region overlaps an existing split file) return HTTP 400 with the same payload shape as upload conflicts.

## 5. Downstream consumers

- [x] 5.1 In `POST /api/files/{file_id}/match-json` (`save_match_json` in `app/main.py`): when `rec.dxf_view ∈ {top, bottom, side}`, skip `split_matches_by_side` and force every match key to be prefixed with that view (`<view>.<class>.<idx>`). When `rec.dxf_view == 'multi'` (or NULL legacy), keep current behavior.
- [x] 5.2 In `POST /api/products/{product_id}/rule-check`: replace the "one file per role" assumption with a merge step. Group files by `dxf_role`; for each role, union the per-file `match_json` dicts and the per-file `entity_shapes`. Namespace every handle as `<short>:<handle>` (first 8 chars of `file_id`) in both the merged map keys and the values inside `match_json`'s match groups. Result is one `dxfs_by_role[role] = {match_json, entity_shapes, file_ids: [...], dxf_paths: [...]}` shaped identically to today for rule code; `file_id` / `dxf_path` singular fields stay populated when only one file contributes (back-compat).
- [x] 5.3 ~~Update template library save/load…~~ **NOT APPLICABLE.** Verified that `app/library.py` templates store raw point geometry only; there are no file/product references to migrate. Templates are file-agnostic by design and remain unaffected by the multi-DXF change.

## 6. Frontend

- [x] 6.1 In `app/static/dashboard.js`, extend the product-row renderer so a role slot shows either (a) a single `multi` file (current presentation) or (b) a stack of up to four rows (one `multi` + per-view split), each tagged with its `dxf_view`.
- [x] 6.2 Add a "split file" affordance per role slot: a small dropdown / menu next to the slot offering "Upload as top", "Upload as bottom", "Upload as side". Selecting one targets a file picker whose upload sets `dxf_view` accordingly.
- [x] 6.3 Plumb the new HTTP 400 payload (`error: 'view_coverage_conflict'`) into the existing status-bar surface so users see a meaningful message instead of "upload failed: 400".
- [x] 6.4 Add a delete control to remove a single split-view file from a role slot (calls a new `DELETE /api/products/{pid}/files/{file_id}` route).

## 7. New DELETE route

- [x] 7.1 Implement `DELETE /api/products/{product_id}/files/{file_id}` in `app/main.py`: verify the file belongs to the product, free the slot, and respond 204. (Resolves Open Question Q1 in the design.)
- [x] 7.2 Tests: deleting a split file leaves siblings intact; deleting the only `multi` file empties the role; deleting a foreign file returns 404.

## 8. Tests

- [x] 8.1 Schema migration test: spin up `FileStore` against a DB with a legacy row, assert `dxf_view = 'multi'` after init and that the old `idx_files_product_role` index is gone.
- [x] 8.2 API test: upload `multi` then `top` to the same `(product, SBT)`; assert both rows exist; assert `GET /api/products/{pid}` groups them under role with view tags.
- [x] 8.3 API test: upload `multi` with a `top_view_rect` set (via side-region update), then attempt to upload a `top` split file; assert HTTP 400 with `error='view_coverage_conflict'`.
- [x] 8.4 API test: existing single-file-per-role test (`test_upload_to_product_rejects_non_dxf` and its neighbors) continues to pass without modification.
- [x] 8.5 Resolver integration test: build a product with mixed sources, call `resolve_for_product`, assert the returned mapping shape and content; corroborate that preprocess and a representative match call consume it correctly.

## 9. Docs and memory updates

- [x] 9.1 ~~Update developer-facing notes…~~ **NOT APPLICABLE.** No project-level `README.md` / `CLAUDE.md` exists at the repo root; the openspec change artifacts (proposal, design, specs, tasks) are the documentation of record.
- [ ] 9.2 If the change archives cleanly, run `/opsx:archive multi-dxf-per-role`. *(left for user after manual UI verification.)*

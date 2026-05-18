## Context

Today, a product owns at most one DXF per role. The constraint lives in `app/files.py` as the partial unique index `idx_files_product_role` on `(product_id, dxf_role)` (currently lines ~239–243), and in `app/main.py`'s `upload_product_file` (around line 262), which clears whatever sits in `(product_id, dxf_role)` before writing the new file.

Inside a DXF, views are already a first-class concept. Each file row carries `top_view_rect`, `bottom_view_rect`, `side_view_rect` JSON columns; the viewer's "Mark sides" mode (`viewer-ui` spec) drives users to mark up to three rectangles, and downstream filters use the rectangles to slice the file's primitives into per-view geometry.

The new requirement is to let a `(product, role)` pair be sourced from one **or many** DXFs while keeping the existing single-file flow untouched. The goal is additive: code that today asks "the file for this role" should keep working when the role is still served by one `multi` file; only the seams that already understand "per-view geometry" need to learn about cross-file resolution.

## Goals / Non-Goals

**Goals**
- Schema and API support for multiple files per `(product, role)`, distinguished by a `dxf_view ∈ {multi, top, bottom, side}` tag.
- Backwards-compatible storage: existing rows migrate to `dxf_view = 'multi'` with no behavior change.
- A single canonical view-resolution layer that downstream pipelines (preprocess, match, rule check, library save) call instead of probing `files` directly.
- Validation that no `view` is sourced from two places at once (single-view file overlapping a `multi` file's region), surfaced as an upload-time error.
- Upload UX preserves the current "drop file onto slot" shortcut as the default; the split-file path is an explicit secondary action.

**Non-Goals**
- Changing how `top_view_rect` / `bottom_view_rect` / `side_view_rect` are produced or consumed inside a `multi` DXF. The viewer's mark-sides flow, region semantics, and per-view geometry slicing stay as they are.
- Expanding `VALID_ROLES` beyond `("SBT", "BD", "POD", "RING")`.
- Per-view sub-roles (e.g., `SBT-TOP` as a distinct role). The view dimension is orthogonal to `dxf_role`.
- Cross-product or cross-library deduping of split files. A `(product, role, view)` slot is the unit of identity here.

## Decisions

### Decision 1 — `dxf_view` as an enum column, not separate roles
- **Choice**: Add `dxf_view TEXT` to the `files` table; values `{'multi', 'top', 'bottom', 'side'}`. Replace `idx_files_product_role` with `idx_files_product_role_view` on `(product_id, dxf_role, dxf_view)`.
- **Why**: The view dimension is orthogonal to the design role. Encoding it as a column keeps `VALID_ROLES` (4) and `dxf_view` (4) independent; rules, library code, and UI labels that already pivot on role don't need to learn a combinatorial role list. The alternative — exploding roles into `SBT-TOP`, `SBT-BOTTOM`, etc. — would force every consumer (templates, rule names, library taxonomy) to track the cross-product.
- **Alternatives considered**:
  - *Subrole enum*: rejected for the reason above.
  - *Junction table* `product_role_views`: rejected because it duplicates `files.product_id` / `files.dxf_role` semantics; the file row already is the natural carrier, and we still need a per-file flag.

### Decision 2 — `multi` means "view derived from in-DXF region rects"
- **Choice**: A row with `dxf_view = 'multi'` SHALL source its per-view geometry from the existing `top_view_rect` / `bottom_view_rect` / `side_view_rect` columns (any subset may be present; a `multi` file with no rects exposes nothing per-view). A row with `dxf_view ∈ {top, bottom, side}` SHALL ignore those columns and expose its whole geometry as that view; the rect columns SHALL be required to be `NULL` for single-view rows (server-side check, not a SQL constraint).
- **Why**: Reuses the existing view-rect mechanism verbatim, so the viewer's mark-sides flow doesn't change for the common case. Single-view files are conceptually "the whole file is that view," and forcing rect columns to `NULL` removes a class of ambiguity (what if a `dxf_view='top'` file also has a `bottom_view_rect`?).
- **Alternative considered**: Treat single-view rows as `multi` with a "full-file" rect. Rejected because it would re-introduce the ambiguity (region rect implicitly tied to file bbox), and require the viewer to fabricate a synthetic rect.

### Decision 3 — View resolution as a pure lookup, validated on write
- **Choice**: A new module `app/product_views.py` exposes `resolve_views(product_id, dxf_role) -> dict[str, ViewSource]` where `ViewSource` is `{ file_id, source: 'region'|'whole_file', rect: dict | None }`. The lookup walks all `files` rows for the `(product_id, dxf_role)` pair and builds the mapping. Conflicts (two sources for the same view) raise; the upload endpoint catches the raise and returns HTTP 400. Read-time callers SHALL NOT see conflicts because writes are validated.
- **Why**: A single function with one shape lets every downstream consumer call the same code. Validating at upload time means the database invariant ("no view covered twice") is enforced at the natural boundary (the API), without needing a complex SQL trigger.
- **Alternatives considered**:
  - *Compute resolution lazily at every read*: rejected — every match / rule / preprocess pass would re-validate, and a conflict surfaced mid-pipeline would be hard to recover from.
  - *Database trigger / CHECK constraint*: rejected — the "no overlap with `multi` regions" rule depends on JSON column content, which SQLite CHECK constraints can't express cleanly.

### Decision 4 — Upload UX keeps the drop-to-slot default
- **Choice**: `POST /api/products/{product_id}/files` SHALL accept an optional `dxf_view` form field; omitted defaults to `multi`. The current drag-and-drop flow in `app/static/dashboard.js` (and the role buttons) stays single-target = `multi`. A new "split file" affordance per role (e.g., a small menu on the role slot) SHALL let users upload single-view files. Replacement semantics scope to `(role, view)`: uploading a new `multi` file overwrites only the existing `multi` row; uploading a `top` file overwrites only the existing `top` row.
- **Why**: The majority of products will continue to live in the single-file world. Forcing a view picker on every upload would slow down the common path. Scoping replacement to `(role, view)` matches the new unique index and means users can iterate on one view file without losing the others.

### Decision 5 — Reject conflicts at upload, not at pipeline time
- **Choice**: When `POST /api/products/{product_id}/files` is invoked with `dxf_view` set, the handler SHALL run `resolve_views` against the *proposed post-write state* and reject with HTTP 400 if the new file would create overlap (e.g., uploading a `top` file when a `multi` file's `top_view_rect` is non-null). The error response SHALL identify the conflicting file id and view.
- **Why**: The user is right there with the upload dialog open; they can correct it (delete the multi file's region, or skip the split file). Catching it later means a half-broken product gets stored and pipelines fail in confusing ways.
- **Trade-off**: A user updating a `multi` file to add a `top` region *after* having already uploaded a split `top` file will get a conflict on re-marking sides, not on upload. We accept that — the side-marking flow runs on a file that is already in the DB, and the same `resolve_views` check applies there.

### Decision 7 — Match JSON shape is preserved; merging happens at rule-check time

- **Constraint from user**: the Match JSON format that rule code consumes SHALL NOT change. Rule writers must not need to learn new keys, types, or handle formats.
- **Choice**:
  - Per-file Match JSON (saved by `POST /api/files/{file_id}/match-json` to `data/match/{file_id}.json`) keeps its current shape `{ "<view>.<class_snake>.<template_index>": [[handle, ...], ...] }`. For a `multi` file, the view prefix comes from the matched instance's region (existing logic in `split_matches_by_side`). For a single-view file (`dxf_view ∈ {top, bottom, side}`), every key SHALL be prefixed with that view name — region splitting is skipped because the file IS that view.
  - `POST /api/products/{product_id}/rule-check` constructs `dxfs_by_role[role]` by merging across every file that contributes to that role (resolved via `resolve_for_product`). The merge SHALL union the per-file `match_json` dicts (collision-free because view-coverage uniqueness is enforced upstream) and union the `entity_shapes` dicts.
  - To prevent handle collisions across files within the same role, handles in the merged `entity_shapes` and `match_json` SHALL be namespaced as `<short_file_id>:<handle>` (the short form is the first 8 chars of the content hash already used as `file_id`). Rule code treats handles as opaque strings, so this is invisible to rule writers.
- **Why**: Rule code stays bit-identical. The viewer's rule-result renderer is the only place that needs to learn the `<short>:<handle>` format (to know which file's primitive set to highlight); that's a single change and not in the rule-writing contract.
- **Trade-off**: Single-file roles still go through a (trivial) merge path, which adds a tiny constant cost. Acceptable.

### Decision 6 — Library / template references key on `(role, view)`
- **Choice**: Where the template library code today persists a reference to a file (e.g., as part of a saved template's source pointer), it SHALL persist `(product_id, dxf_role, view)` instead of `file_id`. At load time the library consults `resolve_views` to get the current file backing that `(role, view)` slot.
- **Why**: Templates outlive individual file uploads; storing the file id today already breaks when the user re-uploads to the same slot. Switching to `(role, view)` makes templates stable across the multi ↔ split transition.
- **Migration**: Existing template rows with a stored `file_id` are looked up once on read; the `(role, view)` is recovered from the file row, and the template is rewritten in-place. If the file row is gone, the template is flagged broken (existing behavior).

## Risks / Trade-offs

- **Risk**: Existing tests that assume `INSERT OR REPLACE`-style slot semantics (upload to role → previous file vacates the slot) may break when the previous file was `multi` and the new upload is `top` (no replacement should happen). → **Mitigation**: keep the legacy "single-slot replace" path for `dxf_view='multi'` uploads (the default) so the existing test fixture continues to pass; only scope-`(role, view)` semantics apply when an explicit `dxf_view` is provided.
- **Risk**: The viewer's mark-sides flow could write a `top_view_rect` into a `multi` file that already has a sibling `dxf_view='top'` row, retroactively creating overlap. → **Mitigation**: reuse `resolve_views` in the side-region update endpoint; reject the write with the same 400 contract.
- **Risk**: Frontend complexity grows — the role-slot widget must show "1 multi file" or "N split files" with view tags, and the upload menu must offer the right defaults. → **Mitigation**: keep the visual hierarchy role-first, view-second; collapse to current single-row display when only one `multi` row exists.
- **Trade-off**: Two ways to express the same view (a `multi` region vs. a split file). We chose flexibility over orthogonality. The validation layer keeps this from causing semantic drift, but reviewers reading a product's files will need to understand both forms.

## Migration Plan

1. **Schema migration (idempotent, in `FileStore.__init__`)**
   - Add `dxf_view TEXT` column if missing; backfill `'multi'` for any row with non-null `product_id` and `dxf_role`.
   - Drop the old `idx_files_product_role` index; create `idx_files_product_role_view` on `(product_id, dxf_role, dxf_view)`.
   - The migration is safe to run repeatedly; the `IF NOT EXISTS` / column-presence checks already used in `FileStore.__init__` (lines ~194–243) are the pattern to extend.
2. **Code rollout order**
   - Land schema migration and `resolve_views` first; no callers, no behavior change.
   - Switch one downstream consumer at a time (preprocess → match → rule check → library) to `resolve_views`, behind tests.
   - Add the API `dxf_view` form field; default `multi` keeps existing clients working.
   - Add the frontend split-file affordance last.
3. **Rollback**
   - If a regression appears, the `dxf_view` column and new index can stay (they're backwards compatible). Reverting the API field default to "always `multi`" is enough to restore old behavior. Existing rows are unaffected.

### Decision 8 — Drop the `dxf_view` enum at the API surface (simplification pivot)

- **Context**: After landing Decisions 1–7, user feedback was that the dominant workflow is "one DXF per role containing top + bottom + side as in-DXF regions"; the multi-DXF case is uncommon and **not necessarily aligned with the top/bottom/side axis** (a split might be along an entirely different dimension). And the user marks side-region rects on every file regardless.
- **Choice**:
  - Stop exposing `dxf_view` as an upload parameter. Every product file is registered as `dxf_view = 'multi'`. The column stays in the schema (vestigial) but carries no surface meaning.
  - Stop enforcing slot uniqueness at the DB layer. The unique index `idx_files_product_role_view` is replaced by a non-unique `idx_files_product_role` (for query speed only). A `(product, role)` accepts an arbitrary number of files.
  - Stop branching on `dxf_view` in `save_match_json`; every file uses the rect-driven `split_matches_by_side` path.
  - Stop rejecting region marks on "split" files in the side-regions endpoint — there are no split files in the new model.
  - Drop cross-file coverage uniqueness checking entirely. Each DXF marks its own region rects independently; if two sibling DXFs both mark a `top_view_rect`, that is valid input (the rule-check merge will concatenate matches from both into the role-level `top_view.*` keys). `resolve_views` and `ViewCoverageConflict` remain in `app/product_views.py` as a utility for any future caller that genuinely needs a single source per view, but no code path calls them on the write path. (Earlier iteration drafts of this design enforced uniqueness at `PATCH /api/files/{file_id}/side-regions` time, which silently broke the second file's region save when a sibling already covered the same view.)
  - Keep the `DELETE /api/products/{pid}/files/{fid}` endpoint and the `replace_file_id` form field on upload. The frontend uses `replace_file_id` to scope eviction to one file (the "Replace" button path); plain uploads are additive.
  - Frontend collapses to the original single-file UX when a role has exactly one DXF; only when ≥ 2 files are present does it stack rows and surface "+ Add file" / "✕".
- **Why**: The split-as-{top|bottom|side} enum was an over-fit. The view dimension lives inside the DXF (via region rects); the file dimension is just "how many DXFs". Keeping these orthogonal makes the data model match the user's mental model and the UI clean.
- **Trade-off**: We lose the ability to optimise away region scanning for a "pure top" file. In practice that file still needs region marking under the simplified model anyway, so no work is saved by special-casing it.

## Open Questions

- **Q1**: Should the API surface a `DELETE /api/products/{product_id}/files/{file_id}` endpoint as part of this change? Today, slot replacement is implicit. With split files, users will want to remove one view file without uploading a replacement. *Tentative: yes, scope into this change.*
- **Q2**: When a `multi` file is uploaded that *replaces* a previous `multi` file (same `(product, role, 'multi')` slot), and the previous file had `top_view_rect` set but the new file has none, do we drop the rect or carry it forward? *Tentative: drop — the rects are tied to the file's coordinate frame; carrying them across files would mis-position.*
- **Q3**: Library template references currently key on `file_id`. The migration plan above rewrites them on read. Is there an existing template-library version field we can bump to gate the rewrite, or do we do it lazily forever? *Need to check `app/library.py`.*

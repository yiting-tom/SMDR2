## Why

A product currently allows exactly one DXF per role (SBT / BD / POD / RING), enforced by a `(product_id, dxf_role)` unique index. In practice, a single role's design can be authored as multiple DXFs (e.g., SBT has `top.dxf` + `bottom.dxf`), or as a partial split (top+bottom in one DXF, side as a separate DXF). The 1:1 assumption blocks these inputs and forces error-prone manual merging.

## What Changes

- Add a `dxf_view` dimension at the file level. Each product file SHALL carry `dxf_view ∈ {multi, top, bottom, side}`:
  - `multi` — the file mixes views; per-view geometry comes from in-DXF region labels (`top_view` / `bottom_view` / `side_view`). This is the existing behavior.
  - `top` / `bottom` / `side` — the entire file represents that single view; region parsing is skipped.
- **BREAKING (storage)**: replace the `(product_id, dxf_role)` unique index with `(product_id, dxf_role, dxf_view)`. Existing rows backfill to `dxf_view = 'multi'`.
- A `(product, role)` SHALL allow multiple files as long as each `view` (top / bottom / side) is covered by at most one source — either a region of a `multi` file or a single-view file. Overlapping coverage SHALL be a validation error; missing coverage of a view SHALL be allowed.
- Upload UX SHALL keep the current drop-to-slot default (`dxf_view = multi`); a secondary control SHALL let the user upload single-view files into a role.
- Introduce a view-resolution layer: `resolve_views(product_id, dxf_role) → {view: (file_id, region_handle_if_multi)}` that downstream pipeline / match / rule stages SHALL use instead of looking up a single file per role.

## Capabilities

### New Capabilities
- `product-files`: Owns the product-scoped DXF file model — slot uniqueness rules across `(product_id, dxf_role, dxf_view)`, view-coverage validation, upload routes and UX for multi-DXF roles, and the view-resolution lookup that downstream pipelines consume.

### Modified Capabilities
<!-- None: existing specs (viewer-ui, dxf-pipeline, pattern-matching, template-library, design-rule-checking) do not currently constrain the product/role/view file model, so this change is purely additive at the spec layer. Downstream pipeline code is affected, but only at the seam where it requests "the file for this role", which the new capability covers. -->

## Impact

- **Schema** (`app/files.py`): new `dxf_view TEXT` column on `files`; unique index migrates from `(product_id, dxf_role)` to `(product_id, dxf_role, dxf_view)`. Existing rows backfill to `'multi'`.
- **API** (`app/main.py`):
  - `POST /api/products/{product_id}/files` accepts an optional `dxf_view` form field (defaults to `multi`); slot-replacement semantics now scope to `(role, view)` rather than `(role)`.
  - `GET /api/products/{product_id}` returns the file list per role grouped by view.
  - New `GET /api/products/{product_id}/views` (or equivalent) surfaces the resolved `(role, view) → source` mapping for pipeline consumers.
- **Frontend** (`app/static/dashboard.js`, viewer): role slot rendering and upload widget extended to show / target a specific view; default path unchanged.
- **Pipeline seams** (`app/preprocess.py`, matcher entry points, rule checker): swap "fetch file for role" calls for the new view-resolution layer.
- **Library save/load** (`app/library.py`): templates currently tied to a single source file SHALL key on `(role, view)` so they remain valid when the underlying file layout changes between multi / split.
- **Tests**: existing single-file-per-role tests SHALL pass unchanged (legacy `multi` path); new tests cover split uploads, mixed multi+single coverage, and overlap validation errors.

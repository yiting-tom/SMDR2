## Why

Developers iterating on SMDR2 typically upload several test DXFs in
a row. Each upload today must go through **Phase 1** — the layer
discovery worker (`_discover_layers_worker`) parses the file,
renders one SVG thumbnail per layer, writes a `layers.json`
manifest, and stops the file at `awaiting_layers` until the
operator opens the layer picker, ticks the layers they want, and
clicks confirm. For dev workflows that don't care about the layer
subset (the whole file is fine), this is pure click overhead — at
N files, it's N picker round-trips, N rendering passes, and N
operator interruptions.

The fix is a single dev-mode-gated checkbox at the upload zone
that tells the backend to **skip Phase 1 entirely** and submit
Phase 2 (`_preprocess_worker`) with `selected_layers=None` (the
existing "keep every layer" signal). The file's lifecycle goes
straight from `preprocessing` → `ready_to_match`, with
`discovering_layers` / `awaiting_layers` never entered.

## What Changes

- `POST /api/products/{product_id}/files` SHALL accept an optional
  form field `skip_layer_pick: bool` (default `false`). When
  `true`, the handler SHALL submit `_preprocess_worker` directly
  with `selected_layers=None` and SHALL register the new file row
  with `initial_status=PREPROCESSING` (skipping
  `DISCOVERING_LAYERS`).
- The file SHALL never enter `discovering_layers` or
  `awaiting_layers` on this code path; layer-manifest JSON and
  per-layer SVG thumbnails SHALL NOT be written for files
  uploaded with `skip_layer_pick=true`.
- The dedup branch (re-upload of bytes-identical content) SHALL
  also honour the flag: if the existing row is in
  `awaiting_layers` and the operator re-uploads with
  `skip_layer_pick=true`, the row SHALL be rebound and Phase 2
  SHALL be submitted with `selected_layers=None` directly. The
  `selected_layers` column SHALL be reset to `NULL` so the
  Phase 2 worker sees "no filter".
- The dashboard upload zone SHALL render a checkbox **only when
  `getDevMode()` returns true**, labelled
  `Skip layer picker (dev: use all layers)`. The checkbox state
  SHALL persist in `localStorage` under a new key
  (e.g. `smdr2.dashboard.skipLayerPick`), mirroring the existing
  dev-preference pattern.
- The upload submission SHALL append the form field
  `skip_layer_pick=true` only when the checkbox is checked AND
  dev mode is on. When either is false the field SHALL NOT be
  sent (server defaults to `false`).
- **No server-side enforcement of dev mode.** Consistent with the
  existing `dev-overrides` surface, dev mode is a UX hint not a
  security boundary; any client may send the flag. The
  rationale is documented in the design.

## Capabilities

### New Capabilities
<!-- None — pure additive variant on existing endpoints / UI. -->

### Modified Capabilities
- `dxf-pipeline`: MODIFY the `Multi-file upload with
  deterministic file IDs` requirement to document the optional
  `skip_layer_pick` form field, and MODIFY the
  `File lifecycle status` requirement so its scenarios cover the
  dev-direct-to-preprocess path (no `discovering_layers` /
  `awaiting_layers` transition).
- `viewer-ui`: ADD a requirement for the dev-mode-only
  `Skip layer picker` checkbox on the dashboard's upload zone
  (visibility, persistence, behaviour when ticked).

## Impact

- **Code**:
  - `app/main.py::upload_product_file` (line 379) — add the
    optional form field, branch on it for both the new-file and
    deduped-rebind branches, route to `submit_preprocess` instead
    of `submit_discover_layers`. The response's `status` field
    becomes `preprocessing` (not `discovering_layers`) on the
    skip path.
  - `app/static/dashboard.js` — render the dev-only checkbox in
    the upload zone, persist its state in `localStorage`, append
    the form field on upload when checked.
- **APIs**: backward-compatible — the new field is optional and
  defaults to false. Old clients that never send it behave
  exactly as today.
- **Persistence / migration**: no DB schema change. The
  `selected_layers` column already defaults to `NULL` for
  "no filter".
- **Tests**: add `tests/test_upload_skip_layer_pick.py` (or
  extend `tests/test_layer_preview.py`) covering: (a) flag absent
  → existing Phase 1 path unchanged; (b) flag true → no Phase 1
  job submitted, `_preprocess_worker` submitted with
  `selected_layers=None`, initial status `preprocessing`, no
  `layers.json` / per-layer SVG written; (c) dedup + flag-true
  rebinds the existing row and submits Phase 2 even if the row
  was previously stuck in `awaiting_layers`.
- **Backward compatibility**: every existing flow (non-dev,
  production, no checkbox checked) is identical. The dev shortcut
  is purely additive.
- **Operator-visible**: dev users see a new checkbox; everyone
  else sees nothing change.

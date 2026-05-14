## Why

A real package-engineering DXF often layers BD outline, copper, silkscreen,
SMD pads, BGA balls, drill grid, dimensions, vendor stamps, and stray
import-junk on top of each other. The packaging engineer typically only
cares about a handful of layers (e.g., SMD pads + BD outline). Today every
primitive in the file is parsed, indexed for matching, and rendered — even
layers the user knows are noise. That bloats `parsed/{file_id}.json`,
slows the scan-all pre-match, fills the viewer with visual chatter, and
forces rule-check to scan irrelevant geometry.

Letting the user pick the relevant layers **before** the heavy
preprocessing makes the rest of the pipeline (render / match / rule check)
faster and cleaner, and matches how the engineer already mentally filters
the file in AutoCAD ([[feedback_autocad_ux]]).

## What Changes

- Split preprocessing into **two phases**:
  - **Phase 1 (layer-discovery, cheap):** parse the DXF, enumerate every
    DXF layer, and render a small per-layer SVG thumbnail. File status
    transitions to a new `awaiting_layers` state instead of jumping
    straight to `ready_to_match`.
  - **Phase 2 (full-preprocess, today's pipeline):** runs only after the
    user confirms which layers to include. Flattens primitives **filtered
    to the chosen layers**, builds the shape index, and runs scan-all
    pre-match.
- Add a **layer-selection modal** on the dashboard: per-product, per-DXF, it
  pops up automatically once Phase 1 finishes, showing each layer's
  thumbnail, name, primitive count, and a checkbox. Default selection is
  "all layers". User confirms with "Use selected"; this kicks off Phase 2.
- Persist the user's per-file layer selection so re-preprocessing
  (e.g., library swap, role re-upload) skips the modal and reuses the
  prior choice unless the user explicitly clicks "Edit layers".
- Drop primitives whose `layer` is not in the selected set everywhere
  downstream: `parsed/{file_id}.json`, `prematch/{file_id}.json`, viewer
  render, match-JSON export, rule check.
- **BREAKING (internal data):** `parsed/{file_id}.json` schema gains a
  `selected_layers: [...]` header; existing parsed caches are invalidated
  and re-generated on next access.

## Capabilities

### New Capabilities
(none — this extends existing capabilities)

### Modified Capabilities
- `dxf-pipeline`: add a two-phase preprocess (layer discovery → full
  preprocess), persist the per-file layer selection, and require every
  downstream artifact (parsed, prematch, match-JSON) to honor the
  selection. Add new lifecycle state `awaiting_layers`.
- `viewer-ui`: add the layer-selection modal that auto-opens on
  `awaiting_layers`, plus an "Edit layers" affordance to re-open it for
  ready files. Dashboard rows reflect the new status.

## Impact

- **Backend (`app/dxf.py`, `app/jobs.py`, `app/files.py`,
  `app/storage.py`, `app/main.py`)**: new layer-discovery worker, new
  status, new `data/layer_preview/{file_id}/` directory with per-layer
  SVG thumbnails + a `layers.json` manifest, new
  `selected_layers` column on `files`, layer filter threaded through the
  existing full-preprocess worker.
- **Frontend (`app/templates/dashboard.html`, `app/static/dashboard.js`,
  `app/static/style.css`)**: new modal markup + script, polling logic
  that detects the new status, API calls to confirm selection.
- **Frontend (`app/templates/viewer.html`, `app/static/canvas.js`)**: an
  "Edit layers" header button that re-opens the modal and re-runs Phase 2
  on confirm.
- **No change to matching/rule-check algorithms** — they keep operating
  over whatever primitives reach them; the filter happens upstream.
- **Storage:** per-file SVG thumbnails are tiny (≤ a few KB each) so disk
  cost is negligible vs. the existing `parsed/` JSON.

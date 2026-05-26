## Why

A semiconductor package DXF often lays out the **frontside** and
**bottomside** copper/SMD/BGA artwork side-by-side in the same drawing
sheet. Today the matcher and rule-check treat the whole sheet as one
flat namespace — every SMD instance is `smd.0`, regardless of which
half of the package it belongs to. Downstream consumers (rule reports,
BOM exports, customer hand-off) can't tell a frontside ball from a
bottomside ball without re-inspecting coordinates by eye.

The engineer already knows where each half lives the moment they open
the file; they just need a quick way to **mark the two regions** and
have the rest of the pipeline carry that label forward. Adding a
side-region tag to match-JSON keys (`smd.0` → `frontside.smd.0`)
matches how packaging engineers already namespace nets and pads in
EDA tools and unblocks per-side rule checks.

## What Changes

- Add a **mark side regions** mode to the viewer ([[feedback_autocad_ux]]):
  toggled via a header button + hotkey, the user drags two rectangles
  on the canvas — first one tagged `frontside`, second one tagged
  `bottomside`. Both rectangles are world-space axis-aligned.
- Persist each file's two rectangles to the `files` row (per-file, not
  per-product) so swapping libraries or re-preprocessing keeps the
  side assignment ([[project_smdr2_pipeline]]).
- When **exporting match JSON** (and the cached `prematch/{file_id}.json`),
  compute each match instance's bbox **center**; if it lies inside the
  frontside rect, emit the instance under key `frontside.<class>.<i>`;
  if inside bottomside, emit under `bottomside.<class>.<i>`; if neither
  (or no regions set), keep the original `<class>.<i>` key. Splitting
  is per-instance, so the same template can contribute to both
  `frontside.smd.0` and `bottomside.smd.0` in one file.
- Render the two rectangles as a faint persistent overlay on the
  canvas so the user can see what's currently tagged; the overlay is
  read-only outside mark-mode.
- The rule-check report ([[project_smdr2_pipeline]]) consumes the
  side-prefixed keys as-is — no rule-side changes needed beyond
  surfacing the new names; existing rules that pattern-match on
  `<class>.<i>` continue to work because the suffix is unchanged.

## Capabilities

### New Capabilities
(none — this extends existing capabilities)

### Modified Capabilities
- `viewer-ui`: add a "mark side regions" mode (button + hotkey) that
  lets the user drag two world-space rectangles tagged frontside /
  bottomside, with a persistent on-canvas overlay and a way to clear /
  redraw a region.
- `dxf-pipeline`: persist per-file `frontside_rect` and
  `bottomside_rect`; when writing/serving `prematch/{file_id}.json` or
  `match` API output, split each class's match instances into
  side-prefixed keys based on bbox-center containment.

## Impact

- **Backend (`app/files.py`, `app/storage.py`, `app/main.py`,
  `app/matching.py`)**: new `frontside_rect` / `bottomside_rect`
  columns on `files` (stored as JSON: `{x0,y0,x1,y1}` or null), new
  endpoints to read/write them, and a small post-processing pass in
  the match-JSON serializer that walks each instance's entity
  centroids, computes a bbox center, and rewrites the key.
- **Frontend (`app/templates/viewer.html`, `app/static/canvas.js`,
  `app/static/style.css`)**: new toolbar button, hotkey, mode state
  machine (mirrors the measure tool's mode-gating pattern from
  [[project_smdr2_workflow]]), two-step rectangle capture, overlay
  renderer, API calls to persist regions. Mark-mode joins the Esc
  cascade.
- **Storage:** two `REAL`/`TEXT` columns on `files`; no new files on
  disk. Existing match/prematch JSON caches are invalidated when
  regions change so the next read regenerates with the new keys.
- **No change** to the matcher itself ([[pattern-matching]]) — the
  side label is applied purely at serialization time on top of the
  matcher's existing output.
- **Rule check** ([[design-rule-checking]]) sees the new keys
  transparently; rules that key on `<class>.<i>` patterns are
  forward-compatible because the side prefix is additive on the left.

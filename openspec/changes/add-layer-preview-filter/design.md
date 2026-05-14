## Context

Today every uploaded DXF goes through one preprocess job (`app/jobs.py`
`_preprocess_worker`) that does the expensive work in one shot: parse →
flatten primitives → build entity shape index → scan-all against the
file's library → persist `parsed/{file_id}.json` and
`prematch/{file_id}.json`. The file's lifecycle is `preprocessing →
ready_to_match → checking_rules → report`. Every primitive that ezdxf
emits ends up in the parsed JSON, regardless of which DXF layer it came
from — primitives already carry their `layer` string (see `_props` in
`app/dxf.py`), but nothing downstream uses it as a filter.

Real customer DXFs routinely contain 20+ layers, most of which are noise
for the packaging engineer (vendor stamps, dimension overlays, drill
grid, alternate-stackup geometry). Indexing and scanning those layers is
pure overhead in render, match, and rule-check.

The dashboard is product-centric: a product holds DXFs in role slots
(`BD`, etc.). Layer selection has to be **per file**, not per product,
because two DXFs in the same product (BD vs. another role) typically
ship completely different layer sets.

## Goals / Non-Goals

**Goals:**
- Cheap, fast first-pass that lists every layer in the file with a
  human-readable thumbnail.
- The user picks layers **once per file**, before any heavy work
  (scan-all pre-match, rule check) is invested in irrelevant geometry.
- The selection survives library/role swaps and re-preprocesses; the
  user is not re-prompted unless they explicitly choose to edit.
- Everything downstream (render, match, scan-all, rule check) operates
  on the filtered primitive set transparently — no algorithm rewrite.
- Thumbnails render in the same coordinate frame so the user can
  recognise BD outline vs. silkscreen vs. pads at a glance.

**Non-Goals:**
- Per-class layer filtering (e.g., "use these layers only for
  `bga_ball`"). Filtering is whole-file scope for v1.
- Live, in-viewer layer toggles. The viewer renders whatever the chosen
  layer subset produced; toggling layers post-render requires re-running
  Phase 2, which is invoked via the "Edit layers" affordance.
- DXF block / xref / paperspace handling beyond what ezdxf's `Frontend`
  already flattens in modelspace.
- Persisting per-library or per-product layer presets ("apply this layer
  set to every new file"). Could come later if customers want.
- Recovering layers that were dropped after Phase 2 without re-running
  Phase 1 (we always re-derive from the cached layer manifest, which
  Phase 1 wrote).

## Decisions

### D1. Two-phase preprocess with a new `awaiting_layers` lifecycle state

The single `preprocessing` state in `app/files.py` splits into a
two-phase flow:

```
upload → discovering_layers → awaiting_layers → preprocessing → ready_to_match → …
                                                                              ↘ error
```

- `discovering_layers` (Phase 1): the cheap pass — parse DXF with
  ezdxf, enumerate layers, render per-layer SVG thumbnails. Writes
  `data/layer_preview/{file_id}/layers.json` + one
  `data/layer_preview/{file_id}/<layer>.svg` per layer.
- `awaiting_layers`: terminal-for-now state. The dashboard polls this
  status, opens the modal, and waits for the user.
- `preprocessing` (Phase 2): the existing heavy work, but now filtered
  to the user's chosen layers.

This keeps the existing state machine compatible (downstream code
already understands `preprocessing` and `ready_to_match`) while making
the new gate visible to the polling UI.

**Alternatives considered:**
- *Skip the gate, do everything then offer "post-filter".* Wastes the
  expensive scan-all on noise layers — the whole point is to avoid it.
- *Re-use `preprocessing` for both phases.* Would force the UI to poll
  some other field to know "is the layer modal ready yet"; explicit
  status is cleaner.

### D2. Phase 1 thumbnail strategy: per-layer SVG via ezdxf's `MatplotlibBackend`-style approach, or DIY JSONBackend filter

We need a compact, faithful per-layer preview. Three options
considered:

1. **PNG via matplotlib** — heavy dep, slow, file-system bloat.
2. **SVG via ezdxf's built-in matplotlib backend** — still pulls
   matplotlib, and would emit one SVG per render call.
3. **Reuse `JSONBackend`, group primitives by `layer`, emit a small SVG
   directly from the primitive list (chosen).**

We pick option 3 because:
- The existing `JSONBackend` already produces flat primitives keyed by
  `layer`. Grouping is a single dict-walk.
- SVG keeps it scalable (the dashboard renders the thumbnail at ~160 px
  but the same SVG would zoom cleanly).
- No new dependencies, no font / DPI issues.
- We re-use the file-wide bbox so every layer's thumbnail aligns on the
  same world frame — the user sees pad-row-1 vs. pad-row-2 instead of
  two random-scale shapes.

Thumbnail simplification:
- Stroke width fixed at a screen-px-equivalent (e.g., 1 unit, scaled
  via SVG `viewBox` to match the bbox).
- `filled_polygon` rendered as `<polygon>` with a 50%-alpha fill so
  pads and outlines remain distinguishable.
- Lines / polylines stroked in the primitive's own color (falls back to
  black if missing).
- Decorative entities (text/dimension/hatch — already flagged by the
  existing `_decorative` mechanism) are skipped in the thumbnail, since
  the user doesn't need to know dimensions exist; their layer entry is
  still listed.

Thumbnails are emitted at one viewport per layer:
`viewBox="xmin ymin width height"` where the bbox is the file-wide bbox
(not the per-layer bbox), so every preview shares the same frame.

**Alternatives considered:**
- *Per-layer bbox per thumbnail* — would make a single line on one
  layer look as big as the whole BD outline on another. Confusing.
- *Single composite "layer-on / layer-off" toggle preview* — clever but
  defers the cost to the client, and the modal would have to ship the
  full primitive list before the user picks anything.

### D3. Phase 1 also writes the parsed primitives once, Phase 2 just filters

Parsing a real DXF is the slowest step. To avoid re-parsing in Phase 2:

- Phase 1's worker calls `flatten_for_render(path)` exactly once, and
  writes the full primitive set to a transient
  `data/layer_preview/{file_id}/primitives.json`.
- Phase 2 reads that file and filters primitives by the user's selected
  layer set, then resumes today's pipeline (handle index → shape index
  → scan-all → write `parsed/`, `prematch/`).
- Once Phase 2 succeeds, `data/layer_preview/{file_id}/primitives.json`
  is **deleted** (we don't need two copies). Thumbnails and
  `layers.json` are kept.

If the user later clicks "Edit layers", we re-run `flatten_for_render`
(cheap once the bytes are in the OS page cache) into the transient
file again, then proceed with Phase 2.

**Alternative considered:** Keep parsed full primitives permanently and
filter at read-time. Rejected — `parsed/{file_id}.json` is consumed by
many endpoints; threading a layer filter into every consumer is more
invasive than filtering at write-time.

### D4. Selection persistence: `selected_layers` TEXT column on `files`

Add a nullable `selected_layers TEXT` column to the `files` table.
Stored as a JSON-encoded array of layer names. `NULL` means
"user has not chosen yet" (i.e., file is in `awaiting_layers`).
Empty `[]` is forbidden — the UI's confirm button is disabled until at
least one layer is checked.

When the user confirms (`POST /api/files/{file_id}/layers`):
- Validate every name against the layer manifest written by Phase 1.
- Persist the list.
- Flip status to `preprocessing` and submit the Phase 2 job.

On library / role swap (`PATCH /api/files/{file_id}`), the cached
`selected_layers` is **reused**, and Phase 2 re-runs against the new
library snapshot. The user is not re-prompted.

The viewer's "Edit layers" button clears nothing — it just opens the
modal pre-checked with the current `selected_layers`. Confirming re-runs
Phase 2.

### D5. Filter is applied at primitive-load boundary, not in every consumer

A new helper `app/dxf.py::filter_primitives(primitives, layer_set)`
walks the primitive list and drops any whose `layer` is not in
`layer_set`. Decorative primitives are filtered alongside, on the same
rule (their `layer` is the host entity's layer).

This helper runs **once** inside `_preprocess_worker` between
`flatten_for_render` and `build_handle_index`. After that point, the
existing pipeline (handle index → shape index → scan-all → match-JSON
→ rule check) is unchanged: it never sees primitives from rejected
layers, so no consumer needs to know about the filter.

`parsed/{file_id}.json` gains a `selected_layers: [...]` field so
downstream debugging tools can see what was kept.

### D6. Dashboard modal: per-file, auto-opens on `awaiting_layers`

Dashboard polling (`GET /api/products`) already returns
`files_by_role[*]`; it gains a `status: "awaiting_layers"` value plus
a `layers_available: true` hint. On detecting that status the dashboard
JS opens the layer-selection modal for that file:

- Title: `<filename> — pick layers to include`.
- Body: a responsive grid of cards. Each card has the SVG thumbnail
  (~160 × 120 px), a checkbox bound to layer name, the layer name
  rendered as a monospace label, and "<N entities>" counter.
- Footer: "Use selected (K of M)" primary button (disabled when K=0),
  "Select all" / "Select none" links.
- The modal is **modal** (blocks page interaction) — but the user can
  cancel and come back later; the file simply stays in
  `awaiting_layers`.

Thumbnail SVGs are fetched lazily via
`GET /api/files/{file_id}/layer-preview/{layer}.svg`; the modal
inserts them as `<img>` so the browser can stream them.

A "Layers" button on the file's row (visible in any post-Phase-1 state)
re-opens the modal with current selections pre-checked.

### D7. Viewer "Edit layers" affordance

The viewer header gets a small "Layers" button. Clicking it:
- Fetches `GET /api/files/{file_id}/layers` (manifest + current
  selection).
- Re-uses the same modal markup as the dashboard (extracted into a
  shared partial / shared JS helper).
- On confirm, posts the new selection, watches for status →
  `ready_to_match`, then reloads the page so the canvas re-fetches the
  newly-filtered primitives. This mirrors the existing library-swap
  reload pattern in `viewer.html`.

### D8. Backwards compatibility for already-uploaded files

Existing rows in `files` have no `selected_layers` and the new column
defaults to NULL. On startup migration, any file already in
`ready_to_match` (or beyond) is **left alone**: its parsed cache stays
valid, just without a recorded layer selection. The viewer's "Edit
layers" button on those files will show "all layers checked" by
default; the user can opt into filtering at their own pace.

Files currently sitting at `preprocessing` (in-flight at deploy time)
are short-lived; if a worker crashes mid-deploy the file goes to
`error` and the user re-uploads. No special handling needed.

`data/parsed/{file_id}.json` files without `selected_layers` are
treated as "all layers". The field is informational, not load-bearing.

### D9. Process-pool reuse: Phase 1 and Phase 2 both use the existing executor

Phase 1's worker is lightweight (flatten + group + write SVGs) but
still does the DXF parse, so we keep it on `ProcessPoolExecutor` for
parity with Phase 2 and to avoid blocking the event loop. The
in-memory job dict (`_jobs` in `app/jobs.py`) gains a `phase` field
(`"discover"` | `"preprocess"`) so the dashboard can show stage-aware
progress copy ("scanning layers" vs. "indexing geometry").

`MAX_WORKERS=2` is unchanged; in the worst case one upload occupies one
worker through both phases, with the user-confirm gap in the middle —
i.e., the worker is free during the gap, available for other files.

## Risks / Trade-offs

- **[Risk]** Thumbnails of a file-wide bbox look empty for layers that
  occupy a small region (e.g., a 5×5 mm stamp on a 200×200 mm board).
  → **Mitigation:** show the entity count next to the thumbnail and
  consider a small magnifier-zoom button as a follow-up. Acceptable for
  v1 because the user's mental model is "which layer is this in
  context?", not "show me the layer in isolation".
- **[Risk]** Phase 1 picks up layers from blocks/inserts that the user
  may not recognize by name (e.g., `0`, vendor-defined codes).
  → **Mitigation:** the thumbnail makes the layer recognisable
  regardless of its name. The default-all selection means a confused
  user can confirm without breaking anything.
- **[Risk]** Re-confirming a layer set produces a different scan-all
  result than the previous one; users might miss matches they relied on.
  → **Mitigation:** every Phase 2 run is idempotent and re-derives all
  caches; the user is the one driving the change. The "Edit layers"
  button is a deliberate action, not a side-effect.
- **[Trade-off]** Two-phase workflow adds latency from upload to
  `ready_to_match` (the user gap). For users who want the old behavior,
  selecting "all layers" in the modal makes Phase 2 exactly equivalent
  to today's preprocess. We accept the prompt as the price of the
  feature; selecting all + confirming is two clicks.
- **[Trade-off]** Disk cost: per-layer SVGs accumulate per file. Real
  files have ~10-30 layers; at ~2-5 KB per SVG that's ~50-150 KB extra
  per upload. Negligible vs. uploaded DXF size.

## Migration Plan

- **Schema:** `ALTER TABLE files ADD COLUMN selected_layers TEXT`
  guarded by the same `PRAGMA table_info` migration pattern already in
  `app/files.py`. Default NULL.
- **Disk:** `data/layer_preview/` directory created at startup
  alongside `data/parsed/`, `data/prematch/`, etc.
- **Status migration:** existing rows in `preprocessing`,
  `ready_to_match`, etc. are unchanged. The new `awaiting_layers`
  state only appears on **new** uploads after deploy.
- **Rollback:** removing the feature requires no schema change
  (selected_layers column simply goes unread). The two-phase split
  collapses back to one phase by short-circuiting Phase 1 to write
  `selected_layers = <all layer names>` and submitting Phase 2
  immediately — a one-line code path if we ever want to disable the UI.

## Open Questions

- Should the modal show a **composite preview** (all checked layers
  overlaid) at the bottom, so the user can see the cumulative result
  before clicking Confirm? Probably yes, but it requires another SVG
  render endpoint or client-side compositing. Deferred to v2 unless
  early feedback demands it.
- Should `selected_layers` be a **library default** so that two DXFs in
  the same product / library can inherit a layer-naming convention?
  Tempting, but layer names are not standardized across vendors.
  Deferred.
- For files with a single non-default layer (`0`), should we skip the
  modal entirely? Probably — degenerate UX. Leave for follow-up after
  observing real upload distributions.

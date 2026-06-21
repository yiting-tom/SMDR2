# Design — render paper-space layouts

## Problem framing

A DXF has tabs: `Model` (modelspace) plus zero or more paper-space
**layouts** (`Layout1`, …). `flatten_for_render` rendered only modelspace.
Two distinct real-world shapes break on that:

1. **Geometry in a single layout** (model space empty). The fix is
   invisible: auto-fall back to that layout.
2. **Geometry spread across several layouts** ("one view per tab"). The
   operator must choose which tab to load — there is no safe automatic
   answer.

Viewports (a layout that merely *windows into* model space) are NOT this
case: there the geometry is in model space, so auto-resolution renders
model space and the layouts are irrelevant. The picker only ever appears
when model space is genuinely empty.

## Key decision: keep the common path at one DXF open, zero regression

The dominant case — geometry in model space — must not pay for this
feature. So the gate is driven off a flag the parser already computes,
not a second pass:

- `flatten_for_render` returns `source_is_paperspace`. When auto-resolution
  used model space (model space had entities), it is `False` and the
  discover worker proceeds exactly as before — **no `enumerate_layouts`
  call, no extra open**.
- Only when auto-resolution *fell back* to a paper-space layout
  (`source_is_paperspace == True`) does the worker run `enumerate_layouts`
  to decide between "single layout → just use it" and "multiple layouts →
  picker". The wasted initial flatten in the rare multi-layout case is
  acceptable; the common case is untouched.

Alternative considered and rejected: a dedicated always-on "layout
discovery" Phase 0 before layer discovery. Cleaner separation, but it adds
a second DXF open to *every* upload (model-space files included) — a real
latency regression on 100k-entity files for a feature that helps a small
fraction of files. Folding the gate into the existing discover worker
keeps the cost where the benefit is.

## Resolution rules (`_resolve_layout`)

```
if layout_name given and exists      → that tab
elif modelspace has entities         → modelspace        (historical default)
elif any paper-space layout non-empty→ the one with the most RENDERABLE entities
else                                 → modelspace (empty, == old behaviour)
```

**Renderable count, not raw length.** AutoCAD writes a VIEWPORT into
essentially every paper-space layout tab (the sheet's window into model
space), so a pure framing/title-block tab has `len(layout) >= 1` despite
holding no drawable geometry. Counting raw length would (a) let a
viewport-only tab out-rank or tie a real tab and (b) trip the picker on the
single-real-layout DWG-export case the feature targets. So `entity_count`
(in `_enumerate_layouts_doc`) and the `_resolve_layout` ranking both use
`_renderable_entity_count`, which excludes `NON_RENDERED_DXFTYPES`
(`{"VIEWPORT"}`). The modelspace fast-path keeps `len(msp) > 0` (O(1));
modelspace never holds viewports, so the per-entity scan is only paid on the
rare paper-space-fallback path.

This cheap proxy still over-counts entities that render empty for other
reasons (e.g. TEXT under `TextPolicy.IGNORE`). The picker build is the exact
backstop: `_build_layout_picker` flattens each candidate, drops tabs with
zero primitives, and commits a manifest only when ≥2 tabs actually render —
otherwise the worker falls through to layer discovery on the auto-resolved
tab. So the operator is never offered (nor can pin) a blank tab.

The flatten-tolerance diagonal for a paper-space tab is likewise measured
over `NON_RENDERED_DXFTYPES`-excluded entities, so the VIEWPORT's sheet
rectangle doesn't inflate the diagonal and coarsen the tolerance.

## Persistence: `chosen_layout` mirrors `user_unit_override`

The chosen tab is per-file render state that every re-preprocess must
honour, so it follows the proven `user_unit_override` pattern exactly:

- nullable `chosen_layout TEXT` column, idempotent `ADD COLUMN` migration,
  tolerant `_get` read (NULL = modelspace = every legacy row).
- `submit_preprocess` / `submit_discover_layers` resolve it from the row
  when the caller passes `None`, so library-swap / unit-override /
  reprocess-all all keep rendering the pinned tab without extra plumbing.
- Auto-resolved paper-space sources are stamped back to `chosen_layout`
  after preprocess (`_persist_source_layout`) so the choice is stable and
  the badge has a value. Modelspace sources leave it NULL.

Cache consistency: the Phase-1 transient primitives cache is rendered with
the same `chosen_layout` Phase 2 resolves (both read the row), so reuse is
layout-consistent — no new cache key needed. The transient cache also
carries `source_layout` so the cache-reuse path can stamp the badge
without re-opening the DXF.

## Picker flow (multi-layout only)

```
upload → discovering_layers → _discover_layers_worker (auto)
  source_is_paperspace and ≥2 content layouts?
    yes → render per-tab thumbnails + layouts.json → awaiting_layout
            → operator picks → POST /layouts → set chosen_layout
            → re-run discover (explicit tab, gate skipped) → awaiting_layers
    no  → layers.json as today → awaiting_layers
→ confirm layers → preprocess(layout_name from row) → ready_to_match
```

The layout picker is a single-select clone of the layer picker (radios, no
select-all/none) reusing the same manifest/thumbnail/SVG contract and modal
CSS. Layout assets live in `layer_preview/{id}/layouts/` so they survive
the layer-discovery rewrite and the operator can re-pick (the "View"
action, gated on `has_layout_options`).

## Tolerance

Curve-flatten tolerance is `max(BASE_TOLERANCE, diagonal * SCALE_FACTOR)`.
The diagonal must reflect the *rendered* tab: modelspace keeps the
`$EXTMIN/$EXTMAX` header shortcut; a paper-space layout uses
`ezdxf.bbox.extents(layout, fast=True)` because the header extents describe
model space, not the layout's own coordinate frame.

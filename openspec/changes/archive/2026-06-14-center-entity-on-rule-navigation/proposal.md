## Why

When a rule-check sub-rule's geometry lives in another role's DXF, the
sidebar shows a "→ {part} viewer" affordance that navigates to that file's
viewer with `?rule=&idx=`. On arrival the target sub-rule is *focused*
(highlighted) — but the view is still at the default whole-file framing, so
the operator has to hunt for the highlighted entity, which may be tiny and
off to one side. After "go to role" the viewer should **locate onto the
entity and centre it**.

## What Changes

- When a sub-rule is focused as the result of **cross-role navigation**
  (the `?rule=&idx=` URL applied on viewer load), the viewer pans and zooms
  so the sub-rule's geometry is **centred and framed**.
- "Geometry" is the union bounding box of whatever the sub-rule carries —
  handle entities (`from`/`to`/`tol`) and/or coordinate geometry
  (`from_coordinates`/`to_coordinates`/`to_entity`).
- Framing fits the bbox with a margin and **caps the zoom** so a tiny or
  degenerate (single-point) target does not zoom in absurdly; a point-sized
  target centres at a sensible standing zoom rather than filling the canvas.
- Local sidebar clicks (geometry already in the open file) keep their current
  behaviour — no recentre — since the operator is already looking at it.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `viewer-ui`: add a requirement that cross-role rule navigation recentres
  and frames the focused sub-rule's geometry on arrival.

## Impact

- **Frontend only**: `app/static/canvas.js` — a helper computing the focused
  sub-rule's world bbox (handles via `bboxOf`, plus coordinate points) and a
  recentre that reuses `fitToBbox` with a margin + max-zoom cap, called from
  the navigation-triggered focus path (`focusSubRuleByKey`). No payload, API,
  or schema change.
- **Non-goals**: changing local-click focus behaviour, the handle/coordinate
  rendering, or the cross-role navigation URL contract.

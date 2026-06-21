## Context

`focusSubRuleByKey(ruleName, idx, role)` runs on viewer load when the URL
carries `?rule=&idx=` (set by the sidebar's "→ {part} viewer" link). It calls
`focusSubRule`, which highlights the geometry but leaves `view` (`cx`, `cy`,
`zoom`) untouched. The viewer already has the pieces to recentre: `view` is
the pan/zoom state, `fitToBbox(bbox)` sets `cx`/`cy` to a bbox centre and
zoom to fit it (0.92 margin), `bboxOf(primitive)` gives a primitive's world
bbox, and `primitiveCenter(handle)` resolves a handle. Coordinate-mode
sub-rules carry their points directly.

## Goals / Non-Goals

**Goals:**
- After cross-role navigation, the focused sub-rule's geometry is centred and
  visibly framed without manual pan/zoom.
- Works for both handle-mode and coordinate-mode sub-rules.

**Non-Goals:**
- Recentring on local sidebar clicks (operator is already looking at it).
- New zoom UI, animation, or changes to rendering / navigation URLs.

## Decisions

### D1 — Recentre only on navigation-triggered focus
The recentre fires from the `focusSubRuleByKey` (URL `?rule=&idx=`) path, not
from `focusSubRule` called by a local click. Cross-role navigation is the
only case where the operator arrives blind; a local click already has the
entity on screen and a surprise jump would be disorienting.

### D2 — Frame the union bbox of the sub-rule's geometry
Compute one world bbox covering everything the sub-rule draws:
- handle entities: union of `bboxOf` for primitives matching `from` /
  each `to` / `tol`;
- coordinates: include `from_coordinates`, `to_coordinates`, and every
  `to_entity` point.
Centre on its midpoint so the whole annotation (e.g. a from→to segment) is
framed, not just one endpoint.

### D3 — Fit with a margin, capped zoom
Reuse `fitToBbox` semantics (centre + fit) but: pad the bbox by a small
margin, and **cap the zoom** at a sensible maximum so a tiny or single-point
target (zero/near-zero bbox) centres at a standing zoom instead of filling
the canvas. A degenerate bbox falls back to "centre on the point, keep a
default close zoom". Re-render after.

## Risks / Trade-offs

- **[Geometry not yet loaded when focus fires]** → `focusSubRuleByKey` runs
  during rule-sidebar load, after primitives/`primBBoxes` exist; guard by
  skipping the recentre if no bbox resolves (focus-only, as today).
- **[Over-zoom on a single small entity]** → the zoom cap (D3) bounds it; a
  point target lands centred at a readable zoom.
- **[Touching the gated `canvas.js`]** → change is additive: a new helper +
  one call in `focusSubRuleByKey`; the highlight/render path is unchanged.

## Open Questions

- Should the recentre also be offered as an explicit action on local clicks
  (e.g. double-click a sidebar row to recentre)? Out of scope here; revisit
  if requested.

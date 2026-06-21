## Context

RuleChecking sub-rules reference geometry by DXF handle (`from`/`to`/`tol`).
The viewer (`app/static/canvas.js`, `drawFocusedSubRule`) resolves a handle
against the **open file's** `primitives`, then draws a dashed shortest
segment between the closest points. Everything is in the open file's world
frame (DXF mm), then `worldToScreen`. Two needs are unmet: a raw
point-to-point distance, and drawing a target entity that lives in **another
product's DXF** (no handle in the open file). The rule team will emit
coordinates for these; this change defines that coordinate mode and renders
it. Constraints carried in: `canvas.js` is large and test-gated — touch only
the focused-sub-rule draw path; do not change handle-mode behaviour.

## Goals / Non-Goals

**Goals:**
- A sub-rule can express (a) a point-to-point distance and (b) a
  cross-product entity outline, drawn correctly in the open viewer.
- One coherent schema: coordinate fields sit beside the handle fields with
  clear validation; emitters pick the mode that fits.
- Zero regression to handle mode and existing envelopes.

**Non-Goals:**
- Cross-file *navigation* / coordinate alignment in the viewer (the emitter
  pre-transforms — see D1).
- Changing the handle-mode shortest-segment rendering.
- Backend enforcement, bundle/handoff format, or the rule API wrapper shape.

## Decisions

### D1 — Coordinates are pre-transformed to the open file's world frame
All new coordinates (`from_coordinates`, `to_coordinates`, every `to_entity`
point) are already in the **open viewer file's** world coordinate system
(DXF mm, same origin). The emitter owns the cross-product transform. The
viewer draws them directly with `worldToScreen` — no alignment, no transform
matrix in the payload.

*Why:* the alternative (ship per-product frames and align in JS) pushes
geometry/transform logic into the viewer and a second source of truth.
Confirmed with the user; matches "just connect the points to render".

### D2 — `from_entity` is an alias of `from`; two presentation modes
`from_entity` carries the same meaning as `from` (a source handle in the open
file) and normalises to `from` during validation — it exists only because
emitters pair it with `to_entity` for readability. A sub-rule then reads as
one of two modes, and **both always render `text`**:
- **Handle mode** (unchanged): `from`/`from_entity` + `to` (+ `tol`).
- **Coordinate mode** (new): `from_coordinates`+`to_coordinates` and/or
  `to_entity`.
Groups MAY coexist on one sub-rule; the viewer draws whatever is present.

### D3 — Coordinate-mode rendering
- `from_coordinates` ↔ `to_coordinates`: a **solid** line between the two
  points, with a **distance label in mm** at the midpoint (reuse the measure
  readout's number formatting).
- `to_entity`: a **closed dashed polygon** — connect the points in order and
  join the last back to the first — drawing the cross-product entity's
  outline. Distance is *not* labelled on `to_entity`; the measured value is
  the `from_coordinates`/`to_coordinates` line.
- Colour follows the existing focused-sub-rule convention (`pass` green
  `#69f0ae` / fail red `#ff5252`); the dashed stroke already distinguishes a
  drawn-from-coordinates outline from solid geometry.

### D4 — Validation (`_validate_envelope`), additive
- `from_coordinates` / `to_coordinates`: each `[number, number]` (length-2,
  finite numbers) or null; **paired** — one present requires the other.
- `to_entity`: null, or a **non-empty** list whose every element is
  `[number, number]`. Empty list rejected (emit null for "none").
- `from_entity`: validated as a handle and normalised to `from`. If both
  `from` and `from_entity` are set they MUST agree (else reject).
- Coordinate-mode geometry does **not** require `file_id` (self-located in
  the open frame), unlike handle-mode which still does.
- All existing handle invariants unchanged. A sub-rule MAY carry handle and
  coordinate groups together; `text` stays required and non-empty.

## Risks / Trade-offs

- **[Touching `canvas.js` draw path risks the gated viewer]** → Mitigation:
  changes confined to `focusedSubRule` population + `drawFocusedSubRule` /
  `drawFocusedLabel`; handle-mode branch untouched; verify with a
  coordinate-mode Upload Rule JSON fixture + screenshot.
- **[Spec line "must set at least one of `from`/`tol`" predates text-only
  acceptance and now also predates coordinate mode]** → The MODIFIED
  requirement restates the "renderable group" rule to read: a sub-rule
  carries a handle group, a coordinate group, or is a text-only informational
  entry — reconciling it with the adapter's actual behaviour. Scope kept to
  wording needed for coordinate mode.
- **[Bad/degenerate coordinates (NaN, 1-point `to_entity`)]** → `_validate_envelope`
  rejects non-finite numbers and enforces non-empty `to_entity`; a 1-point
  polygon renders as a degenerate dot (acceptable, not invalid).
- **[Emitter forgets the frame and ships source-DXF coords]** → Out of scope
  to detect; the line/polygon would land off-canvas. Documented in the spec
  that coords are in the open file's world frame.

## Open Questions

- Should a `to_entity` with exactly 2 points render as a closed segment
  (back-and-forth) or a single open segment? Default: closed (back-and-forth
  degenerate), consistent with the rule; revisit if the rule team needs open
  polylines later.

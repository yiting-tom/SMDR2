## Why

RuleChecking sub-rules can only point at geometry by **DXF handle**, and the
viewer resolves those handles inside the *currently open* file. That breaks
two real cases the rule team needs: (1) a plain **point-to-point distance**
between two coordinates (no entity to reference), and (2) a measurement whose
target entity lives in **another product's DXF** — there is no handle for it
in the open file, so it cannot be drawn at all today. We need a coordinate
mode alongside the existing handle mode.

## What Changes

- Add four optional coordinate fields to the RuleChecking sub-rule shape:
  - `from_coordinates: [number, number] | null`, `to_coordinates: [number, number] | null`
  - `from_entity: handleID | null` — an **alias of `from`** (source handle in
    the open file); normalised to `from`
  - `to_entity: list[[number, number]] | null` — the target entity's outline
    as raw points, for cross-product geometry that has no local handle
- Two presentation modes (a sub-rule uses one; both always render `text`):
  - **Handle mode (unchanged)** — `from`/`from_entity` + `to` (+ `tol`):
    resolve handles in the open file, dashed shortest segment.
  - **Coordinate mode (new)** — `from_coordinates` + `to_coordinates` →
    **solid line + distance label (mm)**; `to_entity` → **closed dashed
    polygon** (last point joins back to the first) drawing the cross-product
    entity outline.
- All coordinates are already in the **open file's world frame** (DXF mm,
  same origin); the emitter pre-transforms cross-product geometry, so the
  viewer draws them directly via `worldToScreen` with no alignment.
- Extend envelope validation (`_validate_envelope`): coordinate-pair shape,
  `from_coordinates`/`to_coordinates` must be paired, `to_entity` is a
  non-empty list of `[number, number]`, `from_entity` normalises to `from`.
  Coordinate-mode geometry does **not** require `file_id` (it is self-located
  in the open frame), unlike handle-mode.
- Existing handle-mode behaviour and all current invariants are preserved
  (no regression); handle and coordinate groups MAY coexist on one sub-rule
  ("the viewer draws whatever is present").

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `design-rule-checking`: The "RuleChecking JSON output shape" requirement
  gains the four coordinate fields, the coordinate-mode display rules
  (solid line + distance label; closed dashed polygon), and the matching
  validation invariants. No other requirement in the capability changes.

## Impact

- **Backend**: `app/rule_check.py` — docstring shape block + `_validate_envelope`
  (new field validators; coordinate-pair + `to_entity` checks; `from_entity`
  normalisation). Additive; handle-mode validation unchanged.
- **Frontend**: `app/static/canvas.js` — `focusedSubRule` carries the new
  fields; `drawFocusedSubRule` / `drawFocusedLabel` gain coordinate-mode
  drawing (solid measure line + mm label, closed dashed polygon). The rule
  sidebar treats coordinate-mode sub-rules as focusable (they already sit in
  the open view — no cross-file navigation needed).
- **Tests**: extend the envelope-validation tests + the Upload Rule JSON path
  with coordinate-mode cases (valid pair, unpaired rejection, bad `to_entity`,
  `from_entity` alias).
- **Non-goals**: changing handle-mode rendering, cross-file *navigation*
  (coords are pre-projected so the open viewer just draws them), backend
  enforcement, or the bundle/handoff format.

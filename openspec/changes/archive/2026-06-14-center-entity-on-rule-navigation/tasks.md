## 1. Recentre helper

- [x] 1.1 In `app/static/canvas.js`, add a helper that returns the focused
      sub-rule's world bbox — union of `bboxOf` for primitives matching
      `from` / each `to` / `tol`, plus `from_coordinates`, `to_coordinates`,
      and every `to_entity` point. Return null when nothing resolves.
- [x] 1.2 Add a recentre that pads the bbox and frames it via `fitToBbox`
      semantics, capping the zoom (max) so a tiny / single-point bbox centres
      at a standing zoom; re-render after. No-op when the bbox is null.

## 2. Wire to navigation-triggered focus

- [x] 2.1 Call the recentre from the cross-role navigation path
      (`focusSubRuleByKey`, i.e. the `?rule=&idx=` URL focus) only — local
      sidebar clicks (`focusSubRule` from a click handler) keep current
      behaviour (no recentre).

## 3. Verification

- [x] 3.1 Manual: from a multi-DXF product, open one role's viewer, focus a
      sub-rule whose target is in another role, click "→ {part} viewer", and
      confirm the target entity lands centred and framed (handle-mode and
      coordinate-mode); screenshot.
- [x] 3.2 Confirm a local sidebar click still highlights without changing
      pan/zoom (no regression).

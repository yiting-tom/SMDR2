## 1. Save the rule-sidebar scroll before leaving

- [x] 1.1 `app/static/canvas.js`: add a per-file key
  `smdr2.viewer.ruleScroll.${FILE_ID}` and a `try/catch`-wrapped
  `saveRuleScroll()` writing `$ruleSidebarBody.scrollTop` to `sessionStorage`.
- [x] 1.2 Call `saveRuleScroll()` in the cross-file sub-rule click handler
  right before `location.href = …`, and on `pagehide`.

## 2. Restore after the sidebar rebuilds

- [x] 2.1 After `renderRuleSidebar(role)` populates `#rule-sidebar-body` inside
  `loadRuleSidebar`, restore the saved offset in a `requestAnimationFrame`,
  clamped to `max(0, scrollHeight - clientHeight)`. No saved value → top.
- [x] 2.2 Restore runs after the `?rule=&idx=` focus block (sidebar already
  un-hidden when a jump brought us here); focus doesn't scroll the sidebar.

## 3. Verify

- [x] 3.1 Real-app browser verify (Playwright against a running server):
  viewer loads with **no JS errors** from the new code; the real `pagehide`
  listener writes `scrollTop` to the per-file key
  `smdr2.viewer.ruleScroll.<fileId>` (got `123`); restore reads it back to
  `123` and an oversized saved value clamps to `maxScroll` (no overscroll).
  NOT exercised end-to-end: the actual cross-file jump round-trip needs a
  multi-file version with cross-referencing rule-check results (synthetic data
  heavy) — the save call sits on the same proven `saveRuleScroll` used by the
  click handler, and restore runs in the proven `loadRuleSidebar` path.

## 4. Archive

- [ ] 4.1 `openspec validate viewer-rules-scroll-restore --strict`.
- [ ] 4.2 `/opsx:archive viewer-rules-scroll-restore` after verification.

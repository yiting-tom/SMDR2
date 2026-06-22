## Context

The rules sidebar scroll container is `#rule-sidebar-body` (`$ruleSidebarBody`,
`canvas.js:1799-1802`; `style.css:775` `overflow-y: auto`). The header/controls
are non-scrolling siblings. Cross-file rule jumps set
`location.href = /viewer/<otherFileId>?version_id=…&rule=…&idx=…`
(`canvas.js:2045-2052`) — a full reload — and `renderRuleSidebar` resets the body
with `innerHTML = ""` (`canvas.js:1899`), so scroll returns to top. Nothing
persists the sidebar scroll today.

Existing sidebar persistence uses `sessionStorage`: per-file `VIS_STORAGE_KEY`
(`smdr2.hiddenLayers.${fileId}`) and global `RULE_OPEN_KEY`
(`smdr2.viewer.ruleOpened`), all read/written through a `try/catch` JSON
wrapper. `FILE_ID = document.body.dataset.fileId` is available.

## Goals / Non-Goals

**Goals:**
- Restore the rules sidebar scroll position when returning to a file's viewer
  after a cross-file rule jump.

**Non-Goals:**
- Scroll-to-focused-rule on `?rule=&idx=` (focus does not scroll the sidebar
  today; out of scope).
- Persisting canvas pan/zoom or any other sidebar state (open rows already
  persist via `RULE_OPEN_KEY`).

## Decisions

### D1 — Per-file key in sessionStorage

Key scroll by file: `smdr2.viewer.ruleScroll.${FILE_ID}`. A cross-file jump goes
to a different file whose rule list differs, so each file must remember its own
offset — a global key would restore file A's offset onto file B. This mirrors
`VIS_STORAGE_KEY`. `sessionStorage` (not local) because a scroll offset is
per-tab and ephemeral; a stale offset from another session would mis-restore.

### D2 — Save on the navigation boundary, restore after rebuild

Save `$ruleSidebarBody.scrollTop` (a) in the cross-file click handler right
before assigning `location.href`, and (b) on `pagehide` as a catch-all for
browser back/forward and other exits. Restore after the sidebar body is
populated by `renderRuleSidebar` during `loadRuleSidebar`, in a
`requestAnimationFrame`, clamped to `scrollHeight - clientHeight` so a
now-shorter list doesn't overscroll. Unlike the dashboard (which clears its key
after restoring), keep the key so repeated returns to the same file restore
consistently; it is overwritten on the next save and naturally expires with the
tab session.

### D3 — Don't fight focus

When `?rule=&idx=` is present, `focusSubRuleByKey` highlights the `<li>` and
recenters the **canvas**, not the sidebar. Restoring sidebar scroll is therefore
orthogonal and runs unconditionally after render.

## Risks / Trade-offs

- **Restore races the async-built body** → restore in `requestAnimationFrame`
  after `renderRuleSidebar` returns, clamped to current `scrollHeight`.
- **`sessionStorage` disabled/quota** → all access `try/catch`-wrapped; degrades
  to today's top-reset behaviour, never throws.
- **Saved offset exceeds a shorter rebuilt list** → clamp to
  `max(0, scrollHeight - clientHeight)`.

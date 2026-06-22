## Why

In the viewer, clicking a rule-check sub-rule whose geometry lives on a
**different role/file** navigates via a full page load
(`location.href = /viewer/<otherFileId>?…&rule=&idx=`, `canvas.js`). Returning
reloads the viewer and `loadRuleSidebar` rebuilds `#rule-sidebar-body` from
scratch (`innerHTML = ""`), so the rules sidebar always snaps back to the first
rule. On a long rule list the operator loses their place on every cross-file
jump and has to re-scroll to find where they were.

## What Changes

- **The rules sidebar remembers its scroll position across navigation.** Before
  a cross-file rule jump (and on page hide), the viewer saves
  `#rule-sidebar-body`'s `scrollTop`, keyed per file (mirroring the existing
  per-file `VIS_STORAGE_KEY` convention, in `sessionStorage`). After
  `loadRuleSidebar` rebuilds the sidebar on the next load, the saved offset is
  restored (clamped to the new content height) so the operator returns to where
  they were instead of the top.
- No change to the `?rule=&idx=` focus flow — focus only toggles a `.focused`
  class today and does not scroll the sidebar, so restore does not fight it.

## Capabilities

### Modified Capabilities

- `viewer-ui`: ADDS a requirement that the rule-check sidebar persists and
  restores its scroll position across cross-file rule navigation.

## Impact

- **Code**: `app/static/canvas.js` only — save `$ruleSidebarBody.scrollTop` to a
  per-file `sessionStorage` key before the cross-file `location.href` jump and on
  `pagehide`; restore it after `renderRuleSidebar`/`loadRuleSidebar` rebuilds the
  body. No HTML/CSS/backend change.
- **Storage**: one new per-file `sessionStorage` key (e.g.
  `smdr2.viewer.ruleScroll.${fileId}`), following the existing JSON/try-catch
  convention.
- **Tests**: no JS test harness in the repo → manual-verify. Behaviour is
  additive and isolated to the sidebar scroll offset.
- **Relationship**: same scroll-restoration theme as the dashboard work in
  `dashboard-customer-grouping`, but a different surface (viewer rules sidebar /
  `viewer-ui` vs dashboard list / `dashboard-ui`); the two are independent.

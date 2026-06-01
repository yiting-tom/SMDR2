## Why

Production rule sets are long, and the viewer's rule sidebar renders every rule as a (collapsed) entry with no way to narrow down. An engineer hunting for a specific rule — or wanting to see only the failures in one category — has to scroll the whole list. Adding a fuzzy search box plus category and pass/fail filters makes the sidebar navigable.

## What Changes

- `app/templates/viewer.html`: add a controls block to `#rule-sidebar` (between the header and body) — a search `<input>`, a category `<select>`, and a pass/fail/all segmented toggle.
- `app/static/canvas.js`:
  - Filter state (`ruleSearchQuery`, `ruleCategoryFilter`, `ruleStatusFilter`) + the last role (`currentRuleRole`) so filter changes re-render.
  - `fuzzyMatch(query, text)` — lightweight case-insensitive subsequence match, no new dependency.
  - `ruleCategoryOf(name)` — category = the `<category>` prefix of the `<category>-<index>` rule-name format.
  - `populateRuleCategoryFilter()` — fills the category `<select>` from the loaded rule set (distinct categories, sorted), preserving the active selection.
  - `renderRuleSidebar` filters the rules by status, category, and fuzzy search over **rule name + description**; the summary appends `· showing N` when a filter is active; an empty result shows a "No rules match" message.
  - Event wiring on the three controls re-renders the (already-fetched) results — no server round-trip.
- `app/static/style.css`: styles for the controls block.

## Capabilities

### Modified Capabilities

- `viewer-ui`: ADDS a requirement that the rule sidebar provides fuzzy search (name + description) plus category and pass/fail/all filters.

## Impact

- **Code**: `viewer.html`, `canvas.js`, `style.css`. Client-only; filters the already-fetched rule-check results, no API change. `node --check` passes.
- **Tests**: none added — frontend has no automated test harness (known gap). Manual verification below.
- **Manual verification**: open a product's rule sidebar → type in the search box (rules narrow by fuzzy match on name/description); pick a category (only that category's rules show); toggle Pass/Fail/All (status filters); combinations AND together; clearing all shows everything; the summary shows `· showing N` while filtered.

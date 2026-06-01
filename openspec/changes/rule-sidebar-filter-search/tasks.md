## 1. Controls markup

- [x] 1.1 `app/templates/viewer.html`: add `#rule-sidebar-controls` (search input, category `<select>`, pass/fail/all `.seg` toggle) between the sidebar header and body.

## 2. Filter logic

- [x] 2.1 `canvas.js`: filter state + `currentRuleRole`; `fuzzyMatch` (subsequence); `ruleCategoryOf` (`<category>-<index>`); `populateRuleCategoryFilter` (distinct categories, preserves selection).
- [x] 2.2 `renderRuleSidebar`: filter entries by status + category + fuzzy(name|description); summary shows `· showing N` when filtered; "No rules match" on empty.
- [x] 2.3 Wire the three controls to re-render (client-side, no refetch).

## 3. Styles

- [x] 3.1 `style.css`: `#rule-sidebar-controls`, `#rule-search`, `#rule-category-filter`, `.seg` toggle.

## 4. Verify

- [x] 4.1 `node --check app/static/canvas.js` — OK.
- [ ] 4.2 **[USER]** Manual: search narrows by fuzzy name/description; category dropdown filters; pass/fail/all toggles; filters AND together; summary shows `· showing N`.

## 5. Archive

- [ ] 5.1 `/opsx:archive rule-sidebar-filter-search` after manual verification.

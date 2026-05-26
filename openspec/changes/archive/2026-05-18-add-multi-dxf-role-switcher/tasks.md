## 1. Switch the role-switcher data source

- [x] 1.1 In `app/static/canvas.js` `loadFileInfo`, replace `const sibling = p.files_by_role[role]` with `const siblings = p.files_by_role_all?.[role] ?? []` so the renderer sees the full sibling list. Keep the hardcoded `["SBT", "BD", "POD", "RING"]` loop.
- [x] 1.2 Branch on `siblings.length`:
    - `0` → render the existing dashed-border `role-btn.empty` (current behaviour).
    - `1` AND that file is the current viewer file → render `role-btn.current` (current behaviour).
    - `1` AND that file is NOT the current viewer file → render `<a class="role-btn" href="/viewer/${siblings[0].id}">` (current behaviour).
    - `≥ 2` → render the new dropdown trigger described in §2.

## 2. Build the dropdown trigger + menu

- [x] 2.1 Replace the `<a>` with a `<button type="button" class="role-btn role-btn--multi" aria-haspopup="menu" aria-expanded="false">` whose label is `${role} ×${siblings.length} ▾`. Carry the same `.current` class when the role matches the current file's role.
- [x] 2.2 Construct a `<ul class="role-menu" role="menu" hidden>` sibling element under the trigger. For each file in `siblings`, append `<li role="none"><a role="menuitem" class="role-menu__item" href="/viewer/${file.id}">${file.dxf_view ?? file.name}</a></li>`. When `file.id === FILE_ID` (the current viewer file), add the `role-menu__item--current` class to that `<a>` and convert it to a non-link `<span>` so clicking is a no-op.
- [x] 2.3 Click on the trigger toggles `hidden` on the menu and flips `aria-expanded`. Only one menu may be open at a time — opening one closes any other currently-open menu in the switcher.
- [x] 2.4 Outside-click (document-level mousedown that isn't inside the open menu or its trigger) closes the menu. `Esc` closes the menu and returns focus to the trigger; the handler MUST early-return when no menu is open so it never intercepts the existing viewer-wide Esc behaviour (mark-mode cancel, measure-tool cancel, etc.).
- [x] 2.5 When the user navigates to a menu item, the browser handles the link normally; the page reload re-runs `loadFileInfo` so menu state resets.

## 3. Styling

- [x] 3.1 In `app/static/style.css`, add `.role-btn--multi` styles: same border-box as `.role-btn` plus a small badge for the count and a caret glyph. Match the existing palette (`#c0cad6` resting, `#00ffff` hover/active border).
- [x] 3.2 Add `.role-menu` styles: absolutely-positioned beneath the trigger, dark panel matching the header (`background: #14181f` ish), narrow column, monospace items echoing `.role-btn` typography. Include `z-index` above the canvas.
- [x] 3.3 Add `.role-menu__item` and `.role-menu__item--current` styles: hover highlight on plain items; the current item uses the same cyan accent (`#00ffff`) used by `.role-btn.current` and is shown as a non-link cursor.
- [ ] 3.4 Confirm the role-switcher row's `inline-flex` `gap` still looks right when one or more buttons grow to `${role} ×N ▾` width. _(deferred: manual visual check in the running viewer)_

## 4. Verification

- [ ] 4.1 Manual: upload one product with two DXFs per `BD` role (one `top`, one `bottom`); confirm `BD` shows `BD ×2 ▾` and the dropdown lists both with the current one marked.
- [ ] 4.2 Manual: with that product, the other three roles still show their single-button form (or `empty`); zero visual regression for users on the old data shape.
- [ ] 4.3 Manual: middle-click and cmd-click on a menu item open the sibling viewer in a new tab.
- [ ] 4.4 Manual: open a dropdown, press `Esc` → closes; open dropdown, click outside → closes; open dropdown, click a different role's dropdown trigger → previous closes, new opens.
- [ ] 4.5 Manual: with a measure-tool operation in progress and NO dropdown open, `Esc` still cancels the measure operation (regression check on §2.4 guard).
- [x] 4.6 Re-run the existing `pytest tests/` suite — no backend change means no test churn expected. _(verified: 175 passed, 5 skipped)_

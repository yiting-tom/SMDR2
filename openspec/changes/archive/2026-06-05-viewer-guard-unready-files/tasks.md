## 1. Gate the role switcher on file status

- [x] 1.1 `app/static/canvas.js`: add `isSibViewable(sib)` (`sib?.status === "ready_to_match"`) and `notReadyTitle(sib, role)` (繁中 reasons: `error` → `處理失敗`, `awaiting_layers` → `尚未挑選圖層`, else → `尚未完成 preprocess`) near `isRoleMatched`.
- [x] 1.2 `renderRoleSlot` single-DXF branch: set `href` only when `isSibViewable(sibling)`; otherwise add `.disabled` + `title = notReadyTitle(...)` and omit `href`.
- [x] 1.3 `buildRoleDropdown` menu items: ready sibling → `<a href>` (unchanged); not-ready sibling → non-link `<span class="role-menu__item role-menu__item--disabled">` + `title`. `role` is already in scope.
- [x] 1.4 `app/static/style.css`: `.role-btn.disabled` (muted colour, `not-allowed` cursor, hover override, **no** `pointer-events:none`) and `.role-menu__item--disabled` (muted, `not-allowed`, hover override).

## 2. Fail gracefully on a not-ready file

- [x] 2.1 `app/static/canvas.js` `load()`: check `primRes.ok` before `.json()`. On 425 set a "drawing not ready — wait on Products" status; on other non-OK statuses show the HTTP code; `return` instead of rendering a blank canvas.

## 3. Verify

- [x] 3.1 `node --check app/static/canvas.js` — OK.
- [x] 3.2 **[USER]** Manual: product with one ready role + one role mid-preprocess → the preprocessing role is muted / not-allowed, has no link, shows its `title`; the ready role still navigates. Multi-DXF dropdown lists a not-ready sibling as a disabled non-link with a reason. Opening a not-ready file's viewer URL directly shows the "not ready" status message, not a blank canvas. — user confirmed verified.

## 4. Archive

- [x] 4.1 `/opsx:archive viewer-guard-unready-files` after manual verification.

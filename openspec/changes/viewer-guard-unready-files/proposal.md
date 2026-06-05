## Why

The viewer header's per-role switcher (`SBT` / `BD` / `POD` / `RING` / `LID`)
decides whether a role is clickable purely on whether the product has a DXF
under that role — it never consults that DXF's pipeline `status`. The moment a
file is uploaded it appears in `files_by_role_all[role]`, so the switcher
immediately renders it as a navigable `<a href="/viewer/{id}">`, even while the
file is still `discovering_layers` / `awaiting_layers` / `preprocessing`, or has
gone to `error`.

This is worse than a cosmetic glitch. Opening a not-ready file navigates to
`/viewer/{id}` (which has no status gate), whose bootstrap then calls
`GET /api/files/{id}/primitives`. That endpoint is guarded by `_resolve_file`
and returns **HTTP 425** for a non-ready file. The viewer's `load()` calls
`.json()` on the response **without checking `.ok`**, so the result silently
breaks: a permanently blank canvas with the status line stuck on `fetching…`
and no message telling the operator what happened.

The dashboard already gets this right — its "Open →" link only renders for
`ready_to_match` files (`dashboard.js`). The viewer's switcher is simply
inconsistent with the dashboard's own gating.

## What Changes

- **Gate the role switcher on file status** (`renderRoleSlot` /
  `buildRoleDropdown` in `app/static/canvas.js`): a sibling DXF is navigable
  only when its `status === "ready_to_match"`. A sibling still in the
  `discovering_layers` / `awaiting_layers` / `preprocessing` pipeline, or in
  `error`, renders as a disabled, non-navigable control:
  - **Single-DXF slot** → a `role-btn.disabled` with **no `href`** and a
    `title` that states why it is locked.
  - **Dropdown menu item** → a non-link `role-menu__item--disabled` `<span>`
    (instead of an `<a href>`), also with a `title`.
  - The dropdown **trigger** stays clickable (it only opens the menu) so ready
    siblings under the same role remain reachable.
- **Localised lock reasons** (繁中): `error` → `<ROLE> 處理失敗`;
  `awaiting_layers` → `<ROLE> 尚未挑選圖層`; otherwise → `<ROLE> 尚未完成 preprocess`.
- **Fail gracefully instead of a blank canvas** (`load()` in
  `app/static/canvas.js`): check `primRes.ok` before `.json()`. On a 425 show a
  "this drawing isn't ready — wait on the Products page" status message; on any
  other non-OK status show the HTTP code. No more silent blank canvas.
- **CSS** (`app/static/style.css`): add a `.role-btn.disabled` rule (muted
  colour, `not-allowed` cursor, hover override) and a
  `.role-menu__item--disabled` rule. Deliberately **no `pointer-events: none`**
  — the omitted `href` already makes the element non-navigable, and keeping
  pointer events alive preserves the native `title` tooltip that tells the
  operator *why* the role is locked.

## Capabilities

### Modified Capabilities

- `viewer-ui`: MODIFIES "Per-role sibling-DXF dropdown switcher" so a sibling is
  navigable only when `status === ready_to_match`; non-ready / errored siblings
  render disabled with a localised reason. ADDS a requirement that the viewer
  surfaces a not-ready file as a status message instead of a silent blank
  canvas.

## Impact

- **Code**: `app/static/canvas.js` (one helper pair + three render branches +
  one `load()` guard), `app/static/style.css` (two rules). No backend change —
  `FileRecord.to_dict()` already serialises `status` into
  `files_by_role_all[role]` (`app/files.py`), so the frontend can gate on
  `sib.status` today. JS passes `node --check`.
- **Tests**: none added — the frontend has no automated test harness (known
  gap). Manual verification below.
- **Behaviour**: the currently-viewed file is always `ready_to_match` (its own
  `/primitives` fetch succeeded or the page wouldn't have rendered), so it is
  never affected. The switcher re-renders via `refreshRoleSwitcher()` after
  Save-Match / region edits; a sibling that finishes preprocessing elsewhere
  won't auto-enable until the next refresh — acceptable, and matches the
  existing staleness of the `match_saved` flag.
- **Tradeoff noted**: omitting `pointer-events: none` keeps the lock-reason
  tooltip but means the only navigation guard is the absent `href` (plus the
  hover-style override). There is no global click handler that would navigate an
  hrefless role item, so this is safe today.
- **Manual verification**: with a product that has one ready role and one role
  whose DXF is still preprocessing → the preprocessing role renders muted /
  not-allowed, has no link, and shows its `title`; the ready role still
  navigates. Force a 425 (open a not-ready file's viewer URL directly) → the
  status line shows the "not ready" message instead of a blank canvas.

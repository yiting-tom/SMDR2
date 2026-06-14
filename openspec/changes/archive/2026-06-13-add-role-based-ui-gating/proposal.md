## Why

The server already enforces authorization on every write (`editor_guard` →
403/423), but the **frontend shows every edit affordance to everyone**. A
`viewer`-role user sees Replace / Delete / Add file / 開始編輯 / 畫押 / 新增版本 /
Rule Check / Save Match / 範本 / Scan All and only discovers they are not
allowed *after* clicking and getting a 403. The UI contradicts the
permission model — confusing, and it reads as "broken" rather than
"read-only". Now that role impersonation is testable in dev
(`SMDR2_DEV_RESOLVE_GRANTS`), we can align the UI with the enforcement.

## What Changes

- **Surface each product's effective role to the client.** `/api/products`
  (and `/api/products/{id}`) return an `effective_role`
  (`viewer | editor | admin`) per product, computed by the same
  `effective_role()` the guards use — single source of truth, no authz
  logic duplicated in JavaScript.
- **Dashboard gates write affordances by that role.** For a `viewer`
  product, hide/disable: upload (slot click / drop / Replace / Add file /
  Pick layers·view), Delete product, 開始編輯/lock control, 畫押·解除畫押,
  新增版本, Rule Check / Upload Rule JSON. Keep read affordances: 開啟
  viewer, 查看結果, 比較版本, version switcher.
- **Viewer gates write affordances by role.** For a `viewer` file, hide/
  disable: 開始編輯 (edit lock), Save Match, 範本 (library manage), and the
  template-creating paths (frame-select → save template). Keep read tools:
  Scan All, Measure, Layers, Rules, class inspection, pan/zoom.
- **Read-only signposting.** A small, consistent "唯讀(viewer)" indicator
  so the absence of edit controls is explained, not mysterious.
- **No change to backend enforcement.** `editor_guard` / `require_unsigned`
  / the lock protocol remain the real boundary; this is defense-aligned UI,
  not a new security control. Bypass-admin dev mode keeps full affordances.

## Capabilities

### New Capabilities
- `role-based-ui`: Frontend affordance gating driven by the caller's
  effective role per product — which write actions are shown, disabled, or
  hidden on the dashboard and the viewer, and the read-only experience for
  `viewer`-role users. Owns the rule that the UI must not present a write
  control the server would reject for the current role.

### Modified Capabilities
- `product-files`: The product/version/file read API (`/api/products`,
  `/api/products/{id}`) gains a per-product `effective_role` field so the
  client can gate affordances without re-deriving authorization.

## Impact

- **Backend**: `app/main.py` (`/api/products`, `/api/products/{id}` payload
  builders) add `effective_role` via `app.guards.effective_role`. No new
  enforcement; additive field only (no breaking change to existing
  consumers).
- **Frontend**: `app/static/dashboard.js` (card/slot/footer/version-bar
  affordance rendering, lock control), `app/static/canvas.js` +
  `app/static/edit_lock.js` (toolbar write buttons, edit-lock slot),
  `app/static/topnav.js` is unaffected. `app/static/style.css` for the
  read-only indicator + disabled styling.
- **Tests**: backend payload test for `effective_role`; the role-gating is
  exercised via `SMDR2_DEV_RESOLVE_GRANTS` impersonation.
- **Non-goals**: changing what the server allows, per-field granular locks,
  or hiding read data (viewers still see everything they can read).

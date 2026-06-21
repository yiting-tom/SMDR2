## 1. Backend — surface effective_role (D1)

- [x] 1.1 In `app/main.py`, add `effective_role` to the `/api/products` list
      payload: for each visible product, set it to
      `app.guards.effective_role(ident, product.id)`.
- [x] 1.2 Add the same `effective_role` field to the `GET /api/products/{id}`
      payload builder.
- [x] 1.3 Add a backend test: a product-scoped `viewer` grant yields
      `effective_role == "viewer"` for that product; bypass mode yields
      `"admin"`. Exercise via `SMDR2_DEV_RESOLVE_GRANTS` or a dependency
      override.

## 2. Frontend — shared role helper (D2)

- [x] 2.1 Add a tiny role-tier helper usable by dashboard.js (and reused
      conceptually in the viewer): `roleAtLeast(role, min)` over
      `viewer < editor < admin`, plus `isReadOnly(role) = !roleAtLeast(role,
      'editor')`. Keep it inline/local — no new shared module unless trivial.

## 3. Dashboard gating (D2, D3, D4)

- [x] 3.1 Read `product.effective_role` in `productCard()`; compute
      `readOnly = isReadOnly(role)` and treat it like the existing `signed`
      flag where it suppresses write controls.
- [x] 3.2 Header: when `readOnly`, omit the Delete button and the
      開始編輯/lock control; add a "唯讀" chip next to the product name.
- [x] 3.3 Version bar: when `readOnly`, omit 畫押 / 新增版本; keep the version
      switcher and 比較. Gate 解除畫押 to `role === 'admin'` only.
- [x] 3.4 Footer: when `readOnly`, omit Rule Check and Upload Rule JSON; keep
      查看結果 (Check Result) when a result exists.
- [x] 3.5 Slots (`slotCell` / file actions): when `readOnly`, render empty
      slots as a non-interactive "（唯讀)" placeholder (no click / no
      drag-and-drop), and omit Replace / Add file / Delete / Pick
      layers·view; keep 開啟 viewer.
- [x] 3.6 Ensure dev-mode-only download affordances still respect `readOnly`
      (don't reintroduce write controls under Developer Mode for viewers).

## 4. Viewer gating (D2, D3, D4)

- [x] 4.1 At viewer boot, obtain the product's `effective_role` (reuse the
      payload `canvas.js` already loads if it carries product context;
      otherwise one cached `GET /api/products/{product_id}`). Resolve the
      open question in design.md by reading the boot path first.
- [x] 4.2 When `isReadOnly(role)`, hide the toolbar write buttons
      (`#save-match-btn`, `#library-btn`) and the 開始編輯 content in
      `#edit-lock-slot` (via `edit_lock.js`). Do NOT touch canvas render,
      class toolbar, or event wiring.
- [x] 4.3 Gate the template-create path (frame-select → save template) so a
      viewer cannot trigger it; keep Scan All / Measure / Layers / Rules /
      inspection / pan-zoom available.
- [x] 4.4 Add a "唯讀" chip in the viewer header (reuse `signed-badge`
      styling family).

## 5. Styling (D4)

- [x] 5.1 Add a `.readonly-chip` (or reuse an existing chip class) in
      `style.css` for the dashboard card + viewer header indicators;
      ensure it reads clearly in the dark theme.

## 6. Verification

- [x] 6.1 Backend: `uv run pytest` for the new payload test + existing auth
      tests stay green.
- [x] 6.2 Manual impersonation (copy `data/` to a temp dir; grant a test
      user viewer / editor / admin; run with
      `SMDR2_DEV_RESOLVE_GRANTS=1 SMDR2_DEV_USER=<them>`): confirm the
      dashboard card and viewer show exactly the affordances each role is
      entitled to, and the 唯讀 chip appears for viewer.
- [x] 6.3 Confirm editor/admin and bypass-admin dev mode are visually
      unchanged from before (regression check).
- [x] 6.4 Confirm a stale-role click still 403s gracefully (existing error
      path), proving the gate stays advisory.

## Context

The authorization matrix (`viewer < editor < admin`, scoped global /
customer / product) is enforced server-side by `app/guards.py`
(`editor_guard`, `require_unsigned`, `admin_guard`, the edit-lock protocol).
The frontend, however, renders every affordance unconditionally and relies
on the server's 403/423 to stop a write. With real roles now testable in
dev (`SMDR2_DEV_RESOLVE_GRANTS=1`), the gap is visible: a `viewer` sees
Replace / Delete / 開始編輯 / 畫押 / 新增版本 / Rule Check / Save Match and
only learns they cannot use them by clicking.

Constraints:
- **Backend enforcement is the real boundary and must not change.** This is
  UX alignment, not a security control.
- **`canvas.js` is large (~6.4k lines) and gated on a test harness.** Touch
  only the toolbar write buttons and the edit-lock slot — never the canvas
  render/interaction core.
- **Bypass-admin dev default must keep full affordances** (single-user dev).
- The dashboard already loads `/api/products`; the viewer is file-scoped and
  knows its `product_id` from `<body data-product-id>`.

## Goals / Non-Goals

**Goals:**
- The UI never presents a write control the server would reject for the
  caller's current role on that product.
- A `viewer` gets a clean, obviously read-only experience with a small
  signpost, not a wall of dead buttons.
- One authorization source of truth — no re-implementation of role folding
  in JavaScript.

**Non-Goals:**
- Changing what the server allows, or removing the 403/423 fallback.
- Per-field / per-slot granular locks beyond the product's effective role.
- Hiding *read* data (a viewer still sees files, matches, rule results).
- Gating the admin console (`/admin` is already `admin_guard`-only end to
  end and 403s the page for non-admins).

## Decisions

### D1 — Backend surfaces `effective_role` per product (not client recompute)
`/api/products` and `/api/products/{id}` add `effective_role`
(`viewer|editor|admin`) computed by `app.guards.effective_role(ident,
product_id)` — the exact function the guards use. The dashboard reads it off
each product object; the viewer fetches `/api/products/{product_id}` (or
reads it from the payload it already loads) for its single product.

*Why over client-side folding:* replicating scope folding (global > customer
> product, admin>editor>viewer, dept grants) in JS would duplicate authz and
inevitably drift. A server field is DRY, correct, and additive (no breaking
change). Bypass-admin returns `admin`, preserving the dev default for free.

### D2 — Gate by role tier with two small helpers
Add `roleAtLeast(role, min)` over the order `viewer < editor < admin`.
Affordance tiers:
- **editor+** (`roleAtLeast(role,'editor')`): upload/replace/add/delete file,
  layer/view pick, 開始編輯 (lock), 畫押 (sign-off), 新增版本, Rule Check,
  Save Match, 範本 manage, template create, Upload Rule JSON.
- **admin only** (`role==='admin'`): 解除畫押 (unsign) — mirrors the
  server's admin-only unsign.
- **viewer+** (always shown): 開啟 viewer, 查看結果, 比較版本, version
  switcher, Scan All, Measure, Layers, Rules, pan/zoom/inspect, downloads.

### D3 — Hide write controls, don't disable-grey them
For a `viewer` product the write controls are **omitted** (not rendered
disabled), so the card/toolbar reads as a clean read-only surface rather
than a row of greyed buttons. Empty file slots render a plain "（唯讀)"
placeholder instead of the clickable "＋ 拖放或點擊上傳". Exception: the
version-bar 畫押/新增版本 group — keep the bar, drop the buttons. This
matches the existing signed-off (frozen) rendering, which already hides
write controls and shows a badge.

### D4 — A single read-only signpost per surface
Dashboard: a small "唯讀" chip in the product-card header (next to the
name) when `effective_role==='viewer'`. Viewer: a "唯讀" chip in the header
(reuse the signed-badge styling family). This explains the missing controls.

### D5 — Defense-aligned, not defensive
The role field is advisory for UX only. If it is stale (e.g., a grant was
revoked mid-session), the server still 403/423s and the existing
`handleSignedOff409`-style handling surfaces it. We keep all 403 fallbacks.

## Risks / Trade-offs

- **[Client gate drifts from a server edge rule]** (e.g., `template_editor_guard`
  resolves product via template, a nuance the product-level role can miss)
  → Mitigation: gate coarsely by the product's `effective_role` (same value
  the guard's role floor uses) and keep the 403 fallback as the real stop.
  Worst case is a control shown that still 403s — i.e., today's behavior.
- **[Touching `canvas.js` risks the gated viewer core]** → Mitigation: limit
  viewer edits to (a) hiding/omitting toolbar write buttons and (b) the
  `#edit-lock-slot` content. No change to canvas, class toolbar, or event
  wiring. Verify via `SMDR2_DEV_RESOLVE_GRANTS` impersonation + screenshots.
- **[Viewer needs an extra fetch for its product role]** → Mitigation: one
  cached `/api/products/{product_id}` call at viewer boot (already a cheap,
  cached read); or fold the role into the file payload the viewer loads.
  Decide in tasks; prefer the existing payload if it carries product context.
- **[Stale role after grant change within an open tab]** → Accepted: matches
  current behavior for other state; a reload refreshes; server stays correct.

## Migration Plan

Additive only — no migration. The `effective_role` field is new and ignored
by existing consumers. Rollback = revert the frontend gating; the API field
is harmless if left. No data or schema changes.

## Open Questions

- Does the viewer's existing bootstrap payload already include product
  context we can extend with `effective_role`, or do we add a
  `/api/products/{id}` fetch? (Resolve in tasks by reading `canvas.js`
  boot.)
- Should `editor` see 解除畫押 disabled-with-tooltip rather than hidden, to
  hint "admin only"? Default: hide (consistent with D3).

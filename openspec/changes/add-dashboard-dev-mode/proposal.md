## Why

Engineers debugging the DRC handoff pipeline (or auditing what SMDR2
actually emits) keep asking how to grab a single file's Match JSON or
the whole product's DXF+JSON bundle off a running instance. Today the
only paths are `curl` against undocumented endpoints, or asking a
maintainer. That friction lives on the dashboard — the natural place
to surface "show me the raw artifacts for this thing" — and we just
shipped the DRC bundle endpoint that makes the whole-product case
trivial. The same backend already returns per-file Match JSON via
`GET /api/files/{id}/match-json`, so neither button needs new server
code. Wrap both in a single "developer mode" toggle so non-dev users
don't see the clutter and dev users keep their clicks one toggle away.

## What Changes

- Add a "Developer Mode" toggle to the top of the dashboard
  (alongside the existing header / product-list controls). Toggle
  state SHALL persist in `localStorage` so a reload doesn't snap dev
  users back to the default view.
- When dev mode is ON:
  - Every role-attached file row (already rendered per product card)
    grows a "Download Match" button. Clicking it fetches
    `GET /api/files/{file_id}/match-json` and triggers a client-side
    download as `match-<file_id>.json`. Disabled / hidden when the
    file has no saved Match JSON yet.
  - Every product card with at least one role-attached file grows a
    "Download All Match" button. Clicking it hits
    `GET /api/products/{product_id}/drc-bundle` (the endpoint we
    shipped in the `add-drc-bundle-export` change) and saves the
    response stream as `drc-bundle-<product_id>.zip`. Disabled when
    the product is not yet `ready_for_rule_check`.
- When dev mode is OFF, both new buttons SHALL NOT render at all
  (no greyed-out clutter; mainline users see the dashboard exactly
  as it is today).

## Capabilities

### New Capabilities
- `dashboard-ui`: defines dashboard-level UI behaviors that don't fit
  inside per-feature specs — starting with the developer-mode toggle
  and the dev-only download affordances. Sister to `viewer-ui`.

### Modified Capabilities
<!-- None — the underlying endpoints already exist; no requirement
     change in design-rule-checking or product-files. -->

## Impact

- **New code**: dashboard frontend (`app/static/dashboard.js` +
  `app/templates/dashboard.html` + small CSS in `style.css`). No
  Python changes.
- **No backend changes**: both downloads ride the existing endpoints
  (`GET /api/files/{id}/match-json`, `GET /api/products/{id}/drc-bundle`).
- **Tests**: minimal — the file system isn't touched and the new
  affordances are pure UI. A FastAPI `TestClient` smoke for the
  existing endpoints already covers the backend; for the JS, we add
  a lightweight integration check via an existing testing approach
  if one exists, or skip and rely on manual smoke.
- **Persistence**: one `localStorage` key (`smdr2.dashboard.devMode`).
- **No security implications**: the dev mode is a UI affordance, not
  an auth gate; the endpoints are equally accessible whether dev
  mode is on or off (SMDR2 is single-tenant on a trusted LAN, per
  the design notes in `add-drc-bundle-export/design.md`).

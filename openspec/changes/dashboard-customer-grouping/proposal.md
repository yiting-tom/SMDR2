## Why

The dashboard (`/`) renders a flat list of product cards (`dashboard.js`
`renderProducts`, see the "flat list — no customer/library grouping" comment).
Customer is now a first-class grouping above product (`products.customer_id`,
migration `0004`, → the `customers` table), but the dashboard shows only the
product name — the operator can't see or organise products by customer. The
product-name search is in-memory only (lost on reload/navigation), there is no
customer filter, and scroll position is discarded whenever the operator opens a
product and returns, forcing a re-scroll on a long list.

## What Changes

- **`GET /api/products` exposes the resolved customer name.** Each product
  object gains a `customer` field (the display name from the `customers` table
  via `AUTH_STORE.get_customer(customer_id)`), alongside the existing
  `customer_id`. Resolving server-side (mirroring the DRC manifest) avoids a
  client-side join against `/api/customers` and a render race.
- **Dashboard groups products by customer.** Products render under a header per
  customer (name + product count), sorted by customer name. Each group is
  **collapsible**; fold state persists in `localStorage` so collapses survive
  reload/navigation.
- **Dashboard gains a customer filter + persisted text search.** A customer
  filter narrows the list to selected customers; the existing product-name text
  search is kept. **Both** filter selections persist in `localStorage` and are
  restored on load.
- **Dashboard restores scroll position.** On returning to `/` after navigating
  away (e.g. into the viewer and back), the product list scrolls back to where
  it was. Scroll position persists in `sessionStorage` (per-tab, ephemeral) and
  is restored after the async product render completes, then cleared.

## Capabilities

### New Capabilities

- `dashboard-ui`: the dashboard product-list presentation — grouping products by
  customer with collapsible, fold-persisted groups; a customer filter plus a
  persisted product-name search (both in `localStorage`); and scroll-position
  restoration across navigation (`sessionStorage`).

### Modified Capabilities

- `product-files`: `GET /api/products` (and `GET /api/products/{id}`) additively
  expose the product's resolved `customer` display name, not just `customer_id`.

## Impact

- **Code**:
  - `app/main.py` `list_products` / single-product read — attach `customer`
    name (resolved via `AUTH_STORE.get_customer`), or `app/products.py`
    `Product.to_dict` if resolution is wired there.
  - `app/static/dashboard.js` — group-by-customer render, collapsible groups
    (localStorage fold), customer filter + persisted text search (localStorage),
    scroll save/restore (sessionStorage).
  - `app/templates/dashboard.html` — customer-filter control in the toolbar;
    group/header markup hooks.
  - `app/static/style.css` — group header + collapsed-state styling.
- **API**: `/api/products` payload gains `customer` (additive; no removals). No
  new endpoints (`/api/customers` already exists for the filter's option list).
- **Storage keys (frontend)**: new `localStorage` keys for filter state and
  group fold state; a `sessionStorage` key for dashboard scroll position. Follows
  the existing JSON-set persistence convention.
- **Tests**: `/api/products` includes the resolved `customer` name (including
  the `uncategorized` → `未分類` fallback). Frontend grouping/filter/scroll is
  manual-verify (no JS test harness in repo) unless a harness is added.
- **Relationship**: complements `re-add-customer-fields-to-drc-manifest` (same
  customer-name resolution, different surface) and `improve-gui-ux` (dashboard
  dialogs; no overlap with grouping/filter/scroll).

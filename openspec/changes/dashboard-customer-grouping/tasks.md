## 1. API: expose the customer name (backend)

- [x] 1.1 `app/main.py` `list_products` (and the single-product read
  `GET /api/products/{id}`): attach `customer` = `AUTH_STORE.get_customer(
  p.customer_id).name`, falling back to the `customer_id` string when no row
  matches. Keep `customer_id` as-is. (Resolve in the endpoint, or thread the
  name into `Product.to_dict` — keep `Product` storage-only if it has no auth
  access.)
- [x] 1.2 Avoid an N+1: fetch customers once (`AUTH_STORE.list_customers()` →
  id→name map) and resolve from the map in the product loop.

## 2. Test: API customer name

- [x] 2.1 Test `GET /api/products` includes `customer` equal to the referenced
  customer's name; an `uncategorized` product resolves to `未分類`; a product
  whose `customer_id` has no row falls back to the id string (non-empty).
- [x] 2.2 `GET /api/products/{id}` includes `customer`.

## 3. Frontend: group products by customer

- [x] 3.1 `app/static/dashboard.js` `renderProducts`: bucket the (filtered)
  products by `p.customer`, render one group section per bucket with a header
  (customer name + shown count). Sort groups by name; `未分類` last. Preserve
  existing within-group order. Replace the flat-list loop + its comment.
- [x] 3.2 `app/templates/dashboard.html` / `app/static/style.css`: group header
  + group container markup and styling (header, count, collapsed chevron).

## 4. Frontend: collapsible groups with persisted fold

- [x] 4.1 Header click toggles the group collapsed/expanded. Track collapsed
  customer ids in a `Set`; persist to `localStorage` (JSON array, `try/catch`,
  matching the canvas.js convention). Default expanded when absent.
- [x] 4.2 Apply persisted fold state on first render.

## 5. Frontend: customer filter + persisted text search

- [x] 5.1 Add a customer filter control in the toolbar (`dashboard.html`),
  populated from `/api/customers` (reuse `loadCustomers`). Empty selection = all.
- [x] 5.2 Persist `{ text, customers[] }` to `localStorage`; restore on load
  before the first render; wire both the existing `#product-search` and the new
  customer filter through it. Apply filters before bucketing (task 3.1).
- [x] 5.3 Empty-state when filters match nothing, with a "clear filters" control
  that resets both filters and re-renders.

## 6. Frontend: scroll-position restore

- [x] 6.1 Save the dashboard scroll offset to `sessionStorage` when navigating
  away (on `pagehide`, and on the click that opens a product/version).
- [x] 6.2 After `renderProducts()` completes on load, restore the saved offset in
  a `requestAnimationFrame`, clamped to the scrollable height, then clear the
  key. No saved value → start at top.

## 7. Verify

- [x] 7.1 `pytest -q` green (API field + no regression).
- [x] 7.2 Real-app browser verify (Playwright against a running server, 2
  customers + uncategorized seeded): `/api/products` returns resolved `customer`
  names live; groups render **Alpha(2) → Beta(1) → 未分類(1)** (uncategorized
  last) with counts; collapsing Alpha persists to localStorage and **survives a
  reload**; the customer filter narrows to Beta (count `1 / 4`), persists, and
  **restores on the next load**; a no-match text filter shows the empty state
  with a working **清除篩選** button that resets both filters; the real
  `pagehide` listener saves `main.dashboard-main` scrollTop (250) and restore
  clamps an oversized value to max. Zero console errors.

## 8. Archive

- [ ] 8.1 `openspec validate dashboard-customer-grouping --strict`.
- [ ] 8.2 `/opsx:archive dashboard-customer-grouping` after verification.

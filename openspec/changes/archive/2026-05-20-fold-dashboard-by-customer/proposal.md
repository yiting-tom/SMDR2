## Why

The dashboard at `GET /` renders every product card in one flat list
ordered only by creation time. As the library / customer roster
grows this becomes hard to scan: the engineer has to read product
names + the `<span class="product-library">` chip on every card to
locate the right customer. The `library` dimension already exists
and is now also surfaced in the DRC manifest as `customer` — the
dashboard should reflect the same grouping.

## What Changes

- Group dashboard product cards by `library_id` (the customer
  dimension) into foldable sections. Each section has a header
  reading `Customer Name (N products) ▾` (collapsed) / `▸`
  (expanded), clickable across the full header width.
- **Default state: all sections folded.** First-page-load shows
  customer names only; the engineer opens what they need. Subsequent
  state persists in `sessionStorage` under
  `smdr2.dashboard.foldedCustomers` (value = JSON array of folded
  `library_id` strings).
- **Empty customers are hidden.** A library with 0 products does NOT
  render a section at all — no empty headers, no noise from
  long-defunct libraries.
- The product card itself, all its actions (Rule Check, Download All
  Match, slot drop / Replace / Delete, etc.), and the existing
  library-bar / new-library controls at the top of the page are
  unchanged.
- Scope is purely presentational: `app/static/dashboard.js` for the
  grouping/fold logic + `app/static/style.css` for the section
  styling. No API changes.

## Capabilities

### New Capabilities
- (none)

### Modified Capabilities
- `viewer-ui`: the dashboard SHALL render product cards grouped into
  foldable per-customer sections; default-folded; empty customers
  hidden; fold state persisted in sessionStorage.

## Impact

- Code: `app/static/dashboard.js` (renderProducts), `app/static/style.css`.
- Specs: `viewer-ui`.
- Tests: no automated coverage planned — the JS test harness only
  covers pure functions in `measure_core.js`. Behavior is verified
  by a manual smoke check; the grouping is a layout transform with
  no protocol surface.
- Risks: an engineer who is used to scanning the flat list may be
  briefly disoriented by the default-folded behavior. Mitigated by
  the loud `▾` chevron and the per-customer count in the header.

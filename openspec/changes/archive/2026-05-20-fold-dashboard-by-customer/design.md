## Context

`renderProducts()` in `app/static/dashboard.js` clears `$list` and
appends one `<section class="product-card">` per product. Products
are returned from `GET /api/products` in `created_at DESC` order
(ish — see `PRODUCT_STORE.list_all`). The library bar at the top
already lists every library via `loadLibraries()`, so the customer
roster is known to the page; nothing extra needs fetching.

`sessionStorage` is the project's standing convention for
short-lived dashboard preferences (see
`smdr2.dashboard.selectedLibrary` already in use). The library
modal in the viewer also uses sessionStorage for fold state, so
this is the canonical pattern.

## Goals / Non-Goals

**Goals:**
- Engineers can find a customer's products in O(customers) glances
  instead of scanning every product card.
- The page loads quickly with a tight initial view (folded
  headers), then expands on demand.

**Non-Goals:**
- Search / filter UI. Folding is enough for the current roster
  size; search becomes interesting only once a single customer has
  20+ products.
- Cross-customer sorting toggles (recent activity, name, etc.). One
  ordering (alphabetical customer name, then existing
  product-order inside) is enough; revisit if it ever feels wrong.
- A "expand all" / "collapse all" toolbar button. Easy to add later
  if engineers ask; not in the first cut.

## Decisions

### Render grouping inside `renderProducts`, not via a new endpoint

The page already has every product and every library client-side.
Grouping is a pure transform over the in-memory `products` array
plus `libraries`. No backend work, no extra round-trip.

### Customer ordering: alphabetical by name, with a stable tiebreak

Each section header is keyed by the library's `name`. Customers are
sorted case-insensitively by name; ties (and missing names) fall
back to `library_id` as the tiebreak so the order is deterministic.
The library bar at the top already lists libraries in API order
(creation time) — the dashboard's grouped order doesn't need to
match that bar because the two surfaces have different jobs (the
bar picks an upload target; the sections organize existing work).

### Fold state stored as JSON array of folded library_ids

Storing the *folded* set rather than the *expanded* set means new
libraries appear folded by default (matching the requirement),
without having to read sessionStorage at library-creation time. The
key `smdr2.dashboard.foldedCustomers` namespaces it under the
existing `smdr2.dashboard.*` family.

On the first page load there is no key in sessionStorage; the code
treats "no entry" as "every customer folded" and renders
accordingly. After the first fold/unfold action the array is
written.

### Empty libraries hidden, not greyed-out

A library with 0 products contributes nothing to dashboard
ergonomics; rendering it just adds noise. The library bar at the
top still exposes every library for upload-target selection, so
empty libraries remain discoverable for the create-new-product flow.

### Header is a single clickable element

`<header class="customer-section__header" role="button" tabindex="0"
aria-expanded="false">` with a chevron + name + count. Click /
Enter / Space toggles fold state. No separate hit-targets — the
whole header is the affordance, matching the library-modal fold
pattern in the viewer.

## Risks / Trade-offs

- **Risk — default-folded hides ongoing work.** An engineer who
  uploaded a DXF five minutes ago opens the dashboard and sees
  nothing under the (still-folded) section.
  → Mitigation: the per-section count in the header (`Customer
  Name (3 products) ▾`) tells the engineer how many products are
  inside; the chevron is loud. The cost of re-clicking the section
  once is small compared to the everyday scan cost of a flat list.

- **Risk — fold state desyncs from library deletion.** A folded
  library_id stays in sessionStorage after the library is deleted.
  → Mitigation: the renderer ignores library_ids that no longer
  resolve, so stale ids are harmless. We don't actively prune them;
  the entry rotates out when the session ends.

- **Risk — alphabetical ordering surprises an engineer who expects
  the library-bar order.**
  → Mitigation: the two views have different jobs. If feedback
  asks for parity, we can switch to creation-time ordering in a
  one-line change. Worth raising in the smoke check.

## Migration Plan

None — pure UI change. First page load after deploy reads no
`smdr2.dashboard.foldedCustomers` entry and renders the new
default-folded view. Existing flat-list muscle memory is replaced
by the section headers and chevron.

Rollback: revert the `dashboard.js` + `style.css` changes. No data
state to undo.

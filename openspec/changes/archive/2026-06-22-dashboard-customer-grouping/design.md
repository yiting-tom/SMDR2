## Context

`GET /api/products` returns each product via `Product.to_dict()` — `id`, `name`,
`created_at`, `customer_id` — plus `versions[]` and `effective_role`. The
customer **name** lives in a separate `customers` table (`AUTH_STORE`, columns
`id, name, created_at`), reachable via `AUTH_STORE.get_customer(id)` and listed
by `GET /api/customers`. `dashboard.js` already fetches `/api/customers` for the
new-product modal dropdown (`loadCustomers`), with a `uncategorized → 未分類`
fallback.

`renderProducts` builds a flat list, filtering by an in-memory `filterText` that
is never persisted. There is no scroll-restoration code anywhere in
`app/static/`. The convention for persisted set-valued UI state is
`canvas.js`'s `sessionStorage` JSON-array pattern
(`new Set(JSON.parse(store.getItem(KEY) ?? "[]"))` / `JSON.stringify([...set])`),
all wrapped in `try/catch`.

## Goals / Non-Goals

**Goals:**
- Show each product's customer name and group products under collapsible,
  fold-persisted customer headers.
- Filter by customer and by product-name text, with both persisted across
  reload/navigation.
- Return to the dashboard at the previous scroll position after navigating away.

**Non-Goals:**
- Customer CRUD from the dashboard (stays admin-only via `/api/customers`).
- Server-side filtering/pagination — the list stays client-rendered; filtering,
  grouping, and persistence are all client-side except the one added name field.
- Re-introducing library grouping (libraries are version internals now).

## Decisions

### D1 — Resolve the customer name server-side, attach to the product payload

`list_products` (and the single-product read) attach `customer` = the resolved
display name, via `AUTH_STORE.get_customer(p.customer_id)`. When the customer row
is missing (deleted customer still referenced by a product), fall back to the id
string so the field is always a non-empty human label. The `uncategorized` row
is named `未分類` and resolves normally.

*Alternative — client-side join* (`/api/products` × `/api/customers` in JS):
rejected. It adds a fetch-ordering dependency (group headers can't render until
both resolve), duplicates the DRC manifest's existing server resolution, and
mishandles a product whose customer isn't in the caller-visible customer list.
One additive field on the product is the single source of truth.

### D2 — Group + sort in the client after fetch

`renderProducts` buckets `products` by `customer` name, renders one collapsible
group per bucket. Groups sort by customer name (locale-aware); the `未分類`
bucket sorts **last** so real customers lead. Within a group, the existing
order (`created_at` desc, as returned) is preserved. The customer filter and
text filter are applied **before** bucketing, so empty groups don't render and
the per-group count reflects what's shown.

### D3 — Persisted UI state: localStorage for preferences, sessionStorage for scroll

- **Fold state** (`localStorage`, JSON array of collapsed customer ids): a stable
  preference that should survive across days, default expanded.
- **Filter state** (`localStorage`): `{ text: string, customers: string[] }` —
  `customers` empty ⇒ all shown. Restored on load before the first render.
- **Scroll position** (`sessionStorage`, a single number): per-tab and
  ephemeral on purpose — a scroll offset from a previous day/tab is meaningless
  and restoring it would be jarring. localStorage would leak stale offsets across
  sessions.

All three follow the existing `try/catch` JSON convention; storage failure
degrades to in-memory behaviour (today's behaviour), never throws.

### D4 — Scroll save/restore timing

The dashboard list renders **after** an async `/api/products` fetch, so the page
is short at first paint and a naive synchronous restore scrolls nowhere. Save the
current scroll offset when leaving the page (navigating into a product/version —
a `pagehide`/`beforeunload` handler, plus on the click that navigates). On load,
after `renderProducts()` completes, restore in a `requestAnimationFrame` and
clamp to the scrollable height; then clear the key so a manual reload-at-top
isn't overridden later. Filter changes that shorten the list simply clamp.

## Risks / Trade-offs

- **Deleted/!visible customer referenced by a product** → name resolves to None.
  → Mitigation: D1 falls back to the id string; never renders a blank header.
- **Many customers → many group headers** → collapsible groups + persisted fold
  keep it manageable; default-expanded keeps first-run discoverable.
- **Scroll restore races the async render** → restore in rAF after render, clamp
  to content height, restore once then clear (D4).
- **Filter persisted to a state that hides everything** (e.g. customer filter to
  a now-deleted customer) → render the empty-state with a visible "clear filters"
  affordance so the operator is never stuck on a blank list.
- **localStorage disabled/quota** → all access `try/catch`-wrapped; falls back to
  non-persisted in-memory state.

## Migration Plan

Additive only. The `customer` field is new and optional for any existing client;
the frontend is replaced wholesale on deploy. No schema change, no data
migration. Rollback = revert the frontend + the one API field.

## Open Questions

- Should the customer filter be multi-select chips or a dropdown? Left to
  implementation; the spec only requires "narrow to selected customers" and
  persistence, not the specific control.

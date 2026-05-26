## 1. JS — grouping + fold state

- [x] 1.1 In `app/static/dashboard.js`, add a `FOLD_KEY = "smdr2.dashboard.foldedCustomers"` module constant and two helpers: `loadFoldedSet()` (returns a `Set<string>` from sessionStorage; empty set means "no record yet", but renderer SHALL treat that as "all folded") and `saveFoldedSet(set)`.
- [x] 1.2 Add `groupProductsByLibrary(products)` that returns an array of `{library, products}` objects, sorted by `library.name` case-insensitive with `library.id` tiebreak. Drop libraries with zero products.
- [x] 1.3 Rewrite `renderProducts()`: instead of looping `products` directly, loop the grouped output. For each group build a `<section class="customer-section">` containing a clickable `<header>` (role=button, tabindex=0, aria-expanded) and a `<div class="customer-section__body">` holding the product cards built by the existing `productCard(p)` helper.
- [x] 1.4 Initial fold state: if `smdr2.dashboard.foldedCustomers` has no value in sessionStorage, treat every library as folded. If it has a value, treat exactly the listed ids as folded; everything else expanded.
- [x] 1.5 Wire the header to click + keyboard (Enter / Space, preventDefault on Space): toggle the section's `data-folded` attribute, swap chevron text, flip `aria-expanded`, and persist the new folded set via `saveFoldedSet`.
- [x] 1.6 Make sure the empty-state path (`!products.length`) still surfaces the original "no products yet" message — the new grouping wrapper SHOULD NOT render an empty `<section>` for no products.

## 2. CSS

- [x] 2.1 Add styles for `.customer-section`, `.customer-section__header`, `.customer-section__header[aria-expanded="false"]` (collapsed look), `.customer-section__body`, and the folded-vs-expanded chevron. Use the existing palette (dashed borders / cyan accent / `#2a3340` base) so the section matches the rest of the dashboard.
- [x] 2.2 Header SHALL show `cursor: pointer` and a hover state that telegraphs interactivity.
- [x] 2.3 When folded, the body SHALL be `display: none` (not just visually hidden) so the page reflows tightly.

## 3. Sync + smoke check

- [x] 3.1 Run `openspec validate fold-dashboard-by-customer`.
- [ ] 3.2 Manual smoke: load `/`, confirm every customer section is folded on first paint, click a header → cards appear + chevron flips, reload (same session) → fold state persists, close+reopen the tab (new session) → folded-by-default restored.
- [ ] 3.3 Manual smoke: create a brand-new empty library via the top-bar New Library button, confirm no section appears for it until at least one product binds to it.

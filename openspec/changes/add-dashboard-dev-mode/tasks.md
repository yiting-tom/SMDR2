## 1. Dev-mode toggle infrastructure

- [x] 1.1 In `app/static/dashboard.js`, add module-top constants `DEV_MODE_KEY = "smdr2.dashboard.devMode"` plus `getDevMode()` / `setDevMode(boolean)` helpers (read/write localStorage; treat any value other than `"1"` as OFF).
- [x] 1.2 Add a `downloadAsFile(blob, filename)` helper that constructs a transient `<a download>`, clicks it, and revokes the object URL — reused by both per-file and per-product download paths.
- [x] 1.3 Wire a `<button id="dev-mode-toggle">` into the dashboard top toolbar in `app/templates/dashboard.html` (placement: alongside the existing header controls). The button's text + `aria-pressed` reflect the current state.
- [x] 1.4 On dashboard boot, read the localStorage key, set the toggle's visible state, and add a click handler that flips the key and re-renders the product list so every dev-only affordance materialises / disappears in lockstep.

## 2. Per-file "Download Match" button

- [x] 2.1 In `dashboard.js`'s file-row rendering, conditional-render a "Download Match" button only when both `getDevMode() === true` AND `file.match_saved === true`. Hide entirely otherwise (no greyed-out ghost).
- [x] 2.2 Click handler: `fetch(\`/api/files/${file.id}/match-json\`)`, await `r.blob()`, call `downloadAsFile(blob, \`match-${file.id}.json\`)`. Surface fetch errors via the existing dashboard status line (`$status.textContent = ...`); do NOT silently swallow.
- [x] 2.3 Style the button (in `app/static/style.css`) to match the other small per-file actions; no new color tokens.

## 3. Per-product "Download All Match" button

- [x] 3.1 In `dashboard.js`'s product-card rendering, conditional-render a "Download All Match" button only when `getDevMode() === true`. Disable it (with `title` explaining the precondition) when `product.ready_for_rule_check === false`.
- [x] 3.2 Click handler: `fetch(\`/api/products/${product.id}/drc-bundle\`)`, await `r.blob()`, call `downloadAsFile(blob, \`drc-bundle-${product.id}.zip\`)`. Same error-surfacing pattern as the per-file button.
- [x] 3.3 Style the button to sit beside the existing "Rule Check" button on the card; reuse the existing `.rule-check-btn` style block if the visual treatment matches (otherwise add a sibling style).

## 4. Tests

- [ ] 4.1 Manual smoke: open the dashboard, default state shows no new buttons. Toggle dev mode — buttons appear. Click per-file download — JSON saves. Click per-product download — zip saves. Reload — toggle stays ON, buttons stay visible.
- [ ] 4.2 (Optional, if a browser test harness materialises later) Add a Playwright or Cypress check covering the toggle's persistence + button-presence-by-state. Skip for now if no JS test infra is in place — the affordance is small and the underlying endpoints already have backend test coverage.

## 5. Spec sync

- [x] 5.1 Run `openspec validate add-dashboard-dev-mode --strict` after implementation — expect clean validation.
- [ ] 5.2 At archive time, ensure the new `dashboard-ui` capability spec is promoted to `openspec/specs/dashboard-ui/spec.md` and its three requirements (toggle, per-file button, per-product button) become the initial body of that spec.

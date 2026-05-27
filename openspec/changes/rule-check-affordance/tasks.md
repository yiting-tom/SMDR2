## 1. Dashboard rendering (`app/static/dashboard.js`)

- [x] 1.1 Inside `showRuleResults` (`app/static/dashboard.js:911` area), introduce a small helper near the function top: `function isLocatable(sub) { return Boolean(sub && (sub.from || sub.to || sub.tol)); }`. Apply it to every sub-rule the modal renders so the locatable / text-only split is computed in exactly one place.
- [x] 1.2 In the `subs.forEach((sub, idx) => …)` block (around `dashboard.js:937`), branch on `isLocatable(sub)` BEFORE deciding what HTML to emit for the row:
  - If the role's file does not resolve, keep the existing `<span class="no-file">${sub.part} not uploaded</span>` branch unchanged (no icon, no link).
  - Else if `isLocatable(sub)` is true: prefix the row text with `🎯 ` (followed by a non-breaking space) and render the existing `<a class="view-link" href="/viewer/${file.id}?rule=…&idx=…">View in ${sub.part} →</a>` link.
  - Else (file exists but sub-rule is text-only): prefix with `ℹ ` and **omit** the `View in <PART> →` link. Add a CSS class `subrule-text-only` to the `<li>` so the dimmed style applies.
- [x] 1.3 Header chip: between the existing `<span class="text">${escapeHtml(rule.text || "")}</span>` and the closing `</header>`, append a `<span class="rescaled-pill">…</span>` whose text is computed as: `rule.rules.length === 0 ? "ℹ no locator" : \`🎯 ${nLocatable} · ℹ ${nTextOnly}\`` — `nLocatable` and `nTextOnly` derived from a single pass over `rule.rules` using `isLocatable`. The chip's `title` attribute SHALL state `"<N> locatable · <M> text-only sub-rule(s)"` (or `"no sub-rules emitted"` for the empty case) so hover gives the long-form explanation.
- [x] 1.4 Make sure the icon prefix is rendered as text inside the existing `<span class="text">` block (or a new sibling span) — do NOT escape the emoji into HTML entities. Verify by inspecting the rendered DOM in a browser: the `🎯` / `ℹ` should appear inline before `${sub.text}`.

## 2. CSS (`app/static/style.css`)

- [x] 2.1 Add a `.subrule-text-only` rule that lowers row opacity (around `opacity: 0.7`) and removes any hover-shading that the existing sub-rule rows may apply. Keep cursor as default (NOT `cursor: pointer`), so the row visibly reads as non-interactive.
- [x] 2.2 Verify the existing `.rescaled-pill` style absorbs the new header chip without modification (same chroma, same margin-left). If the chip looks visually cramped against the rule name, add a small `margin-left` bump scoped via a new modifier class (e.g. `.rescaled-pill--inline-chip`) — only if needed; the default style is the preferred path.

## 3. Manual verification

- [ ] 3.1 Synthesise three rules in a local rule-check result to exercise every branch:
  - Rule A: 3 sub-rules, each with non-null `from` — expect three `🎯` rows with clickable links and chip `🎯 3 · ℹ 0`.
  - Rule B: 2 sub-rules, all handle fields null (manually-edited test payload) — expect two `ℹ` rows, dimmed, no link, chip `🎯 0 · ℹ 2`.
  - Rule C: 1 locatable + 1 text-only sub-rule — expect mixed icons, chip `🎯 1 · ℹ 1`.
  - Rule D: `rules: []` — expect the existing "No sub-rules emitted" row plus chip `ℹ no locator`.
- [ ] 3.2 Click each `🎯` row and confirm the viewer opens with the correct highlight (no regression in the existing `?rule=&idx=` flow).
- [ ] 3.3 Hover the header chip and confirm the long-form title text appears (`"<N> locatable · <M> text-only sub-rule(s)"` or `"no sub-rules emitted"`).
- [ ] 3.4 With a real rule-check result on a production product (all sub-rules locatable in well-formed data), confirm the modal looks unchanged in the common case aside from the `🎯` prefix and the chip — no layout shift, no link regression.

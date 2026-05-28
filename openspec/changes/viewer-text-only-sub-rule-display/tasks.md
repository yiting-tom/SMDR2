## 1. Code change

- [x] 1.1 In `app/static/canvas.js`, add a top-level helper `subRuleHasHandles(sub)` immediately above `renderSubRuleItem` that returns `true` when any of `sub.from`, `sub.tol`, `sub.to` is non-empty (treating `Array.isArray(sub.to) && sub.to.length === 0` as empty).
- [x] 1.2 In `renderSubRuleItem`, add an early-return branch at the top (after the `dataset` assignments, before `resolveSubRuleFile`): when `!subRuleHasHandles(sub)`, build an `<li class="text-only">` containing `.part`, `.sub-text`, and an empty third `<span>`, and return immediately — no nav-hint, no click handler.
- [x] 1.3 In `app/static/style.css`, add a `.text-only` modifier inside the `#rule-sidebar .subrules li` block: `cursor: default;`, `border-left-color: #2a3340;`, a `:hover` rule that pins the border / background back to the resting state, and a `.part` rule that mutes the colour to `#9aa5b1`.

## 2. Manual verification

- [ ] 2.1 **[USER]** Start the dev server. Pick a product whose rule-check already includes (or, with dev-mode JSON upload, can be made to include) at least one text-only sub-rule (`from`, `tol`, `to` all null).
- [ ] 2.2 **[USER]** Open that product's viewer, click `Rules`, and confirm for the text-only row:
  - the row reads `<part>  <text>` with no `(no file)` / `→ ... viewer` / `show` hint
  - the cursor over the row is the default arrow, not a pointer
  - hovering does not flash the cyan border / background
  - clicking does nothing — no navigation, no canvas overlay
- [ ] 2.3 **[USER]** Confirm a locatable row in the same product (any rule with a `from` handle) still renders its existing nav-hint and clickable behaviour (canvas highlight for same-role, navigation for other-role, `not-allowed` cursor for missing-file).
- [ ] 2.4 **[USER]** Hand-craft a URL `…/viewer/<file_id>?rule=<text-only-rule-name>&idx=<i>` and confirm: the sidebar opens, the row may be marked `.focused`, the canvas remains clean, and the browser console shows no errors.

## 3. Archive

- [ ] 3.1 After tasks 1 and 2 pass, run `/opsx:archive viewer-text-only-sub-rule-display` to fold the modified `viewer-ui` spec into the live spec and mark the change archived.

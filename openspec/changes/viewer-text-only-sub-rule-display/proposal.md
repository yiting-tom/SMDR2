## Why

The companion change `rule-json-accept-text-only-sub-rules` relaxed
the RuleChecking envelope to admit sub-rules that carry only `part`
+ `text` — informational rows, category headers, notes. That change
explicitly deferred viewer-side polish: today the viewer's rule
sidebar (`renderSubRuleItem` in `app/static/canvas.js`) still routes
text-only rows through the same interactive branches as
handle-bearing rows, ending up with a misleading `(no file)`
nav-hint and pointer cursor even though there is literally nothing
on the canvas to highlight. The dashboard modal already gained the
locatable / text-only distinction (`viewer-ui` spec § "Rule-check
modal distinguishes locatable from text-only sub-rules"); the
viewer sidebar should follow.

## What Changes

- **Viewer rule sidebar** (`app/static/canvas.js` `renderSubRuleItem`):
  detect sub-rules whose `from`, `tol`, and `to` are all empty
  (treating an empty `to` array as empty) and render them as
  non-interactive rows — `.part` + `.sub-text` only, no `.nav-hint`,
  no `click` handler, no routing through `resolveSubRuleFile`.
- **Sidebar CSS** (`app/static/style.css`): add a `.text-only`
  modifier under `#rule-sidebar .subrules li` that neutralises
  cursor + hover affordances and mutes the `.part` colour, so the
  row visually reads as a label, not a button.
- The three existing interactive states — `.same-role`,
  `.other-role`, `.missing-file` — keep their current rendering.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `viewer-ui`: adds a new requirement scoped to the viewer's
  rule-check sidebar, paralleling the existing dashboard-modal
  requirement. No existing requirement is changed.

## Impact

- **Code**: `app/static/canvas.js` (≈15 lines added — one helper,
  one early-return branch in `renderSubRuleItem`) and
  `app/static/style.css` (≈10 lines — one new modifier class).
- **APIs**: none. The rule-check JSON contract and the
  `?rule=&idx=` deep-link contract are unchanged.
- **Deep-link**: `?rule=X&idx=N` pointing at a text-only sub-rule
  is already a visual no-op (`drawFocusedSubRule` has nothing to
  draw); no extra guard required. The sidebar may still scroll the
  row into view and mark it `.focused`, which is harmless.
- **Dashboard rule-result modal**: out of scope — already speced and
  shipped under `2026-05-27-rule-check-affordance`.
- **Tests**: no Python test changes (UI-only). Manual verification
  in the viewer.
- **Operational / migration**: none. Existing rule-check JSON
  payloads render identically except that text-only rows now look
  inert instead of fake-clickable.

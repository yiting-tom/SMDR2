## Context

The viewer's rule-check sidebar lives entirely in
`app/static/canvas.js` (≈ lines 1508 – 1717) and is styled in
`app/static/style.css` (`#rule-sidebar .subrules` block, ≈ lines
374 – 503). Each sub-rule row is rendered by `renderSubRuleItem`,
which today resolves the target DXF file and pushes every row into
one of three interactive buckets:

| class           | when                                                | hint                  | click action                            |
|-----------------|-----------------------------------------------------|-----------------------|-----------------------------------------|
| `.same-role`    | resolved file is `FILE_ID` (this viewer)            | `show`                | `focusSubRule()` — highlight on canvas  |
| `.other-role`   | resolved file is a sibling DXF                      | `→ <part> viewer`     | `location.href = /viewer/<id>?rule=…`   |
| `.missing-file` | no file resolves                                    | `(no file)`           | none (cursor `not-allowed`)             |

After the companion change `rule-json-accept-text-only-sub-rules`,
sub-rules can also arrive with **all** of `from`, `tol`, and `to`
null — informational rows carrying only `part` + `text`. These rows
flow through the same code path and currently fall into
`.missing-file` (when no file resolves) or `.same-role` /
`.other-role` (when a file does resolve), in both cases displaying a
nav-hint and pointer cursor that promise a highlight the canvas
will never actually draw — `drawFocusedSubRule` has no handles to
resolve.

The dashboard's rule-result modal already gained the
locatable / text-only distinction (`viewer-ui` spec §
"Rule-check modal distinguishes locatable from text-only
sub-rules", shipped in `2026-05-27-rule-check-affordance`). This
change applies the same distinction to the viewer sidebar.

## Goals / Non-Goals

**Goals:**

- A sub-rule with no handle fields renders in the sidebar as a
  plain, non-interactive row — visible, but obviously not a button.
- Existing handle-bearing sub-rules render exactly as today, in
  their existing three states.
- Behaviour is identical whether the no-handle sub-rule lives in a
  rule whose role's file is present, missing, or sibling — the row
  is text-only either way.

**Non-Goals:**

- Glyph-prefixing rows the way the dashboard modal does
  (`🎯` / `ℹ`). Keep the viewer sidebar visually quiet — the
  cursor + colour treatment is enough signal in context.
- Reflowing the row grid. The existing three-column grid
  (`3ch 1fr auto`) stays; the third column simply becomes an empty
  span for text-only rows so the grid alignment matches its
  neighbours.
- Suppressing the `.focused` highlight when a no-handle row is
  reached via `?rule=&idx=` deep-link. The sidebar's highlight is
  harmless; the canvas no-op is already correct.
- Changes to the dashboard modal, the rule-check JSON envelope, the
  validator, the deep-link contract, or `focusSubRule` /
  `drawFocusedSubRule`.

## Decisions

### 1. Detect text-only at row-render time, not at sidebar-load time

A `subRuleHasHandles(sub)` helper checks `sub.from`, `sub.tol`, and
`sub.to` (treating an empty array as empty, since the
`rule-check-multi-to` change made `to` polymorphic). The check
runs inside `renderSubRuleItem` and gates an early-return branch.

**Alternative considered:** classify each sub-rule when the
rule-check JSON is fetched, attach a `kind: "text-only" | "locatable"`
field, and branch on that. Rejected — the JSON envelope is the
shared contract with the external rule team, and we don't want the
viewer to materialise a derived field on it. Classification at
render time keeps the JSON untouched and the helper trivially
testable by eye.

### 2. Helper name and definition

```js
function subRuleHasHandles(sub) {
  if (sub.from) return true;
  if (sub.tol) return true;
  if (Array.isArray(sub.to)) return sub.to.length > 0;
  return !!sub.to;
}
```

Mirrors the dashboard modal's classifier semantics (line 1339-1343
of `viewer-ui/spec.md`): a sub-rule is locatable iff any of `from`,
`to`, or `tol` is non-null. The `Array.isArray` branch accommodates
the `to`-as-list multi-target case from `rule-check-multi-to`; an
empty list is treated as "no target", matching the rendering code
elsewhere in `canvas.js` (`drawFocusedSubRule`, lines 1156 – 1168).

### 3. Render branch is an early return, not an interleaved conditional

When `subRuleHasHandles(sub)` is false, `renderSubRuleItem` builds
a stripped-down `<li>` with the `.text-only` class and the
`.part` + `.sub-text` + empty third span — then returns immediately,
before any `resolveSubRuleFile` call, file-state class assignment,
nav-hint span, or click handler attachment.

**Alternative considered:** interleave `hasHandles` checks
throughout the existing function — skip the file resolution if
false, omit the hint if false, skip the click handler if false.
Rejected — the function is short and the two paths share almost no
behaviour, so an early return reads more cleanly than a function
threaded with conditionals.

### 4. CSS — neutralise hover and cursor, mute `.part` colour

```css
#rule-sidebar .subrules li.text-only {
  cursor: default;
  border-left-color: #2a3340;
}
#rule-sidebar .subrules li.text-only:hover {
  border-left-color: #2a3340;
  background: #131923;
}
#rule-sidebar .subrules li.text-only .part { color: #9aa5b1; }
```

The hover override pins the border / background back to the resting
state, since the base `#rule-sidebar .subrules li:hover` rule (style.css
line 485) would otherwise still flash the cyan tint. `.part`
mutes to `#9aa5b1` (the same dim grey used for `#rule-sidebar
#rule-sidebar-summary`), placing the row visually between
`.missing-file` (`#5d8aa8`, washed-out blue) and the active states
(green / orange). A single new class keeps the diff small and the
selector specificity matches the existing modifiers in this block.

### 5. Deep-link `?rule=&idx=` for a no-handle row is left as-is

The URL parameter handler (`focusSubRuleByKey`) sets
`focusedSubRule` and triggers `render()`. For a no-handle sub-rule,
`drawFocusedSubRule` has nothing to draw — its handle resolution
walks an empty set — so the canvas stays clean. The sidebar may
still apply `.focused` to the `.text-only` row, which is visually
harmless. No extra guard is added: a guard would be dead code
forced past today's already-correct no-op.

## Risks / Trade-offs

- [The `.text-only` modifier collides with another module's
  class name] → Mitigated: the selector is scoped to
  `#rule-sidebar .subrules li.text-only`, so even if `.text-only`
  is reused elsewhere the rules don't leak.

- [A future requirement wants the no-handle row to be clickable —
  e.g. clicking should expand a long `text` into a tooltip] →
  Mitigated: the early-return branch is a single block; adding a
  click handler later is a one-line change and the `.text-only`
  class can grow extra styles without breaking the existing
  modifiers.

- [Engineers who used `(no file)` rows as a debugging signal that
  "this rule's geometry isn't in any uploaded DXF" lose that hint
  when the geometry truly is missing **and** the sub-rule is
  text-only] → Accepted: the two conditions are independent and
  the dashboard modal already reports missing files separately
  via the existing `<PART> not uploaded` branch; the sidebar
  remains a quick-glance affordance, not a diagnostic surface.

## Migration Plan

None — frontend-only change, no data on disk and no API moves.
The first deploy that ships these two files takes effect on the
next viewer load; cached `rule_check.json` payloads on disk render
correctly with the new code without any rewrite.

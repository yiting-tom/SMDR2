## ADDED Requirements

### Requirement: Viewer rule-check sidebar distinguishes locatable from text-only sub-rules

The viewer's rule-check sidebar SHALL classify each sub-rule it renders as **locatable** or **text-only**. The implementation lives in `renderSubRuleItem` (`app/static/canvas.js`).

- A sub-rule is **locatable** when at least one of its handle
  fields — `from`, `to`, or `tol` — is non-null. When `to` is an
  array (the multi-target form), an empty array SHALL NOT count as
  "set".
- A sub-rule is **text-only** when all three handle fields are null,
  missing, or — for `to` — an empty array.

For each sub-rule row:

- **Locatable rows** SHALL continue to render in one of the three
  existing interactive states — `.same-role`, `.other-role`, or
  `.missing-file` — exactly as before, including their nav-hint
  (`show` / `→ <part> viewer` / `(no file)`) and click handler.
- **Text-only rows** SHALL render with the `.text-only` modifier
  class, presenting `.part` + `.sub-text` plus an empty third grid
  cell. They SHALL NOT carry a `.nav-hint`, SHALL NOT be wired to
  a click handler, SHALL NOT pass through `resolveSubRuleFile`,
  and SHALL NOT receive any of the three interactive state
  modifiers. The CSS treatment SHALL neutralise hover affordances
  (cursor `default`, no border / background flash on hover) and
  mute the `.part` colour to the same dim grey used elsewhere in
  the sidebar for non-actionable text.

These changes SHALL NOT alter the rule-check JSON envelope, the
`?rule=<name>&idx=<i>` deep-link contract, or the canvas-side
`focusSubRule` / `drawFocusedSubRule` pipeline. A deep link
addressed at a text-only sub-rule MAY mark its row `.focused` in
the sidebar; the canvas SHALL draw nothing for it, matching the
existing all-null behaviour of `drawFocusedSubRule`.

#### Scenario: Text-only sub-rule renders inert with no nav-hint

- **WHEN** the sidebar renders a sub-rule whose `from`, `tol`, and
  `to` are all null (or, for `to`, an empty array)
- **AND** the sub-rule carries a non-empty `text` and a valid
  `part`
- **THEN** the row's `<li>` carries the `.text-only` class and not
  `.same-role` / `.other-role` / `.missing-file`
- **AND** the row renders `.part` and `.sub-text` only, with an
  empty third cell, and no `.nav-hint` element
- **AND** the cursor over the row is `default` (not `pointer` and
  not `not-allowed`)
- **AND** hovering does not flash the cyan border / background that
  interactive rows show
- **AND** clicking the row does not navigate, focus, or call
  `focusSubRule`

#### Scenario: Locatable rows are unchanged

- **WHEN** the sidebar renders a sub-rule with `from` non-null
- **THEN** the row renders in whichever of `.same-role`,
  `.other-role`, or `.missing-file` matches its file-resolution
  outcome, with its existing nav-hint and click behaviour
- **AND** the row does not carry the `.text-only` class

#### Scenario: `to`-only multi-target sub-rule is locatable

- **WHEN** the sidebar renders a sub-rule whose `from` and `tol`
  are null but whose `to` is a non-empty array of handles
- **THEN** the row is classified as locatable and rendered with
  its existing interactive state — it is not stamped `.text-only`

#### Scenario: Empty `to` array does not count as locatable

- **WHEN** the sidebar renders a sub-rule whose `from` and `tol`
  are null and whose `to` is an empty array
- **THEN** the row is classified as text-only and stamped
  `.text-only`

#### Scenario: Deep link to a text-only sub-rule is a canvas no-op

- **WHEN** the viewer is opened with `?rule=<name>&idx=<i>` and the
  resolved sub-rule is text-only
- **THEN** the sidebar may apply `.focused` to the row
- **AND** the canvas draws no overlay (no dashed line, no handle
  highlight, no annotation label)
- **AND** no JavaScript error is raised by the focus pipeline

## ADDED Requirements

### Requirement: Rule-check modal distinguishes locatable from text-only sub-rules

The rule-check results modal (`showRuleResults` in
`app/static/dashboard.js`) SHALL classify each sub-rule it renders
as **locatable** or **text-only**:

- A sub-rule is **locatable** when at least one of its handle
  fields — `from`, `to`, or `tol` — is non-null. (Per the DRC
  integration contract, a well-formed sub-rule is always locatable;
  this classification stays defensive against malformed emit.)
- A sub-rule is **text-only** when all three handle fields are
  null or missing.

For each sub-rule row:

- **Locatable rows** SHALL render with a `🎯` glyph prefix and
  SHALL include the existing clickable `View in <PART> →` link
  pointing at `/viewer/<file_id>?rule=<name>&idx=<i>`, unchanged
  from prior behaviour.
- **Text-only rows** SHALL render with an `ℹ` glyph prefix, the
  text in a dimmed style (lower opacity than locatable rows), and
  SHALL NOT render a `View in <PART> →` link — the click would
  not produce any highlight in the viewer.
- The existing "**PART not uploaded**" branch (no file uploaded
  for the role) SHALL remain unchanged: no link, no glyph swap,
  the message text reads as before.

Each rule card header SHALL additionally show a small chip
summarising sub-rule locator counts:

- When `rule.rules` is a non-empty list, the chip text SHALL read
  `🎯 N · ℹ M`, where N is the count of locatable sub-rules in
  this rule and M is the count of text-only sub-rules. Either
  count MAY be zero; both are displayed.
- When `rule.rules` is empty, the chip SHALL read `ℹ no locator`.
- The chip SHALL use the same neutral-informational style as the
  existing `rescaled` / recover pills on the dashboard (the
  `.rescaled-pill` family).

These affordance changes SHALL NOT alter the underlying
rule-check JSON shape, the `?rule=&idx=` query parameter contract,
or the viewer's `focusedSubRule` highlight pipeline.

#### Scenario: All-locatable rule shows clickable rows with full chip
- **WHEN** a rule card renders for a rule whose `rules` list
  contains three sub-rules, each with `from` non-null
- **THEN** all three sub-rule rows are prefixed with `🎯`
- **AND** each row renders a clickable `View in <PART> →` link
- **AND** the rule card header chip reads `🎯 3 · ℹ 0`

#### Scenario: All-text-only rule dims the rows and hides links
- **WHEN** a rule card renders for a rule whose `rules` list
  contains two sub-rules, neither carrying `from`, `to`, nor `tol`
- **THEN** both sub-rule rows are prefixed with `ℹ`
- **AND** both rows render in the dimmed text-only style
- **AND** neither row renders the `View in <PART> →` link
- **AND** the rule card header chip reads `🎯 0 · ℹ 2`

#### Scenario: Mixed rule shows mixed icons and bucketed chip counts
- **WHEN** a rule card renders for a rule whose `rules` list has
  two locatable sub-rules and one text-only sub-rule
- **THEN** the two locatable rows are prefixed with `🎯` and
  clickable
- **AND** the one text-only row is prefixed with `ℹ` and dimmed
  with no link
- **AND** the rule card header chip reads `🎯 2 · ℹ 1`

#### Scenario: Empty-rules rule shows the no-locator chip
- **WHEN** a rule card renders for a rule whose `rules` list is
  empty
- **THEN** the existing "No sub-rules emitted" empty-state row is
  preserved unchanged
- **AND** the rule card header chip reads `ℹ no locator`

#### Scenario: PART not uploaded branch is preserved
- **WHEN** a sub-rule's referenced file is not uploaded to the
  product (no `file` resolves)
- **THEN** the row continues to render the existing
  `<PART> not uploaded` message
- **AND** the row is not prefixed with either `🎯` or `ℹ`
- **AND** no `View in <PART> →` link is rendered

#### Scenario: tol-only sub-rule is locatable
- **WHEN** a sub-rule has `from` and `to` null but `tol` non-null
- **THEN** the row is classified as locatable
- **AND** the row is prefixed with `🎯`
- **AND** the row renders the clickable `View in <PART> →` link

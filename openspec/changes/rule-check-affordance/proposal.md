## Why

The rule-check modal (`showRuleResults` in `app/static/dashboard.js`)
treats every sub-rule the same: each row renders a `View in X →`
link to the viewer regardless of whether the sub-rule carries any
geometry handles. When the operator clicks a row that has no
`from` / `to` / `tol` (e.g. a rule whose `rules: []` produced no
sub-rules, or — defensively — a malformed sub-rule with all handles
null) the viewer opens with no highlight. To the operator this
looks like the click did nothing, which erodes trust in the rule
report. The UX needs to distinguish "you can locate this in the
viewer" from "this is text-only, nothing to point at" **before** the
click.

## What Changes

- For each sub-rule, the dashboard SHALL classify it as **locatable**
  (at least one of `from`, `to`, `tol` is non-null) or
  **text-only** (none of the three is set). Per the DRC integration
  invariant a well-formed sub-rule is always locatable, but the
  classification is defensive.
- **Locatable** sub-rule rows SHALL render a `🎯` glyph prefix and
  keep the existing `View in X →` link as clickable.
- **Text-only** sub-rule rows SHALL render an `ℹ` glyph prefix, the
  text in a dimmed style, and SHALL NOT render the `View in X →`
  link (the click would produce no highlight).
- The existing "PART not uploaded" branch (no file for the role)
  is unchanged.
- Each rule card header SHALL gain a small affordance chip showing
  `🎯 N · ℹ M` — N = count of locatable sub-rules in this rule,
  M = count of text-only sub-rules. When the rule's `rules` list is
  empty the chip SHALL read `ℹ no locator`.

No backend changes: the rule-check JSON schema and the DRC
integration contract stay untouched. The DRC team continues to emit
the same payload; the viewer just renders it with sharper
affordance cues.

## Capabilities

### New Capabilities
<!-- None; pure UI refinement of an existing surface. -->

### Modified Capabilities
- `viewer-ui`: ADD a requirement that the rule-check results modal
  visually distinguishes locatable from text-only sub-rules and
  surfaces per-rule locator counts.

## Impact

- **Code**:
  - `app/static/dashboard.js` (around `showRuleResults` at line 911)
    — classify each sub-rule, conditionally render the link, attach
    the icon prefix, and compute + render the header chip.
  - `app/static/style.css` — one new rule (`.subrule-text-only` or
    similar) for the dimmed-row treatment, plus the header chip
    style (reuse the existing `.rescaled-pill` family if visually
    consistent).
- **APIs / data**: no change. The rule-check JSON keeps its
  current shape; `dxfs_by_role`, `from`, `to`, `tol`, `file_id`,
  `text` semantics are unchanged.
- **Tests**: dashboard-side rendering is exercised manually today
  (no jsdom suite). Manual verification covers (a) a rule with
  all-locatable sub-rules → all rows clickable + green chip,
  (b) a rule with `rules: []` → header chip reads `ℹ no locator`,
  (c) a mixed rule (locatable + text-only) → mixed icons + chip
  counts both buckets.
- **Operator-visible**: the modal becomes more readable; rows that
  used to look interactive but did nothing are now visibly
  non-interactive, and the header chip lets the operator scan
  which rules are worth drilling into.

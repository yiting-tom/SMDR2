## Context

`showRuleResults` (`app/static/dashboard.js:911`) renders the modal
that opens after a product-scoped rule-check job completes. The
function iterates `data.results` → for each `(name, rule)` pair it
builds a `<section class="rule-result-card">` whose body is an
ordered list of sub-rules; each sub-rule row gets a
`View in <PART> →` link. Today this link is rendered unconditionally
as long as the role's file exists, regardless of whether the
sub-rule carries a handle the viewer can highlight.

Per the DRC integration contract (`openspec/specs/design-rule-checking
/INTEGRATION.md:485`), every emitted sub-rule MUST have at least one
of `from` / `to` / `tol` non-null. So in well-formed data every
sub-rule IS locatable. But two real cases still produce text-only
states the modal must handle gracefully:

1. **`rule.rules == []`** — the rule was evaluated but produced no
   sub-rules (the integration contract explicitly allows this; line
   458 says "0+ 個 sub-rule"). The dashboard already renders an
   `<li class="empty">No sub-rules emitted (rule could not be
   evaluated)</li>`, but the rule header looks identical to a
   rich-content rule.
2. **Malformed sub-rule** — a rule emitter ships data that violates
   the invariant (all three handle fields null). The dashboard
   shouldn't crash, and shouldn't render a clickable link that goes
   nowhere.

The change is pure presentation. No backend touches, no
schema change, no new endpoint.

## Goals / Non-Goals

**Goals:**

- A glance at any rule card tells the operator how many sub-rules
  can be located in the viewer, before any click.
- Clicking a row that can be located opens the viewer with the
  expected highlight (current behaviour, preserved). Clicking a
  text-only row is impossible — no link is rendered.
- The modal stays readable: no new colour family introduced, no
  layout shift in the common case (rule with all-locatable
  sub-rules).

**Non-Goals:**

- Changing the rule-check JSON schema or the DRC integration
  contract. The emitter side stays exactly as documented.
- Inlining geometry previews inside the modal. The modal stays
  text-first; the viewer is still the place that shows the DXF.
- Adding a settings toggle for the new affordance — it's the
  permanent UX.

## Decisions

### Decision 1: Locatable predicate is `from || to || tol`

The DRC integration doc treats `tol` as a highlightable entity
(viewer's `focusedSubRule.tol` adds the handle to the highlight
set — see `canvas.js:1137`). So "locatable" means *any of the three
handle fields is non-null*. A sub-rule that emits only `tol` still
lights up an entity in the viewer.

**Alternative considered:** `from || to` only. Rejected — a
`tol`-only sub-rule does highlight successfully, and treating it as
"text-only" would hide a working interaction.

### Decision 2: Icon-first affordance, not colour-coded

`🎯` (locatable) vs `ℹ` (text-only) icons sit at the start of each
sub-rule row. Colour signalling is reserved for pass/fail
(`✓` / `✗` already use red/green); piling another colour onto
"interactive vs not" would compete with the status indicator.

**Alternative considered:** colour the text-only row a different
shade. Rejected — overloads the colour vocabulary.

### Decision 3: Header chip uses the existing pill family

The header chip reuses the `.rescaled-pill` CSS class (the
neutral-informational pill we just standardised for recover notes
and rescaled units). One pill family, consistent grammar:
`ℹ <short label>`.

**Alternative considered:** new dedicated `.affordance-chip` class.
Rejected — the existing pill is the right size and chroma; another
class is just maintenance overhead.

### Decision 4: `ℹ no locator` for empty-`rules` rules

When `rule.rules` is empty the modal already renders an `<li class=
"empty">…</li>` row. The header chip mirrors that by reading
`ℹ no locator` instead of `🎯 0 · ℹ 0`, since "0/0" reads as a
parsing error to the operator.

### Decision 5: No new test infrastructure

There is no jsdom / headless dashboard suite today (the project's
JS verification is operator-driven). Adding one for this small
patch is out of proportion. Manual verification with three
synthetic rules (all-locatable / all-text-only / mixed) covers the
behaviour and matches how all other dashboard UI is verified.

## Risks / Trade-offs

- **Risk:** Operator who's used to clicking every row finds the
  text-only rows confusing ("why can't I click this one?"). →
  **Mitigation:** the `ℹ` icon + dimmed style + visible chip count
  on the header all signal "this row is informational". The
  header chip is the most efficient signal because it answers
  the question before the operator drills down.

- **Risk:** A future rule emitter starts including text-only
  sub-rules deliberately (e.g. a summary line at the end of the
  list). → **Mitigation:** the spec allows this; the UI handles
  it correctly (dimmed row + chip count). The DRC integration
  contract still says every sub-rule should have a handle, but
  the dashboard is now robust against violations.

- **Risk:** The chip text gets long for rules with many sub-rules
  (e.g. `🎯 47 · ℹ 3`). → **Mitigation:** the chip lives on the
  header line next to the rule name + text — at typical widths
  there's plenty of room. We don't truncate.

- **Trade-off:** Reusing `.rescaled-pill` means the recover pill,
  rescaled pill, and affordance chip share one colour family. If
  we later want to differentiate, we have to peel them apart. For
  now, consistency wins.

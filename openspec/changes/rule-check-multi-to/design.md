## Context

The RuleChecking JSON sub-rule today is a flat dict where every
handle field — `from`, `to`, `tol` — is `str | None`. The viewer's
focused-sub-rule path (`app/static/canvas.js`) reads those three
fields, highlights the entities, and when both `from` and `to`
are set draws one dashed segment between them with the sub-rule's
`text` at the midpoint. The adapter (`app/rule_check.py`) validates
the envelope on the way back from the external rule function and
rejects any deviation.

In practice, plenty of real rules are "one entity vs. several
others" — minimum spacing between a BGA ball and its neighbours,
clearance between a fiducial and the nearest pads, etc. Today the
external team emits N sub-rules with the same `from` and different
single `to`s; operators see what reads as N near-identical findings
for one logical violation. The fix is to let `to` be a list, and
have the viewer fan dashed segments from `from` to each element.

## Goals / Non-Goals

**Goals:**

- A single sub-rule can describe a one-to-many target relationship,
  collapsing N rows into one in the rule modal.
- Old emitters keep working without change. `to: "X"` and
  `to: ["X"]` are visually equivalent in the viewer.
- Backend validation rejects malformed list shapes early (empty
  list, non-string elements, `to: [..]` without `from`).
- The viewer's existing focused-sub-rule pipeline keeps its single
  code path — the list form is normalised at the top of each
  drawing call, not threaded through every helper.

**Non-Goals:**

- Many-to-many. `from` stays single. If the external team wants a
  group-to-group constraint they emit N sub-rules with different
  `from`s.
- Changing `tol` or `tol_text`. The annotation-only highlight stays
  exactly as is.
- New per-segment colour or per-segment text. Every fan segment
  shares the sub-rule's `pass`/`fail` colour; only the first
  segment carries the label.
- Pre-merging or de-duplicating the targets list. The external team
  decides what's in there; we render whatever they emit (subject to
  the well-formed-list validation).

## Decisions

### Decision 1: Backward-compatible union, not a breaking switch

`to: str | list[str] | None` keeps old emitters running unchanged.
The cost is the union — every consumer (validator, viewer,
dashboard predicate) has to handle two forms — but it's tiny in
practice (one `Array.isArray` branch / one `isinstance` branch).

**Alternative considered:** breaking switch to `list[str] | None`,
external team always emits a list. Rejected — the integration
contract is shared with an external team and they'd have to
coordinate a deploy; not worth the schema cleanliness.

### Decision 2: `from` stays single

The asymmetry models the real use case: one source entity is
flagged against a set of related targets. A many-to-many shape
would invite questions like "do we draw N×M lines?" with no
operator-meaningful answer. If the external team needs that, they
emit multiple sub-rules.

**Alternative considered:** make `from` list-shaped too. Rejected
— doubles the validation surface and the rendering decision space
for no concrete use case.

### Decision 3: Empty list `to: []` is rejected, not normalised

Three behaviours were possible for `to: []`:
- (a) treat as `to: null` (silently accept and move on)
- (b) reject (force the emitter to send `null`)
- (c) accept and render as "from-only" (highlight `from`, no
  segment)

(b) is the bright-line rule that's easiest to reason about: a
non-null `to` always means "render something from-to". (a) hides
emitter bugs. (c) introduces a "list with zero elements means the
same as null" edge case that future readers will trip over.
We pick (b) and document it in the integration contract.

### Decision 4: Label at the first segment's midpoint

When `to` is `[t1, t2, ..., tN]`, stacking N labels at N midpoints
is visual noise; clustering them at a single point overlaps
illegibly; centroid placement gets confusing fast. The first
element of the list gets the label, every other element just gets
its dashed segment.

The external team controls list order, so if a particular target
"matters most" they put it first.

**Alternative considered:** label at the centroid of all segment
midpoints. Rejected — for N≥3 the centroid often lands inside a
crowded region with no anchor entity, making the label feel
disembodied.

### Decision 5: Normalise list at the top of each viewer render call

`drawFocusedSubRule` and `drawFocusedLabel` will each compute
`toList = Array.isArray(to) ? to : (to ? [to] : [])` once and use
it as the canonical iteration target. The downstream code paths
(`shortestSegmentBetween`, `drawEndpointMarker`, `drawLabelBox`)
stay scalar — no need to thread the union type through every
helper.

The dashboard's `isLocatable` predicate gets the same treatment
via a `hasTo` helper.

### Decision 6: One adapter validation path for both forms

`_validate_sub_rule` currently calls `_typed_handle(sub, "to",
label)` which insists on `str | None`. We replace just the `to`
read with a small `_typed_to_handle_or_list` helper that returns
either a `str`, a `list[str]`, or `None`. The downstream invariant
checks (`to is not None and frm is None` → reject) become "is `to`
non-null in any form?" — easy with a `has_to_value(t)` helper.

We don't touch `_typed_handle`'s contract for `from` / `tol`;
they remain scalar.

## Risks / Trade-offs

- **Risk:** External emitter mistakenly sends `to: ["X"]` with
  `from: null`. → **Mitigation:** the adapter rejects exactly as
  today for the scalar case. New scenarios cover the list form.

- **Risk:** Operator reads only the first segment's label and
  misses that other `to_i` targets exist. → **Mitigation:** every
  target is highlighted with the same focus colour; the dashed
  segments converge on the `from` entity, so the fan shape is
  visually obvious. The chip from `rule-check-affordance` shows
  the total locatable count.

- **Risk:** Very large `to` lists (e.g. N=50) produce a busy
  viewer. → **Mitigation:** out of scope for this change; the
  external team is expected to use list-`to` for small clusters
  (≤ ~10). If real-world rules push higher we can revisit with
  per-segment thinning.

- **Risk:** The list-element validation reads each handle as a
  string but doesn't verify they exist in the file's match-JSON.
  → **Mitigation:** same policy as today for the scalar form —
  the adapter trusts the emitter's handle vocabulary; viewer
  silently no-ops on unknown handles. Out of scope here.

- **Trade-off:** Adapter validation gets slightly more elaborate
  (list-or-string branch + per-element type check). Trade is
  worthwhile because the new shape unlocks meaningful operator UX
  wins.

- **Trade-off:** Updating both `spec.md` and `INTEGRATION.md`
  duplicates the type definition. Trade is necessary because
  INTEGRATION.md is the document the external team reads; spec.md
  is internal contract. We're keeping them aligned for now.

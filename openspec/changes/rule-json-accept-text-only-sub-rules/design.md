## Context

`app/rule_check.py:_validate_sub_rule` is the single envelope
validator for the RuleChecking JSON payload. It runs in two paths:

1. **Production worker path** — `check_rules(product_id, bundle_dir)`
   calls the external rule function, then validates its result before
   the worker persists it as the product's `rule_check.json`.
2. **Dev-mode upload path** — `POST /api/products/{id}/rule-check/upload`
   calls `_validate_envelope` directly on the hand-crafted JSON the
   external team posts.

The current invariant set includes "a sub-rule must set at least one
of `from`, `tol`" — rationale: "nothing to highlight". This proved
too restrictive in practice; the external team needs to emit
informational sub-rules without entity references.

Everything downstream of the envelope already tolerates null
handles: the viewer's `focusSubRule` (`canvas.js:1631-1646`) uses
`?? null` for every handle field, and the sidebar `renderSubRuleItem`
falls through to a `(no file)` hint when `resolveSubRuleFile` returns
null. So the constraint is purely a validator-side gate, not a
load-bearing assumption anywhere downstream.

## Goals / Non-Goals

**Goals:**
- Accept sub-rules whose `from`, `to`, `tol`, and `tol_text` are all
  null, provided `text` is present and non-empty (this remains required).
- Preserve every other envelope invariant unchanged:
  - `to` requires `from`
  - `tol_text` requires `tol`
  - any non-null handle requires non-null `file_id`
  - rule-level required keys (`pass`, `text`, `rules`)
  - sub-rule `text` must be non-empty string
  - sub-rule `part` must be one of the valid set
- Lock the unaffected invariants with a regression test so a future
  relaxation doesn't accidentally widen them too.

**Non-Goals:**
- Polishing the viewer sidebar's "(no file)" hint for text-only
  sub-rules. Out of scope; tracked separately if asked.
- Adding new sub-rule fields (e.g. an explicit "informational"
  flag). The envelope contract stays minimal.
- Changing the rule-name level (`pass`, `text`, `rules`) — those
  invariants are untouched.
- Touching `app/external_rule_check/_dev_mock.py` or other helpers.

## Decisions

### Decision: Remove the constraint, keep the docstring honest

The minimal change is deleting the four-line `if frm is None and tol
is None:` block in `_validate_sub_rule` and the corresponding line
in the module docstring's invariant list.

**Rationale:** the constraint was a *prescription* (the validator
refusing payloads it could otherwise carry), not a load-bearing
*assumption* (no downstream code reads it as a guarantee). Removing
it doesn't shift behaviour anywhere except the rejection itself.

**Alternative considered: keep the validator strict; add an opt-in
flag.** Rejected — the relaxed behaviour is the desired default;
text-only sub-rules are a legitimate use case, not a niche bypass.
A flag would just defer the spec decision while complicating every
caller.

### Decision: Flip the existing test, add a new "other invariants still hold" test

The existing test (`tests/test_rule_check.py:181` — "nothing to
highlight") asserts the rejection. After this change, that test
becomes the canonical positive test for the new behaviour.

In parallel, add `test_validator_still_rejects_other_invariants`
that exercises three counter-examples:
- `to` set but `from` null → rejected
- `tol_text` set but `tol` null → rejected
- `from` set but `file_id` null → rejected

**Rationale:** the test in line 181 was the *only* coverage of "any
null-combination rejection", and after this change there is no
coverage at all of the *adjacent* invariants. The new test
guarantees a future "while we're relaxing things" PR can't quietly
drop the wrong invariant.

### Decision: No viewer code change

The viewer's `focusSubRule` already uses `?? null` for every handle
field. Rendering a text-only sub-rule on the canvas is a no-op
(nothing to highlight, nothing to draw a line to). The sidebar's
`(no file)` hint for text-only sub-rules is sub-optimal UX but
functional; polishing it is a separate concern.

**Alternative considered: also update sidebar rendering in the same
change.** Rejected — bundling unrelated UI work into a contract
change inflates the diff and the review surface without simplifying
either piece. Track the sidebar polish separately if the user wants
it.

## Risks / Trade-offs

- **Risk:** a future caller might assume the envelope still enforces
  the old constraint and rely on it. → **Mitigation:** the docstring
  is updated alongside the code; the spec change records the
  decision; the inverted test makes the new contract visible at
  test-read time.
- **Risk:** text-only sub-rules clutter the viewer sidebar with
  `(no file)` hints. → **Mitigation:** out-of-scope polish. The
  current rendering is correct (sub-rule is genuinely not local to
  any file), just suboptimal.
- **Trade-off:** the contract loosens. Previously the validator
  guaranteed every sub-rule pointed at *something*; now it doesn't.
  Acceptable because every downstream consumer already handles the
  null case.

## Migration Plan

Single commit; no data migration. Existing payloads continue to
validate (the change only *accepts* more, never rejects more).
Rollback is the inverse commit; no payload corruption risk.

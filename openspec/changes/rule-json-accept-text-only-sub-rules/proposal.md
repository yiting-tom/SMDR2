## Why

The RuleChecking JSON envelope currently rejects any sub-rule whose
`from` AND `tol` are both `null`, on the rationale that "a sub-rule
with both null carries no entity to highlight". In practice the
external rule team needs to emit purely informational sub-rules — a
status message, a category header, a note that doesn't pin to any
particular DXF entity. The current invariant blocks those, forcing
authors to invent a placeholder handle just to satisfy the validator.

Relaxing the invariant lets the rule-check pipeline carry text-only
sub-rules end-to-end. The viewer already tolerates `null` everywhere
via `?? null` fallbacks (`canvas.js:1638-1641`), so the change is
contained to the envelope contract.

## What Changes

- **`app/rule_check.py`** — `_validate_sub_rule` SHALL no longer
  raise `RuleCheckOutputError` when both `from` and `tol` are null.
  Other invariants stay (`to` requires `from`, `tol_text` requires
  `tol`, handles require `file_id`).
- **`app/rule_check.py` module docstring** — invariant list updated:
  drop "A sub-rule MUST set at least one of `from`, `tol`".
- **`openspec/specs/design-rule-checking/spec.md`** — requirement
  "RuleChecking JSON output shape" updated: invariant 4 ("set at
  least one of `from`, `tol`") removed; existing scenario "Sub-rule
  must reference at least one entity" replaced by an explicit
  "text-only sub-rules are accepted" scenario. Other scenarios
  unchanged.
- **`tests/test_rule_check.py`** — the test that currently locks the
  reject behaviour (around line 181, `"nothing to highlight"`
  fixture) is inverted to assert acceptance. Add a regression test
  proving the *other* invariants still reject (`to`-without-`from`
  still fails; `tol_text`-without-`tol` still fails; handle-without-
  `file_id` still fails).

## Capabilities

### New Capabilities

_None._ This relaxes an existing constraint in an existing capability.

### Modified Capabilities

- `design-rule-checking`: the "RuleChecking JSON output shape"
  requirement's invariant 4 is removed; one scenario flipped, one
  added. No other requirements affected.

## Impact

- **Code**: `app/rule_check.py` only — ~5 lines removed (the
  `if frm is None and tol is None:` block + its docstring entry).
- **APIs**: `/api/products/{id}/rule-check/upload` and the
  background-worker rule-check path both call
  `_validate_envelope` and inherit the relaxed behaviour
  automatically. No new endpoints, no payload shape change.
- **Tests**: 1 test inverted, 1 added. Existing tests for the
  unaffected invariants stay green.
- **Dependencies**: none.
- **Operational**: rule authors can now ship informational
  sub-rules without inventing placeholder handles. No data
  migration; previously-rejected payloads simply start succeeding.
- **Viewer**: no code change. `focusSubRule` already handles
  null-everywhere via `?? null` fallbacks; the sidebar shows
  text-only sub-rules with a `(no file)` hint. Polish on that
  hint is intentionally out of scope (separate UI concern).

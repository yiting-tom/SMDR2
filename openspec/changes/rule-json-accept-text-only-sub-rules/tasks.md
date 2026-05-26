## 1. Code change

- [x] 1.1 In `app/rule_check.py:_validate_sub_rule`, remove the block that raises when both `frm is None and tol is None`. Other invariants untouched.
- [x] 1.2 Updated the module docstring's invariant list: dropped "MUST set at least one of `from`, `tol`" + added the text-only acceptance note.

## 2. Tests

- [x] 2.1 Inverted `test_adapter_rejects_no_from_or_tol` → `test_text_only_sub_rule_is_accepted` with informational `text` and `assert result is good` instead of `pytest.raises`.
- [x] 2.2 Added `test_validator_still_rejects_other_invariants` covering all three counter-examples (to-without-from, tol_text-without-tol, handle-without-file_id).
- [x] 2.3 `pytest tests/test_rule_check.py` — 20 passed.
- [x] 2.4 `pytest` (full) — 420 passed / 5 skipped / 0 failed.

## 3. Manual verification

- [ ] 3.1 **[USER]** Start the dev server, with dev mode on POST a hand-crafted JSON to `/api/products/{id}/rule-check/upload` containing one rule whose `rules` array has one text-only sub-rule (only `part` + `text`, every other field null or omitted). Confirm:
  - HTTP 200 OK with `rule_count: 1`, `pass_count` / `fail_count` consistent with the rule's `pass`
  - The persisted `rule_check.json` on disk contains the text-only sub-rule verbatim
  - The viewer sidebar shows the sub-rule (with the current `(no file)` hint, which is acceptable for now — see proposal "Non-Goals")

## 4. Archive

- [ ] 4.1 After tasks 1–3 pass, run `/opsx:archive rule-json-accept-text-only-sub-rules` to fold the modified `design-rule-checking` spec into the live spec and mark the change archived.

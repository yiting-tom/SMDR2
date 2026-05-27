## 1. Adapter validation (`app/rule_check.py`)

- [x] 1.1 Add a private helper `_typed_to(sub, label) -> str | list[str] | None` that mirrors `_typed_handle`'s signature but accepts either a string (passed through), `None` (returns `None`), or a list. For the list branch: reject empty (`raise RuleCheckOutputError(f"{label}: `to` is an empty list; emit null instead")`), reject any element that is not a non-empty `str` (`raise … "{label}: `to` list element #{i} must be a non-empty string"`), return the list unchanged. Any other type raises `RuleCheckOutputError`.
- [x] 1.2 In `_validate_sub_rule` (`app/rule_check.py:131`), replace `to = _typed_handle(sub, "to", label)` with `to = _typed_to(sub, label)`. Keep the existing `frm = _typed_handle(sub, "from", label)` and `tol = _typed_handle(sub, "tol", label)` calls unchanged (those stay scalar).
- [x] 1.3 Update the `to is not None and frm is None` check to a small helper `_has_to_value(t)` that returns `True` for non-empty string and non-empty list, `False` for None. Use it in the `if frm is None and _has_to_value(to)` guard and the file_id-required guard so both list and scalar forms are checked.
- [x] 1.4 Update the docstring at `app/rule_check.py:14-35` to reflect the new `to` type (`handleID | list[handleID] | None`) and add a sentence about empty-list rejection.

## 2. Viewer rendering (`app/static/canvas.js`)

- [x] 2.1 Add a top-of-function normaliser inside both `drawFocusedSubRule` (around `canvas.js:1131`) and `drawFocusedLabel` (around `canvas.js:1199`): `const toList = Array.isArray(focusedSubRule.to) ? focusedSubRule.to : (focusedSubRule.to ? [focusedSubRule.to] : []);`. Use this list everywhere downstream in those functions; do not reach back into `focusedSubRule.to` after the normaliser.
- [x] 2.2 In `drawFocusedSubRule`: replace the existing `if (focusedSubRule.to) handles.add(focusedSubRule.to)` with a loop that `handles.add(t)` for every `t` in `toList`. Replace the `if (focusedSubRule.from && focusedSubRule.to) { … shortestSegmentBetween(from, to) … }` block with a `if (focusedSubRule.from && toList.length) { for (const t of toList) { const segment = shortestSegmentBetween(focusedSubRule.from, t); if (!segment) continue; … draw … } }` loop, keeping the per-segment dashed-line + endpoint-marker rendering unchanged on each iteration.
- [x] 2.3 In `drawFocusedLabel`: replace the `if (focusedSubRule.from && focusedSubRule.to)` block's segment lookup with `shortestSegmentBetween(focusedSubRule.from, toList[0])` so the label always anchors to the first segment. The rest of the branch (midpoint world→screen → `drawLabelBox`) stays exactly the same. The `else if (focusedSubRule.from)` branch (from-only, no `to`) takes effect when `toList.length === 0`, so guard the first branch with `if (focusedSubRule.from && toList.length)` for symmetry.

## 3. Dashboard predicate (`app/static/dashboard.js`)

- [x] 3.1 Add a small helper near `isLocatable` (`dashboard.js` around the `isLocatable` definition added by `rule-check-affordance`): `function hasToValue(to) { if (!to) return false; if (Array.isArray(to)) return to.length > 0; return true; }`. Then update `isLocatable` to `Boolean(sub && (sub.from || hasToValue(sub.to) || sub.tol))`. This keeps a non-empty list `to` classified as locatable and treats `to: []` (illegal upstream, but defensive) as no-`to`.

## 4. Integration doc (`openspec/specs/design-rule-checking/INTEGRATION.md`)

- [x] 4.1 Update the type column for `rules[].to` from `str \| null` to `str \| list[str] \| null` (or the local equivalent phrasing in 繁中). Document next to it: "list 非空，元素必須是非空字串；emit null 而不是 [] 來表達 no `to`".
- [x] 4.2 Update the "Viewer 顯示語意" table to add a row for the list-`to` case: `from + to[]` → 「對每個 to_i 從 from 各畫一條虛線（fan），text 顯示在第一條（to[0]）的中點」.
- [x] 4.3 Update the "不變式" section to add: "`to: []` 視為不合法（請傳 null）；`to` list 元素必須是非空 string"; and update "`to` 只能在 `from` 也有設的情況下出現" to clarify it applies whether `to` is string or list.

## 5. Tests (`tests/test_rule_check.py`)

- [x] 5.1 Add `test_validate_accepts_scalar_to_string` — sanity baseline asserting the existing scalar form still validates.
- [x] 5.2 Add `test_validate_accepts_non_empty_list_to` with `to: ["AB12", "CD34"]` and `from: "AA00"`; assert the envelope validates and the persisted-output payload preserves the list form verbatim.
- [x] 5.3 Add `test_validate_rejects_empty_list_to` — `to: []` raises `RuleCheckOutputError`, exception message mentions "empty" and "null".
- [x] 5.4 Add `test_validate_rejects_list_to_with_non_string_element` — `to: ["AB12", 42]` raises with a message naming the bad element index.
- [x] 5.5 Add `test_validate_rejects_list_to_with_empty_string` — `to: ["AB12", ""]` raises.
- [x] 5.6 Add `test_validate_rejects_list_to_without_from` — `to: ["AB12"]` with `from: null` raises (mirrors the existing scalar-form rejection test).
- [x] 5.7 Verify all existing scalar-`to` tests still pass unmodified — the change is backward-compatible by design.

## 6. Manual verification

- [ ] 6.1 With a synthetic rule emitting `to: ["h1", "h2", "h3"]` and `from: "h0"`, open the viewer with `?rule=…&idx=…` and confirm: three dashed segments are drawn from `h0` to each of `h1`/`h2`/`h3`, all four entities are highlighted in the focus colour, and the sub-rule's `text` appears at the midpoint of the `h0→h1` segment only.
- [ ] 6.2 Re-open the same product with a scalar-form sub-rule (`to: "h1"`) and confirm the legacy single-segment render is unchanged.
- [ ] 6.3 Emit a sub-rule with `to: []` (manually-edited test payload) and confirm the rule-check job fails with an `error` mentioning the empty-list rejection.
- [ ] 6.4 Dashboard: confirm a sub-rule with `to: [...]` is counted as 🎯 (locatable) in the affordance chip from `rule-check-affordance`, not as ℹ.

---
name: add-rule
description: Add a new Design Rule Check (DRC) rule to SMDR2. Use when the task is to add, define, or implement a new product-scoped rule that checks geometry / counts / relationships across the uploaded DXFs.
target: any-agent
---

Add a new Design Rule Check (DRC) rule to SMDR2.

This skill is agent-neutral — it describes the contract and procedure
for extending `app/external_rule_check/`. Any agent (Claude, custom
tooling, scripted code-mod, etc.) can follow it.

## Architecture

Rule logic is owned by the external rule-checking team and lives in
`app/external_rule_check/` (currently `_stub.py` + `_dev_mock.py`
until the real module lands). SMDR2's `app/rule_check.py` is a thin
adapter that:

1. Calls `app.external_rule_check.check_rules(product_id, bundle_dir)`.
2. Validates the returned envelope against the RuleChecking JSON
   shape contract.
3. Returns the result verbatim — no mutation, no padding.

A rule lives entirely inside the external module. The adapter does
not need to know about it.

## Required inputs from the human

Before writing code, the agent MUST collect these. If any are missing
or ambiguous, ask the human — do not guess.

1. **Rule name** — short identifier (`Rule4`, `BgaPitchCheck`, ...);
   becomes the outer dict key.
2. **What it checks** — one sentence; ends up in the rule's `text`.
3. **Which DXF role(s)** the rule needs (`SBT` / `BD` / `POD` / `RING`
   / `LID`) and the failure-mode if a needed role is missing.
   Canonical pattern when the role is absent: `pass: True` with an
   explanatory `text` and empty `rules: []`, so the rule doesn't
   false-fail a product that simply doesn't carry that role.
4. **Geometry kind** — distance? count? containment? angle?
5. **Threshold(s)** — numeric values with units (mm) and the
   comparator (`<`, `<=`, `>`, `>=`, `==`).

## Input: the bundle directory

The external function receives the path to a materialised handoff
bundle. Layout:

    <bundle_dir>/
        manifest.json
        dxfs/<file_id>.dxf
        match/<file_id>.json

The manifest groups files by role. JSON schema lives at
`openspec/specs/design-rule-checking/drc-manifest.schema.json`.
Relevant fields:

```json
{
  "bundle_version": "1.2.0",
  "product_id": "...",
  "customer_id": "...",
  "files": [
    {
      "role": "SBT" | "BD" | "POD" | "RING" | "LID",
      "file_id": "<lowercase-hex>",
      "dxf": "dxfs/<file_id>.dxf",
      "match_json": "match/<file_id>.json"
    }
  ]
}
```

Match JSON keys are `<class>.<index>` or `<view>.<class>.<index>`
(where `<view>` ∈ `top_view` / `bottom_view` / `side_view`); values
are lists of handle-list match instances.

**Handles are raw and per-file.** The bundle ships one Match JSON per
DXF — there is no merged `<file_id[:8]>:` prefix. A role with N
sibling DXFs (e.g. BD top + bottom) appears as N separate entries in
`manifest.files`; iterate by role to find them.

## Output: the RuleChecking JSON contract

The function returns a dict keyed by rule name. Each value:

```python
{
    "<ruleName>": {
        "pass": bool,
        "text": str,                # overall rule description
        "rules": [SubRule, ...],    # may be empty
    },
    ...
}
```

Each SubRule:

| Key        | Type                                              | Meaning |
|------------|---------------------------------------------------|---------|
| `part`     | `"SBT"` \| `"BD"` \| `"POD"` \| `"RING"` \| `"LID"` | Role whose viewer renders this annotation |
| `file_id`  | `str` \| `None`                                    | Full id (not the 8-char short form) of the DXF the sub-rule's geometry lives in |
| `from`     | `handleID` \| `None`                               | Single source DXF handle (raw, unprefixed) |
| `to`       | `handleID` \| `None`                               | Single target DXF handle |
| `text`     | `str`                                              | Per-sub-rule message (non-empty) |
| `tol`      | `handleID` \| `None`                               | Annotation-only entity to highlight |
| `tol_text` | `str` \| `None`                                    | Label adjacent to `tol`; only meaningful when `tol` is set |

### Envelope invariants (enforced by `app/rule_check.py`)

- `rules` MAY be empty; `pass` and `text` are still required.
- When `rules` is non-empty, every sub-rule MUST carry non-empty `text`.
- A sub-rule MUST set at least one of `from`, `tol`.
- `to` MAY only be set when `from` is also set.
- Any sub-rule with non-null `from` / `to` / `tol` MUST also carry a
  non-null `file_id`.
- `tol_text` MAY only be set when `tol` is also set.

A violation raises `RuleCheckOutputError` and the rule-check worker
maps it to a job-level `error`. The adapter does NOT mutate the
output — pass-through verbatim once validation succeeds.

### Sub-rule display patterns

| Shape     | Fields                       | Viewer renders |
|-----------|------------------------------|----------------|
| Distance  | `from` + `to`                | Dashed segment along the shortest path between the two entities (vertex-vs-edge perpendicular-foot search); `text` at midpoint |
| Highlight | `from` only                  | Highlight `from`; `text` adjacent |
| Tolerance | `tol` (+ optional `tol_text`) | Highlight `tol`; `tol_text` adjacent. Independent of `from`/`to` — may co-exist on the same sub-rule |

See `app/external_rule_check/_dev_mock.py` for a concrete example of
all three patterns.

The formal contract — every invariant the adapter enforces and every
display rule the viewer implements — lives in
`openspec/specs/design-rule-checking/spec.md` under "RuleChecking JSON
output shape" and "External rule function contract". Read that when
you need the unambiguous version.

## Steps

1. **Read `_dev_mock.py`** for the wire-format shape; treat it as the
   template for the dict you emit.

2. **Add the rule** in the external module. While the codebase is
   still on the stub (`_stub.py` raises `NotImplementedError` outside
   `SMDR2_DEV_MOCK_DRC=1`):
   - For experimentation, extend `_dev_mock.py` — add one builder
     function and one new entry in the dict returned by
     `check_rules`.
   - For a real customer-bound rule, coordinate with the external
     team before adding code; the long-term home is their module
     that will replace `_stub.py`.

3. **Constants** for thresholds live at module scope, one per
   threshold, named in the rule's terms (`SUBSTRATE_TO_SMD_MIN_DIST_MM
   = 5.0`, not `THRESHOLD_1 = 5.0`).

4. **Navigate the bundle** by reading `manifest.json` and the
   per-file Match JSONs. Helpers like `_class_from_key` and
   `_candidates_by_role` in `_dev_mock.py` show the pattern. There
   is no shared helper module — own the small primitives you need
   next to the rule.

5. **Build the sub-rule list** per the patterns above. When a rule
   fails, emit a sub-rule for each offending entity-pair so the
   viewer has something to highlight; empty `rules: []` is valid
   only when there is genuinely nothing to draw.

6. **Add tests:**
   - Adapter-level tests in `tests/test_rule_check.py` cover the
     envelope contract — only touch them if you change the contract
     itself (e.g. a new sub-rule field).
   - Rule-specific tests belong with the external module owned by
     the external team.

7. **Verify:**
   - `uv run pytest tests/test_rule_check.py -v`
   - `uv run pytest tests/test_rule_check_job.py -v`
   - `uv run pytest tests/` — full suite still green

8. **End-to-end smoke** (dev-mode only):
   - `SMDR2_DEV_MOCK_DRC=1 uv run uvicorn app.main:app --reload`
   - Upload a product, save matches, click Rule Check, confirm the
     dashboard renders the rule card and the viewer renders the
     sub-rule highlight when clicked.

9. **No UI changes required.** The dashboard's rule-check modal walks
   the result dict generically and emits one card per `Rule*` with one
   `View in <part> →` link per sub-rule. The viewer's `?rule=…&idx=…`
   route picks up the sub-rule by key.

   *Exception:* if the rule is significant enough to deserve a
   documented behavior contract (real customer requirement, not a
   mock), add a `### Requirement:` block in
   `openspec/specs/design-rule-checking/spec.md`.

## Common pitfalls

- **Editing `app/rule_check.py` directly.** That file is the
  validation adapter; rule logic does not live there. Touching it
  for a new rule is a layering violation.
- **Forgetting `file_id` on a sub-rule with a handle.** Any
  `from` / `to` / `tol` requires `file_id` to be the **full** hex id
  (from `manifest.files[*].file_id`), not the 8-char short form. The
  adapter raises if you omit it.
- **Inventing a `from` / `to` list.** The contract is a single handle
  per field, not a list. Multi-entity highlight = multiple sub-rules,
  or use `tol` to add an annotation-only third entity to the same
  sub-rule.
- **Setting `to` without `from`.** The adapter rejects it. If you
  want a single entity highlighted, use `from` alone (or `tol`).
- **Parsing handles for a file prefix.** Bundle handles are raw and
  per-file — there is no `<file_id[:8]>:` prefix to split. Use the
  `file_id` on each `manifest.files[*]` entry to know which file each
  handle belongs to.
- **Hard-coding role.** If the rule legitimately spans more than one
  role, iterate `manifest.files` and group by `role` rather than
  assuming one DXF per role.
- **Threshold inversion bugs.** Write the comparator in the result
  text too (e.g. `"< 5 mm"` vs `">= 5 mm"`); a test that asserts the
  threshold value appears in `text` catches flipped signs.

## Output

After the rule is added, the agent should report:
- Which file(s) it changed and the new rule's name.
- The test commands run and their result.
- A pointer to `POST /api/products/{pid}/rule-check` (or the dashboard
  "Rule Check" button) so the human can see it fire end-to-end.

---
name: add-rule
description: Add a new Design Rule Check (DRC) rule to SMDR2. Use when the task is to add, define, or implement a new product-scoped rule that checks geometry / counts / relationships across the uploaded DXFs.
target: any-agent
---

Add a new Design Rule Check (DRC) rule to SMDR2.

This skill is agent-neutral — it describes the contract and procedure for
extending `app/rule_check.py`. Any agent (Claude, custom tooling, scripted
code-mod, etc.) can follow it.

DRC is **product-scoped**: one call to `check_rules(product_id, dxfs_by_role)`
sees every uploaded DXF in the product (keyed by role: `SBT / BD / POD /
RING`) and emits a result dict the dashboard renders + the viewer can
focus into. Adding a rule = adding one block inside that function.

## Required inputs from the human

Before writing code, the agent MUST collect these. If any are missing or
ambiguous, ask the human (use whatever clarification mechanism the agent
runtime provides) — do not guess.

1. **Rule name** — short identifier (`Rule4`, `BgaPitchCheck`, etc.); becomes the dict key.
2. **What it checks** — one sentence; ends up in the `text` field shown on the dashboard card.
3. **Which DXF role(s)** the rule needs (BD / SBT / POD / RING) and the failure-mode if a needed role is missing. Canonical pattern: fail with explanatory `text`, empty `rules: []`.
4. **Geometry kind** — distance? count? containment? angle? something else?
5. **Threshold(s)** — numeric values with units (mm) and the comparator (`<`, `<=`, `>`, `>=`, `==`).

## Inputs & outputs (the contract)

### Input shape: `dxfs_by_role`

```python
{
    "BD": {
        "file_id":       "abc123",
        "dxf_path":      "data/uploads/abc123.dxf",
        "match_json":    {                 # already user-saved
            "smd.0":       [["H1","H2","H3"], ["H4","H5","H6"], ...],
            "substrate.0": [["S1"]],
            "bga_ball.0":  [["B1"], ["B2"], ...],
        },
        "entity_shapes": { "H1": EntityShape(...), ... },
    },
    "SBT": {...},   # optional — may be missing
    "POD": {...},
    "RING": {...},
}
```

- `match_json` key = `<class>.<template_index>`; value = list of matches; each match is the handle list that makes up that template occurrence.
- `entity_shapes[handle]` gives `centroid`, `bbox`, `points` (ndarray of vertices), etc. — defined in `app/matching.py`.

### Output shape (per rule)

```python
{
    "Rule4": {
        "pass": bool,             # overall pass/fail
        "text": str,              # description shown on the dashboard card
        "rules": [                # 0..N concrete from→to sub-rules
            {
                "part": "BD",                # role; viewer routes here on click
                "from": ["H1", "H2"],        # source handle(s)
                "to":   ["S1"],              # target handle(s)
                "text": "BGA #3 → substrate = 3.2 mm (< 5 mm)",
            },
            ...
        ],
    },
}
```

The viewer draws the **shortest segment** between `from` and `to` (across all
vertices of all listed handles). `from` / `to` being lists is forward-compat —
single-entity rules just pass one handle.

## Reusable helpers (already in `app/rule_check.py`)

| Helper | Purpose |
|---|---|
| `_first_match_handles(mj, prefix)` | First `prefix.*` match's handle list — use for "the" substrate / "the" first SMD |
| `_all_match_groups(mj, prefix)` | All `prefix.*` matches as a list of handle-lists — one inner list per occurrence |
| `_all_handles_for_prefix(mj, prefix)` | Same, flattened — when you don't care about grouping |
| `_count_for_prefix(mj, prefix)` | Just the count |
| `_shortest_distance(shapes, hs_a, hs_b)` | Min distance (mm) between two handle groups' geometry; returns `None` when geometry can't be computed |

If a new geometric primitive is needed (containment, angle, bbox-overlap, …),
add it next to these as a module-private `_helper`. Keep it pure (no I/O).

## Steps

1. **Read the existing rules** in `app/rule_check.py` for tone / structure:
   - Rule1 — single-pair distance
   - Rule2 — cross-DXF count comparison
   - Rule3 — per-item distance loop

   New rules should match one of these shapes whenever possible.

2. **Pick a constant name** for any threshold and put it near the existing
   `SUBSTRATE_TO_SMD_MIN_DIST = 5.0` block (or beside the rule if it's
   single-use). One constant per threshold, named in the rule's terms.

3. **Insert the rule block** inside `check_rules` *before* `return results`:

   ```python
   # ---- Rule<N>: <one-line summary> -------------------------------------
   rule<N>_sub: list[SubRule] = []
   rule<N>_pass = False
   bd = dxfs_by_role.get("BD")            # or whichever role(s) you need
   if bd is None:
       rule<N>_text = "BD DXF required (not uploaded)"
   else:
       <pull handles via _first_match_handles / _all_match_groups>
       shapes = bd["entity_shapes"]
       if <required matches missing>:
           rule<N>_text = "<explain what's missing>"
       else:
           # compute geometry, set rule<N>_pass, append sub-rules
           ...
           rule<N>_text = "<rule description w/ thresholds>"
   results["Rule<N>"] = {
       "pass": rule<N>_pass,
       "text": rule<N>_text,
       "rules": rule<N>_sub,
   }
   ```

4. **Emit a sub-rule even on failure** so the viewer has something to draw —
   the existing rules all do this. The `text` on the sub-rule should include
   the measured value and the comparator.

5. **Add tests** in `tests/test_rule_check.py`. The file already exposes:
   - `_shape(handle, x, y)` — synthetic 1×1 square at (x, y)
   - `_bundle(match_json, shapes)` — wraps into the role bundle
   - `_check_envelope(result)` — asserts every rule has the right keys

   Cover at least: happy-path pass, failing threshold, missing-role /
   missing-match failure modes. Follow the naming of `test_rule1_*` /
   `test_rule3_*`.

6. **Verify**:
   - `uv run pytest tests/test_rule_check.py -v` — all green
   - `uv run pytest tests/` — full suite still green

7. **No UI changes required.** The dashboard's rule-check modal walks the
   result dict generically and emits one card per `Rule*` with one
   `View in <part> →` link per sub-rule. The viewer's `?rule=…&idx=…`
   route picks up your sub-rule by key.

   *Exception:* if the rule is significant enough to deserve a documented
   behavior contract (i.e., a real customer requirement, not a mock),
   add a `### Requirement:` block in
   `openspec/specs/design-rule-checking/spec.md` describing the rule and
   its scenarios, mirroring the existing Rule1 entry. For internal /
   experimental rules, skip this.

## Common pitfalls

- **Forgetting `entity_shapes`.** `_shortest_distance` needs `shapes`, not
  `match_json` — get it from `bd["entity_shapes"]`, not the match list.
- **Hard-coding role.** If the rule legitimately spans more than one role
  (e.g., "BGA count must agree across SBT and POD"), iterate roles
  explicitly — see Rule2 for the template.
- **Skipping the sub-rule list on failure.** Empty `rules: []` is a valid
  state (nothing to draw), but if the offending entities are known, emit
  them so the viewer can highlight what failed. Rule3 demonstrates.
- **Using centroids when shortest-distance is the right metric.** SMDR2
  geometry is mostly polygons / arcs flattened to polylines — centroids
  lie. Use `_shortest_distance` whenever the question is "are these two
  shapes close enough."
- **Threshold inversion bugs.** Write the comparator in the result text
  too (e.g., `"< 5 mm"` vs `">= 5 mm"`) — the test that asserts the
  threshold value is in `text` will catch flipped signs.

## Output

After the rule is added, the agent should report:
- Which file(s) it changed and the new rule's name
- The test command run and its result
- A pointer to `POST /api/products/{pid}/rule-check` (or the dashboard
  "Rule Check" button) so the human can see it fire end-to-end

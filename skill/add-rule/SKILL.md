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
3. **Which DXF role(s)** the rule needs (BD / SBT / POD / RING) and the failure-mode if a needed role is missing. Canonical pattern: fail with explanatory `text`, empty `rules: []`. Also ask: when a role legitimately holds **multiple DXFs** (top + bottom siblings, multiple revs), should the rule run per-file, aggregate across the role, or compare siblings against each other? See "Multi-DXF per role" below.
4. **Geometry kind** — distance? count? containment? angle? something else?
5. **Threshold(s)** — numeric values with units (mm) and the comparator (`<`, `<=`, `>`, `>=`, `==`).

## Inputs & outputs (the contract)

### Input shape: `dxfs_by_role`

```python
{
    "BD": {
        # First-file fallbacks (single-file-rule mental model). When the
        # role holds ≥ 2 DXFs these point at file_ids[0] / dxf_paths[0].
        "file_id":       "abc12345",
        "dxf_path":      "data/uploads/abc12345.dxf",
        # Authoritative lists. Single-file roles → length 1; multi-file
        # roles (top + bottom siblings, etc.) → length 2+.
        "file_ids":      ["abc12345", "def67890"],
        "dxf_paths":     ["data/uploads/abc12345.dxf",
                          "data/uploads/def67890.dxf"],
        "match_json":    {                 # already user-saved, merged
            "smd_2t.0":    [["abc12345:H1","abc12345:H2"], ...],
            "substrate.0": [["abc12345:S1"]],
            "bga_ball.0":  [["def67890:B1"], ...],
        },
        "entity_shapes": { "abc12345:H1": EntityShape(...), ... },
    },
    "SBT": {...},   # optional — may be missing
    "POD": {...},
    "RING": {...},
}
```

- `match_json` key = `<class>.<template_index>`; value = list of matches; each match is the handle list that makes up that template occurrence.
- `entity_shapes[handle]` gives `centroid`, `bbox`, `points` (ndarray of vertices), etc. — defined in `app/matching.py`.
- When the role has ≥ 2 files, every handle in `match_json` and every key in `entity_shapes` is prefixed with `{file_id[:8]}:` so handles from different files don't collide. See "Multi-DXF per role" below; the formal contract lives in `openspec/specs/design-rule-checking/spec.md` → "Per-role bundle merging and handle prefix".

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

## Multi-DXF per role

A `(product, role)` may hold any number of DXFs — typical cases are
"BD has both a `top` view and a `bottom` view", "SBT has a `multi`
plus an extra revision". `run_product_rule_check` merges every per-role
DXF into one bundle before calling `check_rules`, so a rule still sees
**one** `dxfs_by_role[role]` dict regardless of how many files
contributed.

The merge applies a handle prefix when the role holds ≥ 2 files:

- **Single-file role** (`len(file_ids) == 1`): handles are the raw DXF
  handles, no prefix.
- **Multi-file role** (`len(file_ids) > 1`): every handle in
  `match_json` and every key in `entity_shapes` is rewritten as
  `f"{file_id[:8]}:{raw_handle}"`. The prefix is applied in lockstep
  across `match_json` and `entity_shapes`, so any prefixed handle in
  the match JSON is guaranteed to resolve in the shape dict.

**Most rules don't need to know this.** `_first_match_handles` /
`_all_match_groups` / `_shortest_distance` treat handles as opaque
strings — they round-trip prefixed handles through `match_json` →
`entity_shapes` lookups without inspection. A rule that only asks
"is the substrate ≥ 5 mm from the first SMD" works on a multi-file
bundle with zero change.

**For rules that DO need file-of-origin**, use the documented helper:

```python
from app.rule_check import _split_handle_prefix

prefix, raw = _split_handle_prefix(h)
# Multi-file: prefix = "abc12345", raw = "7AF"
# Single-file: prefix = None, raw = h unchanged
```

Worked example — "BGA count on this role's two sibling DXFs must
agree" (cross-file count comparison rule):

```python
# ---- Rule<N>: BD sibling BGA counts must match -----------------------
rule_sub: list[SubRule] = []
rule_pass = False
bd = dxfs_by_role.get("BD")
if bd is None or len(bd["file_ids"]) < 2:
    rule_text = "BD requires ≥ 2 sibling DXFs (e.g. top + bottom)"
else:
    counts_by_file: dict[str, int] = {fid[:8]: 0 for fid in bd["file_ids"]}
    for h in _all_handles_for_prefix(bd["match_json"], "bga_ball"):
        prefix, _ = _split_handle_prefix(h)
        if prefix is not None:
            counts_by_file[prefix] = counts_by_file.get(prefix, 0) + 1
    values = list(counts_by_file.values())
    rule_pass = len(set(values)) == 1
    rule_text = (
        f"BGA counts across BD siblings: {counts_by_file} "
        f"({'agree' if rule_pass else 'DISAGREE'})"
    )
results["Rule<N>"] = {"pass": rule_pass, "text": rule_text, "rules": rule_sub}
```

The formal contract — exactly which fields the bundle carries, when
the prefix applies, and the opaque-handle invariant — lives in the
`design-rule-checking` capability spec
(`openspec/specs/design-rule-checking/spec.md`, requirement "Per-role
bundle merging and handle prefix"). Read that when you need the
unambiguous version; the snippet above is the how-to.

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
- **Parsing the prefix yourself.** Use `_split_handle_prefix(h)` —
  never inline a regex or string split against `:`. The prefix scheme
  is documented in the `design-rule-checking` capability spec; any
  rule that hand-rolls the parser will silently break the day the
  scheme changes (or, more likely today, the day someone adds a
  single-file role check and a handle of the form `7AF:something`
  trips a bespoke split).

## Output

After the rule is added, the agent should report:
- Which file(s) it changed and the new rule's name
- The test command run and its result
- A pointer to `POST /api/products/{pid}/rule-check` (or the dashboard
  "Rule Check" button) so the human can see it fire end-to-end

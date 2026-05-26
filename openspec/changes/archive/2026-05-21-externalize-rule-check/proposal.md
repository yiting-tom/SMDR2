## Why

Rule writing is moving out of SMDR2 in terms of ownership: an external team writes the rule logic and contributes it as a Python module checked into this repository. SMDR2 imports their module and calls into it with a bundle directory path; everything inside that module is the external team's domain. Their function consumes the existing Download-All-Match bundle (DXFs + per-file Match JSON + `manifest.json`) and returns a richer RuleChecking JSON than today's mock — sub-rules now carry single-handle `from` / `to` (so the viewer no longer has to compute shortest segments) and two new fields `tol` / `tol_text` for annotation-only highlights that aren't part of a distance check. The three internal mock rules in `app/rule_check.py` (Rule1/Rule2/Rule3) become dead weight once the external package is wired in.

## What Changes

- **BREAKING**: RuleChecking JSON sub-rule shape changes. `from` / `to` go from `list[handleID]` to `handleID | None` (single, optional). Two new fields are added: `tol: handleID | None` (annotation-only entity to highlight) and `tol_text: str | None` (label drawn next to `tol`). Invariants: a sub-rule with any handle field set MUST carry `file_id`; `rules` MAY be empty but each present sub-rule MUST have non-empty `text`.
- **BREAKING**: `app/rule_check.py:check_rules` no longer owns rule logic. It becomes a thin adapter that materialises the handoff bundle on disk, hands the directory path to the external team's in-tree module, and returns its RuleChecking JSON verbatim. The three mock rules (substrate-to-SMD distance, BGA count, every-SMD-within-5mm) are removed along with their geometric helpers (`_shortest_distance`, `_collect_segments`, `_point_to_segment_dist`).
- Viewer rendering rules change to match the new shape:
  - `from` + `to` present → draw a line between the two single entities, render `text` at the midpoint.
  - `from` only → highlight `from`, render `text` next to it.
  - `tol` present (independent of from/to) → highlight that entity; if `tol_text` is also present, render it next to the highlighted entity.
  - The viewer-side `shortestSegmentBetween` distance computation is dropped — endpoints come pre-resolved from the external package.
- Spec rewrite in `openspec/specs/design-rule-checking/spec.md`: replace the "RuleChecking JSON output shape" requirement, delete the three Rule1/2/3 mock requirements (they belong to the external team's spec now, not ours), update the rule-panel hover requirement to describe the new display semantics, and add a new "External rule function contract" requirement defining how SMDR2 invokes the external package and what it expects back.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `design-rule-checking`: RuleChecking JSON output shape changes (single-handle from/to + new tol/tol_text fields), three internal mock-rule requirements removed, new external function contract added, viewer rendering semantics updated.

## Impact

- **Code**:
  - `app/rule_check.py` — `check_rules` rewritten as a thin adapter; all geometric helpers and the three mock rules deleted.
  - `app/jobs.py` (or wherever `run_product_rule_check` lives) — already builds the per-role bundle; may need adjustment to also build the handoff bundle path the external function consumes, depending on the package's signature.
  - `app/static/canvas.js` — `drawFocusedSubRule` / `drawFocusedLabel` / `focusSubRule` rewritten for single-handle `from`/`to` + new `tol` / `tol_text` paths; `shortestSegmentBetween` and its segment-collection helpers can be deleted.
- **Tests**:
  - `tests/test_rule_check.py` — every assertion against `sub["from"]` / `sub["to"]` as lists rewrites to single-value comparison; new envelope test for `tol` / `tol_text`; existing Rule1/2/3 scenarios become tests of the adapter calling the external function (likely with a fake/mock).
  - `tests/test_rule_check_job.py` — needs to mock the external package so jobs run hermetically.
- **Dependencies**:
  - A new in-tree module owned by the external rule-checking team (exact path inside the repo TBD — captured in design.md and resolved at apply time when the team commits their module).
- **Persisted data**:
  - Existing `data/rule_check/{product_id}.json` files were written in the old shape; they MUST be re-run after deploy. We deliberately do NOT migrate them — the dashboard already supports re-running, and the format is internal.
- **Spec**:
  - `openspec/specs/design-rule-checking/spec.md` — three requirements modified, three deleted, one added (see Modified Capabilities).

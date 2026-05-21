## Context

Today `app/rule_check.py:check_rules(product_id, dxfs_by_role)` owns three mock rules (substrate-to-first-SMD-2T distance, SBT/POD BGA count agreement, every SMD-2T within 5 mm of substrate) and a small geometry library (`_collect_segments`, `_point_to_segment_dist`, `_shortest_distance`). It is called from a worker process in `app/jobs.py:_rule_check_worker`, which first merges every role's per-file Match JSON + entity shapes into one bundle (applying the `<file_id[:8]>:` handle prefix when a role has ≥2 files) and then hands that bundle to `check_rules`. The result is persisted at `data/rule_check/{product_id}.json` and consumed by the viewer (`app/static/canvas.js:drawFocusedSubRule` and friends), which re-derives the shortest segment between the `from` / `to` handle lists at render time via `shortestSegmentBetween`.

The external rule-checking team now owns the rules. They contribute their rule logic as a Python module checked into this repository (the boundary is import-only — SMDR2 calls into their module by name, but the source lives in this repo and ships in the same deploy artifact). Their function consumes the same handoff bundle SMDR2 already builds for the "Download All Match" flow (assembled by `app/drc_bundle.py:build_bundle`, conforming to `openspec/specs/design-rule-checking/drc-manifest.schema.json`, version `1.2.0`). They return a richer RuleChecking JSON: `from`/`to` collapse from `list[handleID]` to a single `handleID | None` (the external team pre-computes the closest pair, so the viewer no longer has to), and two new optional fields appear — `tol: handleID | None` (an annotation-only entity that should be highlighted but isn't part of a distance check) and `tol_text: str | None` (a label drawn next to `tol`).

This change rewrites `check_rules` as a thin adapter to that external package, drops the three mock rules and their geometry helpers, updates the viewer to render the new shape, and rewrites the spec to remove our ownership of the rule logic while documenting the boundary contract.

## Goals / Non-Goals

**Goals:**
- `check_rules` becomes a thin adapter: materialise the handoff bundle on disk, call the external team's in-tree function with that path, return the result. No rule logic of our own.
- Sub-rule shape migrates to single-handle `from` / `to` plus optional `tol` / `tol_text` for annotation-only highlights.
- Viewer renders the four display modes the user specified: from+to (line + midpoint label), from only (highlight + adjacent label), tol (highlight only), tol+tol_text (highlight + adjacent label). `tol` is independent of `from`/`to` and may coexist with them in the same sub-rule.
- Spec reflects the new ownership boundary: SMDR2 owns the contract (input bundle, output shape) but not the rule logic. The three mock-rule requirements (Rule1/Rule2/Rule3) move out of our spec entirely.
- Tests use a fake external function so the suite stays hermetic and fast.

**Non-Goals:**
- Migrating existing `data/rule_check/{product_id}.json` files written in the old shape. The dashboard already supports re-running rule check; users re-run after deploy. No format converter, no compatibility branch.
- Adding new rules. This change is purely structural — the new format is wired up but the *rule set itself* is whatever the external package decides.
- Changing the handoff bundle schema (`drc-manifest.schema.json`) or the `app/drc_bundle.py` builder. The bundle is reused as-is. If the external team needs something different at the boundary, that's a separate change.
- Changing the job system (`app/jobs.py` worker pool, `submit_rule_check`, `GET /api/jobs/{job_id}` polling). The worker still does the heavy lifting in a subprocess — only the *body* of `_rule_check_worker` changes.

## Decisions

### Boundary input: bundle on disk vs. in-memory dict

**Decision**: Pass an on-disk bundle directory (the same layout `build_bundle` writes inside the zip — `manifest.json`, `dxfs/<file_id>.dxf`, `match/<file_id>.json`) to the external function. The worker writes the bundle to a temp directory at the start of the job and removes it after the call returns.

**Why**:
- The bundle layout is already specified, schema-validated, and shipped to this exact external team in the "Download All Match" zip. Reusing it means the external function consumes one format end-to-end (offline export and live invocation are the same input shape), which is also what gives them the option to debug a failing product by extracting the zip we already give them.
- Passing dicts in-memory would require us to invent a second contract for the boundary and keep it in sync with the manifest schema. Not worth it.
- The merged `dxfs_by_role` dict the current worker builds (with `<file_id[:8]>:` prefixed handles) is an *internal* artifact of our mock checker — the external team's contract is per-file, unprefixed handles, which is exactly what the bundle ships. Dropping the merge step from the worker is a simplification, not a loss.

**Alternative considered**: import the external module and pass it `dxfs_by_role` directly. Rejected — see above; we'd be inventing a parallel contract.

**Alternative considered**: subprocess / CLI invocation. Rejected — the external team's code lives in-tree, so a direct Python call is the natural boundary.

### Where the external function gets called

**Decision**: `app/rule_check.py:check_rules(product_id, bundle_dir)` is the adapter. Signature changes from `(product_id, dxfs_by_role)` to `(product_id, bundle_dir: str | Path)`. Internally it calls the external package and returns the result verbatim. `app/jobs.py:_rule_check_worker` is responsible for materialising the bundle directory before calling `check_rules` and cleaning it up after.

**Why**: Keeps the call site discoverable (`check_rules` is still the entry point named in the spec) but removes its responsibility for rule logic. The worker stays in charge of process boundary concerns (temp dir, cleanup, exceptions).

**Alternative considered**: drop `check_rules` entirely and have the worker call the external package directly. Rejected — having a named adapter makes it trivial to monkey-patch in tests and gives us one place to add cross-cutting concerns (e.g., timeout, error mapping) later without touching the worker.

### External module location

**Decision**: Hold the in-tree module path + entry point as an open question to be resolved at apply time. The external team's code lives in this repo (likely under `app/`), so once they commit their module we replace the placeholder import. Expected shape:

```python
# Placeholder — actual in-tree path provided by the external team at apply.
from app.<external_module> import check_rules as _external_check_rules

def check_rules(product_id: str, bundle_dir: str | Path) -> RuleResult:
    result = _external_check_rules(product_id, str(bundle_dir))
    _validate_envelope(result)
    return result
```

**Why**: The user confirmed the external team's code will be in-tree, but the exact module path / function name isn't fixed yet. Putting a guessed path in the spec would force a follow-up edit. The proposal already documents the boundary intent; nailing down the module path is a small, isolated apply-phase task. Because the module ships in our repo (not as a PyPI dep), there's no `pyproject.toml` pin to maintain — the import either resolves or it doesn't, and CI catches that immediately.

### Old `dxfs_by_role` merge — keep or delete

**Decision**: Delete it. The worker now builds the on-disk bundle (via `app/drc_bundle.py:build_bundle` or a sibling that writes a directory instead of a zip) and hands the path to `check_rules`. The merge step that produces prefixed handles in `dxfs_by_role` goes away because the external function consumes per-file, unprefixed handles — that was always the boundary contract (it's enshrined in the "Match JSON handles are not pre-merged" scenario of the existing spec).

**Why**: The merge existed only to feed our internal mock checker. Removing it deletes ~60 lines of worker code, removes the `<file_id[:8]>:` prefix handling from the rule-check hot path entirely, and aligns the live worker path with the offline handoff zip — same bundle, different transport.

**Risk**: any in-process consumer that relied on the merged `dxfs_by_role` would break. Verified there are none — `check_rules` is the only caller in the codebase.

### Viewer rendering rules

**Decision**: `app/static/canvas.js:focusSubRule` records single-handle `from` / `to` (defaulting `null`) plus new `tol` / `tol_text`. `drawFocusedSubRule` collects the highlight set from whichever of `from` / `to` / `tol` are present. When both `from` and `to` are present, the viewer runs a vertex-vs-edge perpendicular-foot search across the two primitives' geometries (single-handle variants of the old `collectHandlesSegments` + `shortestSegmentBetween`) and draws a dashed segment along that shortest path with the sub-rule text at the midpoint. When only `from` is present, the viewer falls back to `primitiveCenter(from)` for label anchoring; the `tol` / `tol_text` path uses the same bbox-centre anchor independently.

**Why**: bbox-centre would put the annotation line through entity interiors on long thin shapes (a fiducial cross or a substrate edge), which reads as wrong to the engineer. Perpendicular-foot search keeps the line pinned to the actual nearest pair of points across the two entities' edges — same heuristic the pre-change viewer used, just adapted from list-of-handles to single-handle inputs. The external module still owns "which entity is from / which is to" (one closest pair across all candidate instances); the viewer only resolves the closest point pair WITHIN that fixed pair of entities, which is purely a presentation concern.

**Alternative considered**: ship the line endpoints in the RuleChecking JSON (`from_point`, `to_point`). Rejected — that bakes a coordinate-precision decision into the boundary contract and forces the external module to know about the viewer's segment-collection heuristics for curves (circles flattened to 32-gon, etc.). Cheaper to keep endpoint search client-side.

### Test strategy

**Decision**: Replace the current `tests/test_rule_check.py` substantively. The envelope test stays (validate new shape on a result dict). The three rule-specific scenarios (Rule1/Rule2/Rule3 expected outputs given fixture bundles) are deleted — those properly belong to the external team's test suite. A new test installs a fake external function on the adapter, asserts the adapter passes the bundle path through, and asserts the worker writes the result verbatim to `data/rule_check/{product_id}.json`. `tests/test_rule_check_job.py` likewise mocks the external function so the worker path stays hermetic.

**Why**: We can't usefully test rules we don't own. We CAN test the boundary — bundle is materialised correctly, results round-trip to disk, job-status accounting (`pass_count` / `fail_count` / `rule_count`) still works on the new shape.

## Risks / Trade-offs

- **External module not yet committed**: import path is a placeholder until the external team commits their module. Mitigation: design.md flags it as an open question; apply-phase task is "wire actual import" and is small enough to do in one commit. If the team hasn't committed by then, land the adapter + validator + tests behind a stub external function (raises `NotImplementedError`) so the rest of this change can ship and rule-check stays disabled until the module arrives.
- **Old persisted rule_check.json files are dead on first read after deploy**: dashboard code that reads them must tolerate either shape, or we explicitly delete them on deploy. Mitigation: spec the dashboard to treat an unrecognised shape as "no rule check yet, please re-run" — failing soft is cheaper than a migrator we'd never reuse.
- **Sub-rule with neither from/to/tol set**: technically allowed by `handleID | None` everywhere, but useless (nothing to highlight). Mitigation: spec it out — a sub-rule MUST set at least one of `from`, `tol`. The adapter validates this on the way out and raises if violated, so a misbehaving external function fails loudly instead of silently producing un-rendered sub-rules.
- **Bundle materialisation cost on the hot path**: writing DXF + Match JSON to a temp directory every job is more I/O than the current in-memory merge. For SMDR2's bundle sizes (a handful of DXFs, each a few MB) this is well under a second. If profiling later shows it matters, swap for an `os.symlink`-based bundle pointing at the already-on-disk source files. Not worth doing pre-emptively.

## Migration Plan

1. Implement the adapter + worker rewrite + viewer changes + tests on the branch.
2. Wire the actual import from the external team's committed module (or keep the stub if they haven't landed yet).
3. Deploy. As part of deploy, delete the existing `data/rule_check/*.json` files (they're in the new shape's blast radius and easier to regenerate than migrate). Document the wipe in the deploy notes — users with persistent products will see "no rule check" on first dashboard load and re-run.
4. No DB migration; rule check results are file-system artifacts only.
5. Rollback: revert the branch. Old `check_rules` and viewer still work against any newly-written rule_check.json from prod (they were never deleted on the old code's read path), so rollback is symmetric — wipe the rule_check directory again and let users re-run.

## Open Questions

- Exact in-tree module path and function name of the external team's contribution. Resolved at apply by reading their committed module; until then design.md uses `app.<external_module>.check_rules` as the placeholder.
- Whether the external function takes the bundle directory path or the zip path. Defaulting to *directory* (cheaper for us — skip the zip step) and noting that if the external team prefers a zip we can switch by changing one line in the worker.
- Whether the external module reuses any helpers from `app/` (e.g., `app.matching`, `app.dxf`). That's their call — as long as the function signature and return shape match the contract, anything goes inside.
- Should the worker pass `dev_overrides_snapshot` to the external function? Today's worker calls `apply_snapshot` so dev parameter overrides reach `check_rules`. Once rules are external, dev overrides are no longer relevant to rule check. Decision: drop the snapshot from `_rule_check_worker` and the `submit_rule_check` signature. If the external team later wants per-call config, that's a contract addition, not an override-leak.

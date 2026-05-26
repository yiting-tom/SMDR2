## Context

The RING / LID per-product XOR rule was introduced when the downstream
rule-check and DRC bundle pipelines could only reason about one of the
two cap-side roles at a time. The constraint is enforced today at three
layers:

1. **Server upload** (`app/main.py:403-421`) — opposite-role uploads
   return HTTP 409 with the conflicting sibling file id in the body.
2. **DRC bundle build** (`app/drc_bundle.py:83-92`) — `build_manifest`
   raises `ValueError` if the file list carries both roles, framed as
   a defensive "upstream drift" check.
3. **UI placeholders** — dashboard `ringLidPairCell`
   (`app/static/dashboard.js:528-544`) and viewer `renderRingLidPair`
   (`app/static/canvas.js:200-220`) render the opposite half as a
   non-interactive `slot.empty.disabled` / `role-btn.empty.disabled`
   placeholder with an explanatory `title`.

Beyond enforcement, the rule is described in:
- `openspec/specs/product-files/spec.md` (a top-level Requirement and
  five scenarios)
- `openspec/specs/viewer-ui/spec.md` (split-pair placeholder rules and
  three scenarios)
- Docstrings in `app/products.py`, `app/product_views.py`, `app/rule_check.py`

Downstream — the rule-check role bundler in `app/jobs.py:422-456` and
`run_product_rule_check` in `app/main.py:1078-1144` — is already
role-generic: it groups files by `dxf_role` and emits one
`role_spec` per role found. `app/rule_check.check_rules` only inspects
`SBT`/`BD`/`POD` by name (`Rule1` and `Rule2`); RING and LID flow
through harmlessly. There is therefore no algorithmic obstacle to
letting both coexist — the XOR was always a policy guardrail, not a
correctness requirement.

Customer feedback: packaging engineers routinely work on parts that
have both a RING and a LID and want one product card to represent the
whole part rather than splitting it across two products.

## Goals / Non-Goals

**Goals:**
- A product MAY hold files under both `RING` and `LID` simultaneously.
- Uploading either role to a product that already has the other SHALL
  succeed with HTTP 200, with no opposite-role eviction.
- The DRC bundle SHALL include both RING and LID entries in
  `manifest.files` when both are present; downstream rule-check SHALL
  receive both role bundles in `dxfs_by_role`.
- The dashboard's 4th cell SHALL keep the RING-on-left / LID-on-right
  layout, but each half is independently fillable; no half is rendered
  as a disabled placeholder because of the other.
- The viewer's role switcher SHALL show both halves concurrently when
  both roles have files, each behaving as its own single-/multi-DXF
  slot.

**Non-Goals:**
- No new rule semantics for RING vs. LID — `Rule1` / `Rule2` still
  consume only SBT/BD/POD; whether future rules pair RING and LID is
  out of scope.
- No DB schema change. The XOR was always application-layer.
- No migration of historical "either-or" products to "both" — existing
  products simply gain the ability to accept the opposite-role upload
  going forward.
- No change to the 4-position toolbar layout. The 4th position is still
  the RING|LID pair; only the disabled-half rules go away.

## Decisions

### D1. Drop the XOR check at every layer, do not keep a feature flag

We delete the upload-handler branch outright rather than gating it
behind a flag. Alternatives considered:
- **Feature flag** — adds dead code and complicates spec/test surfaces
  for a behaviour change with no rollback signal we actually want.
- **Per-library opt-in** — premature; we have one customer asking for
  this and no evidence anyone wants to keep the XOR.

The change is breaking only in the "previously a 409, now a 200" sense,
which is a relaxation. No client error-handling path was relying on the
409 as a feature.

### D2. DRC bundle includes both roles as independent `manifest.files` entries

`manifest.files` is already a flat list of `{file_id, dxf_role, ...}`
entries — there is no aggregation by role at the manifest level. We
simply remove the `build_manifest` XOR guard and let the existing
per-file emission cover both roles. The downstream DRC consumer sees
the same shape as today; we treat "RING and LID coexist" as the new
normal shape, not a special case.

Alternative: emit a separate `cap_side` group in the manifest. Rejected
— the manifest is intentionally role-flat and downstream contracts
already key on `dxf_role`.

### D3. Rule-check role bundle keeps both roles' shapes available

`app/jobs._rule_check_worker` already iterates `role_specs` and writes
one entry per role into `dxfs_by_role`. No code change is needed in the
worker. `check_rules` doesn't read `dxfs_by_role["RING"]` or
`["LID"]` today; adding entries there is a no-op for current rules and
unlocks future rules without further plumbing.

### D4. UI: 4th cell becomes two independent slots; no shared "pair" state

The dashboard `ringLidPairCell` keeps its outer `.slot-pair` container
(so CSS keeps the side-by-side layout) but the inner `slotCell` calls
no longer pass a `disabledReason` derived from the opposite half. The
viewer's `renderRingLidPair` is simplified the same way and its
"both files present (server should have rejected)" console warning is
removed — that branch is now the normal case.

CSS: the `.slot.empty.disabled` and `.role-btn.empty.disabled`
selectors stay in `style.css` because they are still reachable from
the existing `slotCell` API (`disabledReason` is still a parameter);
they just stop being triggered by the RING/LID pair specifically.

### D5. Spec deltas: remove the Requirement entirely, do not soften it

The "RING / LID per-product mutual exclusion" requirement in
`product-files` is removed as a whole block (with all five scenarios).
The neighbouring "Multiple DXFs per (product, role)" requirement is
updated to drop its parenthetical cross-reference.

In `viewer-ui`, the split-pair rendering rules are rewritten to make
both halves independently fillable, and the two "disabled when opposite
holds file" scenarios are replaced with "both halves can be active
concurrently" scenarios. The 4-position toolbar contract is unchanged.

### D6. Test rewrites

The two HTTP 409 API tests are replaced with positive coexistence
tests that confirm sequential RING-then-LID and LID-then-RING uploads
both return HTTP 200 and both files surface under
`files_by_role_all`. A new DRC-bundle test asserts that
`build_manifest` for a product with both roles emits both entries in
`manifest.files`. The rule-check test surface gains one case where a
product carries both RING and LID alongside SBT/BD/POD, asserting that
the run completes and the result JSON shape is unaffected.

## Risks / Trade-offs

- **[Risk] Downstream DRC consumer may have its own XOR assumption.**
  → Mitigation: the manifest is role-flat and the consumer has been
  fed multi-role bundles since the multi-DXF-per-role work. We
  document the new "both cap roles may appear" shape in the
  `product-files` per-role-merging Requirement's rationale, and the
  bundle version field (`BUNDLE_VERSION` in `drc_bundle.py`) stays
  unchanged because the schema itself isn't changing.
- **[Risk] Existing customer workflows rely on the 409 as
  "auto-select cap role" UX.** → Mitigation: the dashboard already
  shows the RING|LID split visually; users who used to upload to RING
  and have LID rejected as a "wrong slot" hint will now successfully
  upload to LID. Net behaviour matches what new users expect, and the
  per-slot file rows still let the user delete the wrong file.
- **[Trade-off] We lose the "single cap role per part" hint at the
  data layer.** → Acceptable: the rule was a UX scaffold, not a
  correctness invariant. Future rule-check rules that require exactly
  one cap role can re-enforce at rule-evaluation time without
  resurrecting the upload-layer block.
- **[Risk] Stale clients (pre-deploy tabs) may still render the
  opposite half as `.disabled` after the server starts accepting
  both.** → Mitigation: the next dashboard refresh tick (~3s) will
  re-fetch and re-render with both halves enabled. No persistent
  state.

## Context

`flatten_for_render(dxf_path)` opens the DXF with `ezdxf.readfile`, then
drives a `Frontend(ctx, backend).draw_layout(msp, finalize=True)` against a
custom `JSONBackend` that records primitives. The backend's
`set_current_entity` hook tags primitives `decorative: True` when the
entity's `dxftype()` is in `DECORATIVE_DXFTYPES = {"TEXT", "MTEXT",
"DIMENSION", "HATCH"}`. Downstream consumers (matcher, rule-check,
selection/chain in the viewer) filter on `decorative === true`, but the
viewer's render loop draws everything regardless.

The recent commit `48100bf` added a HATCH-bounded → `circle` (filled)
promotion inside `draw_filled_paths`, on the theory that a HATCH whose only
boundary is a circular edge should ride the same `ctx.arc` + sub-pixel dot
batching path as a real CIRCLE entity. That helps the bounded-by-circle
case but does nothing for the common packaging-DXF case where HATCH
boundaries are irregular shapes (annulus, slot, etc.) and the only effect
is render cost.

User finding (semiconductor packaging engineer, this repo's target user):
in real packaging DXFs all HATCH is decorative noise and should never
reach the render pipeline at all.

## Goals / Non-Goals

**Goals:**
- HATCH entities produce zero primitives in `flatten_for_render`'s output.
- The strip happens at parse time, before `Frontend.draw_layout`, so no
  render budget is spent on them.
- Downstream consumers (matcher, rule-check, DRC bundle) need no change —
  they already filtered on `decorative === true` and now simply see no
  HATCH-sourced rows at all.
- Existing `decorative` infrastructure stays for TEXT/MTEXT/DIMENSION.

**Non-Goals:**
- Not making HATCH stripping configurable per upload — packaging DXFs
  uniformly treat HATCH as noise; a flag would be dead weight.
- Not removing the `draw_filled_paths` circle-promotion code path —
  it stays for any future non-HATCH filled-circle source.
- Not preserving HATCH for any downstream analysis — if a later capability
  needs HATCH metadata it can read the DXF separately.

## Decisions

**Strip via `msp.delete_entity()` before `draw_layout`** (over: filter in
backend, or `entity_filter` on Frontend).

- Why: cheapest and most explicit. One pass over `msp.query("HATCH")`,
  call `msp.delete_entity(h)` for each, then drive the existing
  `Frontend.draw_layout` unchanged. Zero per-entity branching inside the
  hot backend loop.
- Alternative considered — backend-level filter: leave entities in place,
  short-circuit `set_current_entity` to skip drawing when
  `dxftype() == "HATCH"`. Rejected because ezdxf's Frontend still does
  setup work (visit boundary paths, compute hatch patterns) before
  delegating to the backend, so the cost isn't saved.
- Alternative considered — `Frontend(entity_filter=lambda e: e.dxftype() != "HATCH")`:
  more elegant but couples to ezdxf's filter API; explicit deletion is
  obvious to anyone reading `flatten_for_render`.

**Remove `"HATCH"` from `DECORATIVE_DXFTYPES`**.

- Why: dead-code hygiene. After strip, no HATCH ever reaches the backend,
  so the `dxftype() in DECORATIVE_DXFTYPES` check for HATCH is unreachable.
  Keeping it would mislead future readers into thinking HATCH primitives
  exist (just decoratively-flagged).
- The set keeps TEXT, MTEXT, DIMENSION because those continue to be
  rendered (with `decorative: true`) and filtered downstream.

**Edit the dxf-pipeline spec in-place** (over: write a delta spec under
`openspec/changes/drop-hatch-entities/specs/`).

- The spec currently asserts three HATCH-bounded-circle scenarios that
  become wrong post-change. Treating this as a modified capability means
  updating the canonical spec file directly during apply — the change's
  archive captures the diff. No delta-spec scaffolding is needed because
  the modification removes scenarios rather than introducing parallel
  behavior.

## Risks / Trade-offs

- [Loses HATCH-bounded-circle fast path] → Mitigation: in real packaging
  DXFs the BGA balls are CIRCLE or LWPOLYLINE entities, not HATCH-bounded;
  the polyline → circle promotion path (commit `c026ab5`) still covers
  them. The HATCH-bounded fast path was speculative and the user
  explicitly opted out of it.
- [Future need to render HATCH for a different DXF flavor] → Mitigation:
  the strip is one line of code and easily gated behind a kwarg if a
  caller ever needs it. For now YAGNI.
- [Test fallout] → Mitigation: the three HATCH tests in
  `tests/test_dxf.py` (`test_hatch_bounded_by_circle_emits_filled_circle`,
  `test_hatch_bounded_by_polyline_circle_emits_filled_circle`,
  `test_hatch_multi_subpath_stays_filled_polygon`) become wrong; tasks
  list calls for replacing them with a single
  `test_hatch_emits_no_primitives` that asserts the new contract.

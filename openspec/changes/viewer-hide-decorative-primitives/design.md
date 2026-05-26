## Context

`app/dxf.py` flattens DXF entities into a primitive list via
ezdxf's `Frontend` / `RenderContext`. Entities of types listed in
`DECORATIVE_DXFTYPES = {"TEXT", "MTEXT", "DIMENSION"}` (and HATCH
at `app/dxf.py:508`) cause `_PrimitiveBackend._decorative = True`
to be set during `enter_entity`; every primitive emitted while that
flag is on inherits `prim["decorative"] = True`. The flag is the
universal "this is non-load-bearing, skip in the workflow" marker —
matching pipelines, library scans, and click-to-select code all
honour it.

The viewer canvas (`app/static/canvas.js`) is the only consumer
that ignores the flag. It renders every primitive in the array,
unfiltered. When ezdxf's drawing addon emits per-character glyph
outlines for TEXT / MTEXT, the canvas draws them — and on machines
that lack the DXF's referenced font, ezdxf's font fallback emits
character-bounding-box rectangles instead of real glyph paths, so
the user sees rows of empty boxes ("一格格的長方形文字").

## Goals / Non-Goals

**Goals:**
- Stop rendering decorative primitives in the viewer canvas.
- Zero impact on the `/api/files/{file_id}/primitives` API contract
  (still returns every primitive; viewer filters).
- Zero impact on matching, selection, library scan, side-region
  splitting, scan-all overlay, or any other consumer.
- No new UI surface, no new toggle, no settings.

**Non-Goals:**
- Restoring correct text rendering by configuring ezdxf font
  search paths or installing AutoCAD .shx fonts.
- Adding a "Show text" toggle in the layer panel (possible future
  work; tracked separately if asked).
- Changing the back-end DXF flattener to drop decorative primitives
  entirely (would break future consumers that may want them).

## Decisions

### Decision: Filter on the front end, not the back end

The simplest valid scope is a `if (p.decorative) continue;` guard
inside the canvas render loop, before the primitive's per-type
draw branches. The back end keeps shipping decorative primitives
unchanged.

**Rationale:** the back end's responsibility is to flatten DXF
faithfully and tag what is decorative — that's done. Hiding the
flag from any future consumer (e.g. a developer who wants to add
a "Show text" toggle) is a regression of optionality for no gain.
A 1-line front-end guard is the cheapest fix and the easiest to
revert.

**Alternative considered: drop decorative primitives in
`_PrimitiveBackend._append`.** Rejected — would couple "render
choice" to "back-end emit" and prevent future toggle work without
re-plumbing the flattener.

### Decision: Skip in the lowest common render path, not per-type

`canvas.js`'s render loop dispatches per-primitive `type` (line,
circle, point, path). The guard lives once, at the top of the
per-primitive body, so it applies to every type uniformly. A
decorative `circle` or `line` (rare but possible — HATCH boundary,
DIMENSION arrow) is also skipped.

**Rationale:** uniformity matches the `decorative` flag's semantic
("this is not load-bearing, skip"), which is type-agnostic. Per-type
guards would risk missing one.

## Risks / Trade-offs

- **Risk:** a real, load-bearing primitive somehow ends up
  `decorative=true` (mis-tagging in `_PrimitiveBackend`) and goes
  invisible in the viewer. → **Mitigation:** the back-end tagging
  rule is narrow (specific dxftypes only) and covered by existing
  `tests/test_dxf.py` coverage. No mis-tag known today.
- **Risk:** users who explicitly want to see DIMENSION arrows
  (e.g. when manually QA-ing a DXF) lose visibility. → **Mitigation:**
  the layer panel + future "Show decorative" toggle (out of scope
  here) is the right surface for that. Today's users have not asked
  for it; the boxy text artefact is the only feedback received.
- **Trade-off:** the viewer no longer matches "shows everything in
  the DXF" intuition. Acceptable because non-load-bearing entities
  were already invisible to every other workflow step — the viewer
  now agrees with them.

## Migration Plan

Single commit; no data migration; no API change. Rollback is the
inverse commit.

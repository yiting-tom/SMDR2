## Context

The `mark-side-regions` change (proposed earlier, implemented, not yet
archived) introduced two per-file world-space rectangles —
`frontside_rect` and `bottomside_rect` — and a Match-JSON key
prefixer that splits each match instance into the rectangle that
contains its bbox center. The implementation spans
`app/files.py` (DB schema + dataclass), `app/side_regions.py` (pure
helper for bbox-center containment and prefix assembly),
`app/main.py` (`SideRegionsRequest`, `PATCH /api/files/{id}/side-regions`,
save-match wiring), `app/static/canvas.js` (`sideRects` state,
`SIDE_STYLES`, `enterMarkMode`, sides-menu), and the sides-menu
HTML. There are 115 references to `frontside` / `bottomside` across
`app/` and `tests/`.

Packaging engineers laying out a DXF sheet ([[project_smdr2_workflow]])
routinely place three views — top, bottom, and side — on the same
sheet. The internal name `frontside` is a misnomer for top-down
artwork; the engineer thinks "top view" / "bottom view" / "side view".
The Match JSON consumers (rule reports, BOM exports) need three
prefix strings so they can distinguish a side-view ball from a stray
unlabelled ball. The implementation work is largely mechanical
(rename + add a third branch), but the cross-cutting nature — DB
schema + Python + JS + tests + persisted JSON outputs — needs a
single coordinated plan so a half-applied rename doesn't leave the
system inconsistent.

## Goals / Non-Goals

**Goals:**
- A third per-file world-space rectangle `side_view_rect`, captured,
  persisted, and overlay-rendered in the same way as the existing two.
- Internal naming consistent with the engineer's vocabulary
  (`top_view` / `bottom_view` / `side_view`) end-to-end: DB columns,
  Python symbols, JS state, CSS classes, button labels, JSON prefixes.
- A mark-mode UX that handles "any subset of the three views is
  present" — the engineer can mark only top + side, only side, all
  three, or any combination.
- Deterministic Match JSON output under overlapping rectangles
  (priority `top_view > bottom_view > side_view`).

**Non-Goals:**
- Generalising to an arbitrary number of named regions. Only three
  views are planned and the explicit hard-coding keeps the helper,
  DB schema, and UI lean.
- Migrating existing on-disk `data/match/{file_id}.json` outputs. Old
  files keep their old prefixes until the engineer re-runs Save Match,
  at which point the existing PATCH-side-regions cache-invalidation
  hook handles regeneration.
- Changing matcher internals ([[pattern-matching]]). The side label is
  applied at serialisation time only.
- Adding rotated or non-axis-aligned regions.

## Decisions

### Decision 1: Rename DB columns via `ALTER TABLE ... RENAME COLUMN`

SQLite ≥ 3.25 supports `ALTER TABLE ... RENAME COLUMN`, which preserves
data in-place. Python 3.10+ on macOS / Linux bundles SQLite well past
3.25, so the rename is safe in supported environments.

In `app/files.py` `_migrate()`:
1. If `frontside_rect` column exists → `RENAME COLUMN frontside_rect TO top_view_rect`.
2. If `bottomside_rect` column exists → `RENAME COLUMN bottomside_rect TO bottom_view_rect`.
3. If `side_view_rect` column missing → `ADD COLUMN side_view_rect TEXT`.

The `CREATE TABLE IF NOT EXISTS` block uses the new column names for
fresh DBs.

**Alternatives considered:**
- *Add new columns, copy data, leave old columns NULL*: leaves dead
  columns behind, requires future cleanup, and confuses anyone
  reading the schema. Rejected.
- *Rebuild the table* (create new, copy rows, drop old, rename): more
  code, more lock time, no benefit over `RENAME COLUMN` given the
  version floor. Rejected.

### Decision 2: Helper signature takes three explicit `Optional[Rect]` parameters

`split_matches_by_side` and `side_prefix_for` in
`app/side_regions.py` keep the explicit, named-parameter shape but
gain a third `side_view: Optional[Rect]` parameter:

```python
def side_prefix_for(
    handles, shapes,
    top_view: Optional[Rect],
    bottom_view: Optional[Rect],
    side_view: Optional[Rect],
) -> Optional[str]: ...
```

`counts` dict gains a `side_view` key alongside `top_view`,
`bottom_view`, `unassigned`. The returned prefix string is one of
`"top_view"`, `"bottom_view"`, `"side_view"`, or `None`.

**Alternatives considered:**
- *Pass a `dict[str, Optional[Rect]]`*: more flexible but invites
  arbitrary keys, weakens type-checking, and complicates the priority
  order (we'd need to also pass an order list). Rejected per the
  hard-code-three goal.

### Decision 3: Overlap priority `top_view > bottom_view > side_view`

Containment is checked in order. The first rectangle that contains
the bbox center wins. This is deterministic and stable under
rectangle edits.

The priority reflects packaging-engineer intuition: top view is the
primary face, bottom view is the secondary face, side view is the
auxiliary cross-section.

### Decision 4: Three-step mark flow with Enter / bare-click / Esc

Mark mode cycles `top_view → bottom_view → side_view`. At each step
the status hint reads `MARK <view> · drag a rectangle`. While in a
step:

- **Left-press + drag + release** → captures a provisional rectangle
  for the current view. The provisional rect renders live during the
  drag.
- **Enter** → commits the current view's rectangle (provisional one
  if drawn this step, otherwise the previously-stored one) and
  advances to the next view. After the third view's Enter, mark mode
  exits and the three rectangles are PATCH-ed server-side in one
  request.
- **Bare left-click** (mouse-down + mouse-up at the same point,
  pickbox tolerance) → skips the current view, leaving its stored
  rectangle untouched, and advances.
- **Esc** → cancels the entire session. Any provisional rectangles
  drawn this session are discarded; the three stored rectangles
  revert to their pre-session state; mark mode exits without a
  server PATCH.

This satisfies the "any combination" requirement: an engineer who
only wants to set `top_view` and `side_view` drags top, presses Enter,
bare-clicks (skip bottom), drags side, presses Enter → done.

**Alternatives considered:**
- *Auto-advance on drag release* (the current two-region flow): works
  for "always set all of them" but breaks the "skip middle one" path,
  because the user would have to draw a dummy bottom rect first.
  Rejected.
- *Esc commits-and-exits early*: would conflate "I want to bail" with
  "I'm done now". Confusing; Enter is the explicit commit signal.
  Rejected.

### Decision 5: Old Match JSON files are not migrated

Already-saved `data/match/{file_id}.json` files written before this
change keep `frontside.` / `bottomside.` prefixes verbatim. They are
output artefacts, not state; the engineer regenerates them by editing
side regions and re-running Save Match, at which point the existing
PATCH-side-regions cache-invalidation hook deletes the old file and
the next Save Match writes with the new prefixes.

**Alternatives considered:**
- *One-shot disk migration script*: tidier disk state, but adds a
  startup script with parse/rewrite risk on user-edited files. The
  engineer's natural workflow re-emits these files within hours of
  the rename anyway. Rejected.

### Decision 6: Rename JS state keys and CSS classes in lockstep with backend

`sideRects.frontside` / `sideRects.bottomside` become
`sideRects.top_view` / `sideRects.bottom_view` / `sideRects.side_view`.
`SIDE_STYLES` gains a third entry. The sides-menu data-action strings
(`frontside`, `bottomside`) become `top_view`, `bottom_view`,
`side_view`. CSS class names (`.side-overlay-frontside` etc.) follow
the same rename. The PATCH endpoint body uses the new field names.

Because the JS is the sole client of the server side-regions API,
there is no third-party compatibility surface to preserve. Renaming
in lockstep is safe.

## Risks / Trade-offs

- **Risk: SQLite version too old** → `ALTER TABLE ... RENAME COLUMN`
  fails on SQLite < 3.25. Mitigation: detect at migration time and
  raise a clear error pointing at the version floor; the bundled
  SQLite in modern Python interpreters is comfortably above this.

- **Risk: Half-applied rename breaks the viewer** → the rename
  touches DB, Python, JS, HTML, CSS, and tests. A partial deploy
  leaves the JS sending `frontside_rect` to a server expecting
  `top_view_rect`. Mitigation: land all rename pieces in a single
  PR / change unit; the openspec change is the unit of atomicity.

- **Risk: Migration runs on a DB that already has `side_view_rect`** →
  e.g. running twice. Mitigation: guard each `ALTER TABLE` step
  behind the standard `if col_name not in cols` / `if new_name in
  cols` check used for the existing migrations.

- **Trade-off: Hard-coding three regions** vs. generalising to N. We
  pay for adding a fourth region later (more touch points than a
  config tweak) but avoid an abstraction that has no concrete second
  use case today. Per [[feedback_autocad_ux]] the UX is already tuned
  for axis-aligned rectangles; the rest of the cost is mechanical.

- **Trade-off: Bare-click as "skip"** vs. dedicated key. Bare-click
  flows naturally from the AutoCAD-style canvas idiom (a click that
  resolves to no entity is already a "deselect / continue" gesture),
  but requires a click-vs-drag threshold check. We reuse the existing
  pickbox tolerance constant.

## Migration Plan

1. Apply the openspec change atomically (single commit / single PR).
2. On server start, `_migrate()` renames the two columns and adds the
   third; idempotent guards prevent re-running.
3. Existing on-disk `data/match/{file_id}.json` files keep old
   prefixes; engineers regenerate by editing side regions + Save
   Match (no manual action needed).
4. **Rollback**: revert the code, then run
   `ALTER TABLE files RENAME COLUMN top_view_rect TO frontside_rect`,
   `ALTER TABLE files RENAME COLUMN bottom_view_rect TO bottomside_rect`,
   and `ALTER TABLE files DROP COLUMN side_view_rect`. Any
   side-view-only data captured between deploy and rollback is lost;
   top/bottom data round-trips cleanly.

## Open Questions

None remaining. The two carry-over decisions from the proposal —
old-JSON handling (decision 5) and mark-mode key bindings (decision
4) — are resolved above.

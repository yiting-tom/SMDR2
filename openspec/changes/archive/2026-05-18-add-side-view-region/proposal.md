## Why

The existing two-region scheme (`frontside` + `bottomside`) covers a
package DXF's top and bottom copper artwork but leaves no slot for the
**side view** drawing that packaging engineers routinely include on
the same sheet (lateral cross-section / edge profile). Today every
side-view instance falls outside both rectangles and gets emitted with
an unprefixed key, indistinguishable from a stray instance that the
engineer just forgot to tag. Adding a third tagged region closes that
gap and aligns the side-region vocabulary with how engineers actually
talk about the three views (`top_view` / `bottom_view` / `side_view`)
instead of the internal `frontside` / `bottomside` jargon
([[project_smdr2_workflow]]).

## What Changes

- Add a third per-file world-space rectangle `side_view_rect`,
  capturable independently from the other two. Any combination of the
  three rectangles MAY be set; each one stays independently nullable.
- **BREAKING** Rename internal terminology end-to-end:
  - DB columns: `frontside_rect` → `top_view_rect`,
    `bottomside_rect` → `bottom_view_rect`, plus new `side_view_rect`.
    Existing rows migrate via `ALTER TABLE ... RENAME COLUMN`
    (SQLite ≥ 3.25).
  - Match JSON prefixes: `frontside.` → `top_view.`,
    `bottomside.` → `bottom_view.`, new `side_view.`.
  - Python module symbols, FastAPI request model fields, JS state
    keys, CSS overlay class names, and toolbar button labels follow
    the same rename.
- Three-step mark-side-regions flow. The viewer cycles
  `top_view → bottom_view → side_view`; `Enter` commits the current
  rectangle (newly drawn or untouched) and advances; a bare left-click
  (no drag) skips the current view, leaving its stored rectangle as-is;
  `Esc` cancels the entire session, reverting any provisional changes
  and exiting mark mode.
- Match-JSON serialisation gains a third branch with deterministic
  overlap priority `top_view > bottom_view > side_view`. An instance
  whose bbox center sits in `top_view_rect` is emitted as
  `top_view.<class>.<index>` even if it also lies inside another
  rectangle.
- Already-saved `data/match/{file_id}.json` files written with the old
  `frontside.` / `bottomside.` prefixes are NOT migrated; they remain
  on disk verbatim until the engineer re-runs Save Match, which
  invalidates the cache via the existing PATCH-side-regions hook.

## Capabilities

### New Capabilities
(none — this extends existing capabilities)

### Modified Capabilities
- `viewer-ui`: the mark-side-regions mode grows from two rectangles to
  three, with revised hint text, an `Enter`-to-commit + bare-click-to-
  skip cycle, and a third overlay colour.
- `dxf-pipeline`: per-file persistence gains `side_view_rect`; the
  match-JSON key prefixer uses the new `top_view.` / `bottom_view.` /
  `side_view.` strings and the three-way priority order.

## Impact

- **Backend** (`app/files.py`, `app/side_regions.py`, `app/main.py`):
  DB migration that renames two columns and adds a third; rewrite the
  side-prefix helper to take three optional rectangles and emit the
  new prefix strings; broaden `SideRegionsRequest` and the file record
  serializer; preserve the existing "patch invalidates saved match"
  semantics across all three rectangles.
- **Frontend** (`app/static/canvas.js`, `app/templates/viewer.html`):
  rename `sideRects` keys, extend `SIDE_STYLES` to three colours,
  rewrite `enterMarkMode` as a three-step cycle with the new key
  bindings, update the sides-menu HTML/labels, and rename the overlay
  CSS classes ([[feedback_autocad_ux]]).
- **Tests** (`tests/test_side_regions.py`, `tests/test_files.py`,
  `tests/test_api.py`): rename all fixtures and assertions; add
  coverage for three-way overlap priority, independent NULL
  combinations, the migration path, and the new bare-click skip /
  Enter-commit flow.
- **Storage**: no new files on disk; DB schema changes only.
  `data/prematch/{file_id}.json` stays flat (it never carried side
  prefixes). Older `data/match/{file_id}.json` files keep the old
  prefix strings until the engineer regenerates them.
- **No change** to matcher internals ([[pattern-matching]]) — the
  side label is applied at serialisation time.
- **Rule check** ([[design-rule-checking]]) sees three prefix strings
  instead of two; existing rules that key on `<class>.<index>`
  continue to work because the prefix is additive on the left.

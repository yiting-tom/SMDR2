## Context

The viewer class toolbar (`renderClassToolbar` in `app/static/canvas.js`) is a
flat row of one button per class in the library, in rank order. The codebase
already has a precedent for a class-keyed registry that lives in Python and is
**mirrored** into `canvas.js` between sentinel comments with a Python↔JS drift
test: `CLASS_VIEW_CONSTRAINTS` (`app/library.py` ↔ `canvas.js`, guarded by
`tests/test_canvas_constants.py::test_class_view_constraints_js_mirror_matches_python`).
This change follows that exact pattern for a new category registry.

## Goals / Non-Goals

**Goals:**
- Present the class buttons as two floating category panels overlaid on the
  canvas (left: Structure / Balls / SMD / Other; right: Marks) instead of one
  long horizontally-scrolling toolbar row, so the categories are scannable and
  the top bar reclaims its height.
- Single source of truth for the categorisation; no Python/JS drift.

**Non-Goals:**
- Category **filtering** (chips) — a possible follow-up, not this change.
- Reordering `DEFAULT_CLASSES`, or changing class ranks, match strategy, scope,
  or view constraints.
- Any API shape change.

## Decisions

**D1 — Registry in Python, mirrored in JS, drift-tested.**
`library.CLASS_CATEGORY: dict[str, str]` (class display ID → category key) and
`library.CLASS_CATEGORY_ORDER: list[tuple[str, str]]` (ordered `(key, label)`)
are the source of truth. Both are mirrored into `canvas.js` between
`CLASS_CATEGORY_BEGIN/_END` (and `CLASS_CATEGORY_ORDER_BEGIN/_END`) sentinels,
and a new drift test asserts the JS literals equal the Python dicts. *Alternative
(expose `category` via the `/api/classes` summary)* rejected: it would change the
API contract for a purely cosmetic toolbar concern, and the mirror+drift-test
pattern is already established for `CLASS_VIEW_CONSTRAINTS`.

**D2 — Group at render time; do NOT reorder `DEFAULT_CLASSES`.**
`renderClassToolbar` iterates `CLASS_CATEGORY_ORDER`, and for each category
renders a header + the library's classes whose category matches (in their
existing rank order) into the appropriate panel (`marks` → right, every other
category and "Other" → left). `DEFAULT_CLASSES` order and class ranks are
untouched, so the grouping is identical for a freshly-seeded library and an
existing one (whose ranks already differ). *Alternative (reorder
`DEFAULT_CLASSES` so groups are contiguous)* rejected: it changes seed ranks, so
existing libraries (ranks already persisted) and new libraries would
group-order differently.

**D6 — Floating panels overlaid on the canvas, reusing `.floating-panel`.**
The two panels are `<aside>` elements inside `<main>` (which is
`position: relative`), reusing the existing `.floating-panel` chrome (the same
pattern as the Layers/visibility panel) — left at `left: 0.6rem`, right at
`right: 0.6rem`. Class buttons leave the top `nav#class-toolbar` entirely; that row now hosts
the Chain / Sides mode toggles plus the action buttons (Library, Scan All, Save
Match, Measure, Layers, Rules) relocated down from the header into the same row
(low-risk: all are moved by DOM only, wired by id, and given `.tool-btn` — whose
style is identical to the old `header button` rule). The class-button CSS is
re-scoped from `nav#class-toolbar .class-*` to class-based selectors so it
applies inside the panels. Each panel is collapsible
(▾) and hides when it has no groups. *Known interaction:* the right Marks panel
shares the top-right corner with the Layers panel and the rule-check sidebar
(both toggled), so they can overlap when open — the collapse control mitigates
it; a position-aware offset is a possible follow-up.

**D3 — Four categories, fixed order, every default class categorised.**
Order: Structure → Balls & Bumps → SMD Pads → Fiducials & Marks (the operator's
chosen grouping; Fiducials and Marks merged). An import-time assertion requires
every `DEFAULT_CLASSES` member to have a `CLASS_CATEGORY` entry and every
category key used to appear in `CLASS_CATEGORY_ORDER` — so a newly-added class
(like the recent DAM) can't silently fall through.

**D4 — Hotkeys are untouched (they never depended on DOM order).**
The hotkey handler maps a key to a class by index: `HOTKEYS[idx] → classes[idx]`
(`canvas.js`), and the buttons carry **no** on-button hotkey label. So grouping
is a pure DOM reorder — the key→class binding is unchanged, and the existing
"Per-class hotkeys and scan workflow" requirement and its scenario stay valid
verbatim. *Alternative (re-assign hotkeys in grouped render order)* rejected:
it would needlessly remap existing bindings for zero benefit (no labels to keep
in sequence).

**D5 — Uncategorised classes fall into a trailing "Other" group.**
A library may hold a class with no `CLASS_CATEGORY` entry (a user-defined class,
or a future default not yet categorised). Such classes render under a trailing
"Other" header so a button is never dropped. An empty category (no classes in
the current library) renders no header.

## Risks / Trade-offs

- **Python/JS drift** → guarded by the new mirror drift test, exactly as
  `CLASS_VIEW_CONSTRAINTS` is.
- **A new default class added without a category** → the import-time invariant
  assertion fails loudly (and `tests/test_library.py` pins it), so it can't ship
  grey/ungrouped silently.
- **Hotkey remap vs. muscle memory** → grouping changes which key maps to which
  class; acceptable since the operator is explicitly reorganising the row, and
  adding DAM already shifted the flat assignment.

## Migration Plan

Pure additive UI + data; no data migration, no API change. Effective on next
viewer load. Rollback = revert.

## Open Questions

None. (Grouping = Structure / Balls & Bumps / SMD Pads / Fiducials & Marks,
confirmed with the operator.)

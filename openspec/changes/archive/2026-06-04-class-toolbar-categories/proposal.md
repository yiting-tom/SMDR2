## Why

The class toolbar is a flat row of ~18 buttons with no visual structure, so an
operator has to scan the whole row to find the object they want. Grouping the
objects into a few functional categories (structure, balls/bumps, SMD pads,
fiducials & marks) makes the toolbar scannable and mirrors how a packaging
engineer already thinks about these features.

## What Changes

- Introduce a per-class **category** registry: `library.CLASS_CATEGORY`
  (class display ID → category key) plus an ordered
  `library.CLASS_CATEGORY_ORDER` (category key → display label, in render
  order). Every default class is assigned a category. Four categories:
  - **Structure** — Substrate, DieArea, DAM, Lid, LidOuter, LidInner, Protrusion
  - **Balls & Bumps** — C4Ball, BGABall
  - **SMD Pads** — SMD-2T, SMD-3T, SMD-8T, SMD-14T
  - **Fiducials & Marks** — FiducialCircle, FiducialCross, FiducialSquare,
    Pin-1, 2DBarcode
- Mirror both registries in `app/static/canvas.js` between sentinel comments
  (the established pattern for `CLASS_VIEW_CONSTRAINTS`), guarded by a
  Python↔JS drift test.
- The viewer's class buttons move off the long top toolbar row into **two
  floating panels overlaid on the canvas** (reusing the existing
  `.floating-panel` chrome): a left **Objects** panel (Structure, Balls &
  Bumps, SMD Pads, plus any uncategorised **Other** classes) and a right
  **Marks** panel (the `marks` category). Each panel groups its classes under
  category headers in `CLASS_CATEGORY_ORDER` order (rank order within a
  category); a panel with no groups is hidden, and each panel is collapsible.
  The top `nav#class-toolbar` keeps the Chain / Views mode toggles and also
  hosts the action buttons (Library, Scan All, Save Match, Measure, Layers,
  Rules) relocated down from the header into that same row.
- Relabel the viewer's **`Sides` mode button to `Views`** (it marks the top /
  bottom / side view regions, so "Views" reads truer); the button id and
  behaviour are unchanged.
- Hotkeys (`1…0`, `q…p`) are **unaffected**: a key maps to a class by the
  class's index in the library list (`classes[idx]`), not by DOM position, and
  there are no on-button hotkey labels — so visual grouping leaves every
  existing hotkey binding unchanged.

Left/right split: `marks` → right panel; every other category → left panel
(operator's choice). Out of scope: category **filtering** (a follow-up if
wanted) and any change to matching, scope, or view constraints.
`DEFAULT_CLASSES` order is NOT changed — grouping happens at render time.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `template-library`: ADD a "Per-class category registry" requirement
  (`CLASS_CATEGORY` + `CLASS_CATEGORY_ORDER`, every default class categorised).
- `viewer-ui`: ADD a "Floating category class panels" requirement (class
  buttons render in a left + right floating panel overlaid on the canvas,
  grouped by category; hotkeys unchanged).

## Impact

- **Code**: `app/library.py` (two new registries + an "every class is
  categorised" invariant assertion); `app/static/canvas.js` (mirror the two
  registries between sentinels; `renderClassToolbar` renders the grouped
  buttons into the two floating panels; panel collapse wiring);
  `app/templates/viewer.html` (the two `<aside>` floating panels in `<main>`);
  `app/static/style.css` (re-scope the class-button rules from
  `nav#class-toolbar .class-*` to class-based so they apply in the panels, plus
  `.class-panel` layout + header + collapse styles).
- **Tests**: `tests/test_canvas_constants.py` (new Python↔JS drift test for the
  category mirror); `tests/test_library.py` (assert every `DEFAULT_CLASSES`
  member has a `CLASS_CATEGORY` entry and the category keys match
  `CLASS_CATEGORY_ORDER`). Frontend rendering verified manually with Playwright.
- **Downstream**: none — purely a viewer-UI organisation plus a data registry;
  no API shape change, no match/scope/constraint behaviour change.

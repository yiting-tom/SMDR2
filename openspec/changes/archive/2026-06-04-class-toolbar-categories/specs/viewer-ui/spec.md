## ADDED Requirements

### Requirement: Floating category class panels

The viewer SHALL present the library's class buttons in two floating panels overlaid on the canvas instead of a single toolbar row: a left panel for every category except `marks`, and a right panel for the `marks` category. Each panel SHALL group its classes under category headers in `CLASS_CATEGORY_ORDER` order, with classes in their existing rank order within a category. A class with no category SHALL render under a trailing "Other" group in the left panel so its button is never dropped. A panel with no groups (e.g. a library with no marks classes) SHALL be hidden, and each panel SHALL be collapsible to just its header.

The panels SHALL reuse the existing `.floating-panel` overlay chrome. The class-button styling (per-class colour, found / absent / active / staged states, count badge, strategy tag) SHALL apply inside the panels — the rules are class-scoped, not `nav#class-toolbar`-scoped. With the class buttons gone, the top `nav#class-toolbar` SHALL host the Chain / Sides mode toggles together with the action buttons (Library, Scan All, Save Match, Measure, Layers, Rules) relocated from the header into that same row; those action buttons SHALL keep their ids (and thus their wiring) and adopt the `.tool-btn` style.

The panels SHALL preserve existing per-button behaviour: the collapse of `COLLAPSED_TOOLBAR_CLASSES` (its More / Less toggle lives inside the owning group), active / add-mode state, the `+ → ✓` staging indicator, and the per-class count badge. The grouping SHALL NOT change the hotkey mapping — a hotkey maps to a class by the class's index in the library list (`HOTKEYS[idx] → classes[idx]`), independent of which panel a button lands in. `CLASS_CATEGORY` and `CLASS_CATEGORY_ORDER` SHALL be mirrored into `app/static/canvas.js` between sentinel comments and kept in sync with `app/library.py`, enforced by a Python↔JS drift test.

#### Scenario: Classes split into a left Objects panel and a right Marks panel
- **WHEN** the viewer loads a library seeded with the default classes
- **THEN** the left panel shows the Structure, Balls & Bumps, and SMD Pads groups (plus an "Other" group only if uncategorised classes exist)
- **AND** the right panel shows the Fiducials & Marks group (Pin-1, the three Fiducial classes, 2DBarcode)
- **AND** DAM appears under the left panel's Structure group

#### Scenario: SMD variants collapse within their group
- **WHEN** the toolbar is not expanded
- **THEN** the SMD Pads group shows SMD-2T plus a "More" toggle
- **AND** clicking "More" reveals SMD-3T / SMD-8T / SMD-14T under the same SMD Pads group

#### Scenario: Uncategorised class falls under the left "Other" group
- **WHEN** the library contains a class absent from `CLASS_CATEGORY`
- **THEN** its button renders under a trailing "Other" group in the left panel
- **AND** the button is not dropped

#### Scenario: Empty panel is hidden
- **WHEN** the current library has no class in a panel's categories
- **THEN** that panel is hidden

#### Scenario: Action buttons sit in the toolbar row, not the header
- **WHEN** the viewer loads
- **THEN** `nav#class-toolbar` contains the Chain and Sides toggles plus the Library / Scan All / Save Match / Measure / Layers / Rules buttons
- **AND** the header no longer contains those action buttons
- **AND** each relocated button keeps its id (and therefore its wiring)

#### Scenario: Hotkey mapping is unchanged by the panels
- **WHEN** the panels render
- **THEN** pressing a class's hotkey still enters add-mode for that class (key→class by `classes` index, independent of which panel the button is in)

#### Scenario: Python and JS category registries stay in sync
- **WHEN** the Python↔JS drift test runs
- **THEN** the `CLASS_CATEGORY` and `CLASS_CATEGORY_ORDER` literals mirrored in `canvas.js` equal the Python registries in `library.py`

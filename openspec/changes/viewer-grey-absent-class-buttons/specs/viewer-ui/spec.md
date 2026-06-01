## ADDED Requirements

### Requirement: Class toolbar greys classes absent from the current image

A class-toolbar button SHALL show its full per-class colour only when its class has one or more matches in the open drawing; every other button SHALL be visually dimmed (greyed). Dimmed is the default state — it applies both before any match data exists (no scan has run yet) and to any class with zero matches afterwards — so the operator sees at a glance which objects are already extracted vs still absent. Match data is the per-image counts in `scanAllSummary.byClass`, populated by the auto-run prematch on viewer load and refreshed by an explicit Scan All.

A dimmed button SHALL remain fully interactive (clicking it still enters
add-mode for that class). The class currently in add-mode SHALL keep its active
styling and not be dimmed.

#### Scenario: Unmatched class is greyed

- **WHEN** the toolbar renders and class `C` has zero matches in the open drawing
- **THEN** the `C` toolbar button SHALL be rendered dimmed (greyed)
- **AND** a class with one or more matches SHALL keep its full per-class colour

#### Scenario: Greyed by default before any scan

- **WHEN** no prematch or scan-all has populated match data for the drawing
- **THEN** every class-toolbar button SHALL be rendered dimmed (greyed), since no class is confirmed present yet

#### Scenario: Greyed button still enters add-mode

- **WHEN** the operator clicks a greyed (absent) class button
- **THEN** the viewer SHALL enter add-mode for that class
- **AND** the button SHALL show its active styling rather than the dimmed styling

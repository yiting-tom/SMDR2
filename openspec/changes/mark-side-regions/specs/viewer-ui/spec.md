## ADDED Requirements

### Requirement: Mark side regions mode

The viewer SHALL provide a "Mark sides" toolbar button and an `R`
hotkey that toggles a mark-side-regions mode. In this mode, the user
SHALL define exactly two axis-aligned, world-space rectangles by
left-press-drag-release: the first rectangle SHALL be saved as the
file's `frontside_rect`, the second as `bottomside_rect`. The status
hint SHALL read `MARK frontside · drag a rectangle` while waiting for
the first rectangle, then `MARK bottomside · drag a rectangle` while
waiting for the second, then exit mark mode automatically once both
are captured.

Mark mode SHALL be mutually exclusive with add-mode, measure-mode,
and box-select; entering mark mode while another mode is active is a
no-op (the `R` hotkey is suppressed). While mark mode is active the
canvas SHALL NOT perform selection, pickbox, or pan-drag on
left-button events.

#### Scenario: Toolbar button enters mark mode
- **WHEN** the user clicks the "Mark sides" button or presses `R`
- **THEN** the button's active state is set
- **AND** the status hint reads `MARK frontside · drag a rectangle`

#### Scenario: Two consecutive drags capture both rectangles
- **WHEN** in mark mode, the user left-drags a rectangle, releases, then drags a second rectangle
- **THEN** the first rectangle is persisted as `frontside_rect` (normalised so x0<=x1, y0<=y1)
- **AND** the second rectangle is persisted as `bottomside_rect`
- **AND** the viewer exits mark mode automatically

#### Scenario: Hotkey suppressed inside add or measure mode
- **WHEN** the user is in add-mode or measure-mode and presses `R`
- **THEN** mark mode does not activate
- **AND** the current mode is unchanged

### Requirement: Persistent side-region overlay

The viewer SHALL render `frontside_rect` and `bottomside_rect`, when
present, as thin tinted outlines on the canvas at all times (not just
in mark mode). Frontside SHALL use a distinct colour from bottomside,
and both SHALL be drawn beneath selection, near-miss, scan-all match
overlays, and the active box-drag rectangle so they never visually
override interactive feedback.

#### Scenario: Overlay visible after saving regions
- **WHEN** the user has saved a frontside and bottomside rectangle and exits mark mode
- **THEN** both rectangles are still visible on the canvas
- **AND** they are visually distinguishable by colour

#### Scenario: Overlay does not obscure selection
- **WHEN** the user selects an entity whose geometry lies inside the frontside rectangle
- **THEN** the selection highlight is rendered on top of the rectangle outline

### Requirement: Redraw and clear side regions

The "Mark sides" toolbar button SHALL expose options to redraw a
single side or clear both rectangles. Redrawing one side SHALL keep
the other side's rectangle untouched. Clearing both SHALL remove the
overlay and unset both columns server-side.

#### Scenario: Redraw frontside only
- **WHEN** the user picks "Redraw frontside only" and drags a new rectangle
- **THEN** `frontside_rect` is overwritten with the new rectangle
- **AND** `bottomside_rect` is unchanged

#### Scenario: Clear both
- **WHEN** the user picks "Clear both"
- **THEN** both `frontside_rect` and `bottomside_rect` are cleared server-side
- **AND** the overlay disappears

### Requirement: Esc cancels in-progress mark mode

Pressing `Esc` while mark mode is active SHALL participate in the
existing Esc cascade. If the user is mid-drag on a side rectangle,
the drag SHALL be cancelled but mark mode stays active. If no drag is
in progress, mark mode SHALL exit without modifying any saved
rectangle.

#### Scenario: Esc during mid-drag cancels the drag
- **WHEN** the user is dragging the first side rectangle and presses `Esc`
- **THEN** the in-progress rectangle is discarded
- **AND** mark mode remains active waiting for the same side's rectangle

#### Scenario: Esc with no active drag exits mark mode
- **WHEN** mark mode is active, no drag is in progress, and the user presses `Esc`
- **THEN** mark mode exits
- **AND** no saved rectangle is changed

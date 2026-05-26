## MODIFIED Requirements

### Requirement: Mark side regions mode

The viewer SHALL provide a "Mark sides" toolbar button and an `R`
hotkey that toggles a mark-side-regions mode. In this mode the viewer
SHALL cycle through three view slots in order — `top_view`,
`bottom_view`, `side_view` — and SHALL drive each slot through one of
three terminal user actions:

- **Draw + Enter** (left-press-drag-release defines a provisional
  rectangle; `Enter` commits it to the current slot and advances)
- **Bare-click skip** (a left-press-release at the same point, within
  the pickbox tolerance, advances without changing the current slot's
  stored rectangle)
- **Enter-without-draw** (commits the previously-stored rectangle for
  the current slot unchanged and advances)

After the third slot's Enter or bare-click, the viewer SHALL exit
mark mode and PATCH the three rectangles to the server in a single
request. The status hint SHALL read
`MARK <view> · drag a rectangle, Enter to keep, click to skip`
substituting `top_view`, `bottom_view`, or `side_view` for `<view>`
depending on the current slot.

Mark mode SHALL be mutually exclusive with add-mode, measure-mode,
and box-select; entering mark mode while another mode is active is a
no-op (the `R` hotkey is suppressed). While mark mode is active the
canvas SHALL NOT perform selection, pickbox, or pan-drag on
left-button events.

#### Scenario: Toolbar button enters mark mode
- **WHEN** the user clicks the "Mark sides" button or presses `R`
- **THEN** the button's active state is set
- **AND** the status hint reads `MARK top_view · drag a rectangle, Enter to keep, click to skip`

#### Scenario: Three consecutive drags capture all rectangles
- **WHEN** in mark mode, the user left-drags a rectangle and presses Enter, then drags and presses Enter, then drags and presses Enter
- **THEN** the three rectangles are persisted in slot order as `top_view_rect`, `bottom_view_rect`, `side_view_rect` (each normalised so x0<=x1, y0<=y1)
- **AND** the viewer exits mark mode automatically

#### Scenario: Bare-click skips the current view
- **WHEN** in mark mode at the `bottom_view` slot, the user does a bare left-click without dragging
- **THEN** the stored `bottom_view_rect` is left unchanged
- **AND** the slot advances to `side_view`

#### Scenario: Enter without drawing keeps the previously-stored rectangle
- **WHEN** in mark mode at the `top_view` slot, the user presses `Enter` without drawing
- **THEN** the stored `top_view_rect` is unchanged
- **AND** the slot advances to `bottom_view`

#### Scenario: Hotkey suppressed inside add or measure mode
- **WHEN** the user is in add-mode or measure-mode and presses `R`
- **THEN** mark mode does not activate
- **AND** the current mode is unchanged

### Requirement: Persistent side-region overlay

The viewer SHALL render `top_view_rect`, `bottom_view_rect`, and
`side_view_rect`, when present, as thin tinted outlines on the canvas
at all times (not just in mark mode). Each of the three views SHALL
use a distinct colour, and all three SHALL be drawn beneath
selection, near-miss, scan-all match overlays, and the active
box-drag rectangle so they never visually override interactive
feedback.

#### Scenario: Overlay visible after saving regions
- **WHEN** the user has saved at least one of the three rectangles and exits mark mode
- **THEN** every stored rectangle is still visible on the canvas
- **AND** each visible rectangle is colour-distinguishable from the others

#### Scenario: Overlay does not obscure selection
- **WHEN** the user selects an entity whose geometry lies inside the `top_view` rectangle
- **THEN** the selection highlight is rendered on top of the rectangle outline

#### Scenario: Only-side-view file shows one overlay
- **WHEN** the file has only `side_view_rect` set (the other two are null)
- **THEN** only the `side_view` outline is rendered
- **AND** no placeholder is rendered for the absent `top_view` and `bottom_view`

### Requirement: Redraw and clear side regions

The "Mark sides" toolbar button SHALL expose options to redraw a
single view's rectangle or clear all rectangles. Redrawing one view
SHALL keep the other two views' rectangles untouched. Clearing all
SHALL remove the overlay and unset all three columns server-side.

In addition, every committed view's persistent label SHALL render an
"×" delete glyph inside the label background. Left-clicking the ×
SHALL clear that specific view's rectangle (set the corresponding
`<view>_rect` to NULL server-side) and remove its overlay, without
affecting the other two views. The × hitbox SHALL take precedence
over every other left-click gesture (mark drag, measure pick, and
selection) so the user can always remove a view's rectangle from the
canvas chrome regardless of the current mode. When the × is clicked
during an active mark-mode session, the in-flight snapshot SHALL be
updated to reflect the deletion so a subsequent `Esc` cannot restore
the cleared rectangle.

#### Scenario: Redraw side_view only
- **WHEN** the user picks "Redraw side_view only" and drags a new rectangle
- **THEN** `side_view_rect` is overwritten with the new rectangle
- **AND** `top_view_rect` and `bottom_view_rect` are unchanged

#### Scenario: Clear all
- **WHEN** the user picks "Clear all"
- **THEN** all three of `top_view_rect`, `bottom_view_rect`, and `side_view_rect` are cleared server-side
- **AND** the overlay disappears

#### Scenario: × on label clears that view only
- **WHEN** all three rectangles are set and the user left-clicks the × on the `bottom_view` label
- **THEN** `bottom_view_rect` is cleared server-side
- **AND** the `bottom_view` overlay disappears
- **AND** `top_view_rect` and `side_view_rect` remain unchanged

#### Scenario: × during mark mode survives Esc
- **WHEN** the user enters mark mode with all three rectangles already set, left-clicks the × on the `top_view` label, then presses `Esc`
- **THEN** `top_view_rect` remains cleared (the Esc snapshot revert SHALL NOT bring it back)
- **AND** `bottom_view_rect` and `side_view_rect` are unchanged

### Requirement: Esc cancels in-progress mark mode

Pressing `Esc` while mark mode is active SHALL participate in the
existing Esc cascade. If the user is mid-drag on a slot's rectangle,
the drag SHALL be cancelled but mark mode stays active at the same
slot. If no drag is in progress, mark mode SHALL exit and SHALL
discard every provisional rectangle drawn during the current session:
the three stored rectangles revert to their pre-session values and
no server PATCH is sent.

#### Scenario: Esc during mid-drag cancels the drag
- **WHEN** the user is dragging the `top_view` rectangle and presses `Esc`
- **THEN** the in-progress rectangle is discarded
- **AND** mark mode remains active waiting for the same slot's rectangle

#### Scenario: Esc with no active drag cancels the session
- **WHEN** mark mode is active at the `side_view` slot, no drag is in progress, the user has already drawn and Entered a new `top_view_rect` this session, and the user presses `Esc`
- **THEN** mark mode exits
- **AND** the stored `top_view_rect` reverts to its pre-session value
- **AND** no PATCH is sent to the server

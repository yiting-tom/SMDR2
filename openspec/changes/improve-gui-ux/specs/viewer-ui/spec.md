## ADDED Requirements

### Requirement: Viewer provides zoom-to-extents

The viewer SHALL provide a zoom-to-extents action that re-frames the whole
drawing to the bounding box it was first fitted to on load (the value passed
to `fitToBbox` at load time, retained as `loadedBbox`). The action SHALL be
reachable three ways: the `Home` key, a middle-mouse double-click on the
canvas (AutoCAD's Zoom Extents gesture), and a toolbar button ("Fit"). The
action SHALL NOT depend on a page reload and SHALL be a no-op (not an error)
when no drawing bbox is available.

#### Scenario: Home key re-frames the whole drawing

- **WHEN** the user has panned/zoomed away and presses `Home` while the
  canvas has focus and no text input or modal is active
- **THEN** the view re-centres and re-zooms so the full drawing bbox is
  framed, identical to the initial on-load fit

#### Scenario: Middle double-click zooms to extents

- **WHEN** the user double-clicks the middle mouse button on the canvas
- **THEN** the view re-frames to the full drawing bbox
- **AND** a single middle-click still begins a pan (the double-click does not
  break panning)

#### Scenario: Fit button is always available

- **WHEN** the user clicks the toolbar "Fit" button
- **THEN** the view re-frames to the full drawing bbox regardless of the
  current pan/zoom

### Requirement: Pan gives cursor feedback and recovers from lost drags

While a middle-drag pan is active the canvas SHALL show a `grabbing` cursor
(via the already-toggled `.panning` class). If the window loses focus while
any drag (pan, box-select, or mark) is in progress, the viewer SHALL clear the
in-flight drag state and the `.panning` class so the canvas never remains
stuck in a drag after the mouse is released off-window or the user alt-tabs
away.

#### Scenario: Panning shows a grabbing cursor

- **WHEN** the user presses the middle mouse button and drags
- **THEN** the canvas cursor is `grabbing` for the duration of the pan
- **AND** it returns to the default cursor when the button is released

#### Scenario: Drag state recovers after focus loss

- **WHEN** a pan or box-select drag is in progress and the window loses focus
  (alt-tab, or the mouse is released outside the browser window)
- **THEN** the viewer clears the drag and removes the `.panning` class
- **AND** the next interaction on the canvas behaves normally (no ghost pan,
  no stuck selection rectangle)

### Requirement: Sub-pixel circles remain visible as minimum-size dots

A sub-threshold circle SHALL be painted as a batched dot at a device-pixel
size of at least `max(1, round(devicePixelRatio))`, so dense small-circle
fields (e.g. BGA balls) stay perceptible at low zoom on HiDPI displays. The
threshold that decides whether a circle collapses to a dot is unchanged — only
the painted size of an already-collapsed dot changes.

#### Scenario: BGA dots stay visible on a HiDPI display

- **WHEN** a drawing with many sub-threshold circles is viewed at low zoom on
  a display with `devicePixelRatio` ≥ 2
- **THEN** each collapsed circle is painted at ≥ 2 device pixels and the field
  is visibly present (not effectively blank)

### Requirement: Viewer exposes an in-app keyboard-shortcut reference

The viewer SHALL provide an in-app shortcut reference listing its keyboard and
mouse shortcuts, toggled by the `?` key (and dismissable by `?`, `Esc`, or a
backdrop click). While a box-select drag is active the status bar SHALL show a
transient `WINDOW` or `CROSSING` label reflecting the current selection mode,
cleared when the drag ends.

#### Scenario: `?` opens the shortcut reference

- **WHEN** the user presses `?` with no text input focused
- **THEN** an overlay listing the viewer shortcuts is shown
- **AND** pressing `?` again, `Esc`, or clicking the backdrop hides it

#### Scenario: Box-drag shows its selection mode

- **WHEN** the user is mid box-select dragging left→right
- **THEN** the status bar shows a `WINDOW` label
- **AND** dragging right→left instead shows `CROSSING`
- **AND** the label clears when the drag completes or is cancelled

### Requirement: Canvas status readouts use tabular figures

The canvas status-bar readouts SHALL render numbers with tabular (fixed-width)
figures — covering status, mode-hint, handle info, and cursor coordinates — so
the readouts do not shift horizontally as digit values change during cursor
motion.

#### Scenario: Coordinate readout does not jitter

- **WHEN** the cursor moves and the coordinate readout updates across values
  of differing digit widths
- **THEN** the readout's character cells stay aligned (no horizontal jitter of
  surrounding status elements)

### Requirement: Dashboard uses in-app dialogs, not native browser dialogs

Dashboard flows SHALL use the application's own modal / inline UI rather than
the browser's native `prompt()`, `confirm()`, or `alert()` for text input,
confirmation, or error notices. Specifically: creating a new
version (label entry), removing a file from a role (confirmation), and the
signed-off 409 conflict (error notice). The modal dialogs SHALL support
`Enter` to confirm and `Esc` / backdrop to cancel, and SHALL move focus to the
primary field or button on open.

#### Scenario: New-version label uses an in-app modal

- **WHEN** the user starts creating a new version
- **THEN** an in-app modal prompts for the label with focus in the field
- **AND** `Enter` confirms with the entered label and `Esc` / backdrop cancels
  with no version created
- **AND** no native `prompt()` dialog appears

#### Scenario: Removing a file uses an in-app confirmation

- **WHEN** the user removes a file from a role
- **THEN** an in-app confirmation modal (styled as a destructive action) is
  shown, and the removal proceeds only on explicit confirm

#### Scenario: Signed-off conflict shows an inline notice

- **WHEN** a write is rejected because the version is signed off (HTTP 409)
- **THEN** the conflict is surfaced as an inline, non-blocking notice on the
  page (who signed off and when), not a native `alert()`

### Requirement: Secondary UI text meets a contrast floor

Secondary / helper text colours used on the dark surfaces SHALL meet an
approximate 4.5:1 contrast ratio against their background. This applies to the
shared `--text-3` token and the helper colours previously sitting near 3:1.

#### Scenario: Helper text is legible on dark surfaces

- **WHEN** secondary text (e.g. `--text-3`, rule counts, rescaled / unit
  badges, link buttons) is rendered on `--surface` or `--bg-page`
- **THEN** its contrast ratio against that background is approximately 4.5:1
  or greater

### Requirement: Viewer panels and class toolbar adapt to narrow viewports

On narrow viewports the viewer's rule sidebar / rule panel SHALL reduce width
rather than overflow, and the class toolbar SHALL wrap onto multiple rows
rather than introduce a horizontal scroll that pushes controls off-screen.

#### Scenario: Class toolbar wraps on a narrow window

- **WHEN** the viewer is shown in a window narrower than the breakpoint
- **THEN** the class toolbar wraps its buttons onto multiple rows and remains
  fully usable
- **AND** the rule sidebar / panel fit within the viewport without clipping
  controls

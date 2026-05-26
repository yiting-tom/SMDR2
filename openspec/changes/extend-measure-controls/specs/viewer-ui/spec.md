## ADDED Requirements

### Requirement: Multi-chain controls for the measure-distance tool

The viewer's measure-distance tool SHALL support multiple committed
measurement chains on the canvas simultaneously and SHALL provide
per-chain mid-stream undo, commit, and cancel controls. These controls
augment the existing AutoCAD-style measure-distance tool requirement.

State model:

- A measurement consists of an ordered list of world-space picks. Two
  adjacent picks form one segment. Each chain SHALL have at least two
  picks to be drawn as a measurement.
- The tool SHALL maintain two collections of chains:
  - The **active chain** — the chain currently being extended by clicks.
    Its last pick anchors the rubber-band to the cursor. At any time
    there is at most one active chain.
  - The **committed chains** — zero or more chains that have been
    finalized. They SHALL be drawn on the canvas with solid segments,
    endpoint dots on every pick, and a midpoint label on every segment,
    but they SHALL NOT participate in the rubber-band or in OSNAP
    resolution.

Right-click in measure mode (undo last pick):

- A right mouse-button click (browser `contextmenu` event) inside the
  canvas while measure mode is active SHALL suppress the default
  browser context menu.
- If the active chain has one or more picks, the right-click SHALL
  remove its most-recently-added pick. The active chain remains the
  active chain.
- If the active chain has zero picks (including the case where it was
  just reduced from one pick to zero by this right-click), the
  right-click SHALL be a visual no-op besides suppressing the context
  menu; committed chains SHALL NOT be touched.
- After a successful pop, the rubber-band and snap-marker overlay SHALL
  re-resolve from the most recent cached cursor position without
  requiring the user to move the mouse, so the visual state matches the
  new anchor immediately.

Enter in measure mode (commit active chain):

- Pressing `Enter` while measure mode is active SHALL:
  - If the active chain has two or more picks, append it to the
    committed chains and start a fresh empty active chain.
  - If the active chain has fewer than two picks, reset it to empty
    without appending anything (nothing is committed and nothing
    persists from a one-pick chain).
- In either case, the live `Δx` / `Δy` HTML readout SHALL be hidden
  and the status hint SHALL be re-rendered to reflect the new state.
- Enter SHALL be consumed (`e.preventDefault()`) by the measure-mode
  handler so it does not also trigger the add-mode commit path.

Esc in measure mode (clear every measurement):

- Pressing `Esc` while measure mode is active and any active or
  committed chain exists SHALL clear *both* the active chain and *all*
  committed chains in one keystroke. Measure mode itself SHALL remain
  active.
- If neither the active chain nor any committed chain exists, the
  `Esc` cascade SHALL fall through to the existing next step (scan-all
  → add-mode → selection), unchanged.
- The Esc-clears-everything behavior SHALL be inserted at the same
  position in the cascade currently occupied by Esc-clears-active-chain
  — i.e. after the active-box-drag step and before scan-all.

Per-chain cancel affordance:

- Each committed chain SHALL render a clickable "✕" cancel button on
  the canvas overlay, drawn immediately adjacent to the first segment's
  midpoint label, with yellow-on-black styling matching the label.
- The ✕ button's hitbox SHALL be in screen-space CSS pixels and SHALL
  be recomputed on every render so that pan, zoom, and canvas resize
  keep the click target aligned with the visible glyph.
- A left-click whose CSS-pixel position lies inside any committed
  chain's ✕ hitbox SHALL remove only that chain. The active chain and
  all other committed chains SHALL remain unchanged. The click SHALL
  NOT modify the entity selection.
- The hitbox check SHALL run before the measure-mode pick-append logic
  so a ✕ click never accidentally adds a pick to the active chain.

Tool toggle SHALL clear committed chains:

- Toggling the tool off via the `D` hotkey or the toolbar button SHALL
  clear every committed chain in addition to the existing reset of the
  active chain, the snap hint, and the cached cursor.

Status hint reflects the chain counts:

- When neither the active chain nor any committed chain exists:
  `MEASURE · pick first point`.
- When the active chain is empty but at least one committed chain
  exists: `MEASURE · pick first point · N chain[s] saved (Esc to clear all)`.
- When the active chain has one or more picks and no committed chain
  exists: `MEASURE · pick next point · N pt[s] (Shift = ortho, Esc to clear)`.
- When both the active chain has picks and at least one committed
  chain exists: `MEASURE · pick next point · N pt[s] (Shift = ortho, Esc to clear all)`.

The `pt[s]` / `chain[s]` tokens SHALL be singular when the count is
exactly one and plural otherwise.

#### Scenario: Right-click pops the last pick mid-chain

- **WHEN** measure mode is active, the active chain has 3 picks, and
  the user right-clicks anywhere on the canvas
- **THEN** the browser context menu does not appear
- **AND** the active chain has 2 picks
- **AND** the rubber-band redraws from the new last pick to the cached
  cursor position without requiring a mouse move

#### Scenario: Right-click on a one-pick chain returns to "pick first point"

- **WHEN** measure mode is active, the active chain has exactly 1
  pick, and the user right-clicks on the canvas
- **THEN** the active chain becomes empty
- **AND** measure mode remains active
- **AND** the status hint reads `MEASURE · pick first point`

#### Scenario: Right-click on an empty active chain is a quiet no-op

- **WHEN** measure mode is active, the active chain is empty, and the
  user right-clicks on the canvas
- **THEN** the browser context menu does not appear
- **AND** no committed chain is altered
- **AND** the status hint is unchanged

#### Scenario: Enter commits a multi-pick active chain

- **WHEN** measure mode is active, the active chain has 3 picks, and
  the user presses `Enter`
- **THEN** the active chain becomes a committed chain (drawn with
  solid segments, endpoint dots, and per-segment labels)
- **AND** the active chain is reset to empty
- **AND** the live `Δx` / `Δy` HTML readout is hidden
- **AND** measure mode remains active

#### Scenario: Enter on a single-pick active chain resets without committing

- **WHEN** measure mode is active, the active chain has exactly 1
  pick, and the user presses `Enter`
- **THEN** the active chain is reset to empty
- **AND** no committed chain is appended
- **AND** measure mode remains active

#### Scenario: Multiple committed chains coexist on the canvas

- **WHEN** the user has committed two chains via Enter and is in the
  middle of building a third active chain with 2 picks
- **THEN** the canvas shows both committed chains with their solid
  segments, endpoint dots, and per-segment labels
- **AND** the canvas shows the active chain's solid segment plus the
  live dashed rubber-band from the second active pick to the cursor
- **AND** each committed chain has its own clickable "✕" cancel button
  next to its first segment's label

#### Scenario: Clicking a committed chain's ✕ removes only that chain

- **WHEN** two committed chains exist plus a 2-pick active chain, and
  the user left-clicks the first committed chain's ✕ button
- **THEN** the first committed chain is removed from the canvas
- **AND** the second committed chain is still drawn unchanged
- **AND** the active chain still has its 2 picks
- **AND** the entity selection is unchanged

#### Scenario: Esc clears every chain in one keystroke

- **WHEN** two committed chains plus a 2-pick active chain exist, and
  the user presses `Esc`
- **THEN** every committed chain is removed
- **AND** the active chain is reset to empty
- **AND** measure mode remains active
- **AND** the status hint reads `MEASURE · pick first point`

#### Scenario: Esc on an empty measure mode falls through to the cascade

- **WHEN** measure mode is active, neither active nor committed chains
  exist, the scan-all overlay is open, and the user presses `Esc`
- **THEN** the scan-all overlay closes (existing cascade behavior)
- **AND** measure mode remains active

#### Scenario: Toggling measure mode off clears committed chains

- **WHEN** two committed chains plus an active chain exist, and the
  user presses `D` (or clicks the Measure button) to exit measure mode
- **THEN** every committed chain is removed from the canvas
- **AND** the active chain is reset to empty
- **AND** the Measure button is no longer marked active
- **AND** the entity selection from before entering measure mode is
  preserved

#### Scenario: Status hint announces saved chains

- **WHEN** measure mode is active, the active chain is empty, and 3
  committed chains exist
- **THEN** the status hint reads
  `MEASURE · pick first point · 3 chains saved (Esc to clear all)`

#### Scenario: Right-click is suppressed everywhere on the canvas while measure mode is active

- **WHEN** measure mode is active and the user right-clicks on any
  part of the canvas (over geometry or over empty space)
- **THEN** the browser context menu does not appear
- **AND** when measure mode is subsequently toggled off, right-click on
  the canvas restores the default browser context menu

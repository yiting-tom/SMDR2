## ADDED Requirements

### Requirement: Per-role sibling-DXF dropdown switcher

The viewer header SHALL render one role slot for each of the four
hardcoded part roles (`SBT`, `BD`, `POD`, `RING`) in that left-to-right
order. Each slot's appearance and interaction SHALL be determined by
the number of DXFs the current file's product has under that role,
read from `files_by_role_all[role]` on the `GET /api/products/{id}`
payload (the backend already serves this list — see the
`product-files` capability):

- **Zero DXFs** in the role → render an unclickable
  `role-btn role-btn.empty` (dashed border, dimmed colour, `title`
  attribute states the role is not uploaded).
- **Exactly one DXF**, and that DXF IS the currently-loaded viewer
  file → render a `role-btn.current` (cyan accent, non-link, no
  cursor) — the engineer is already there.
- **Exactly one DXF**, and that DXF is NOT the currently-loaded
  file → render a plain `<a class="role-btn" href="/viewer/{file_id}">`
  that navigates to that file.
- **Two or more DXFs** in the role → render a
  `<button class="role-btn role-btn--multi" aria-haspopup="menu">`
  whose label includes the count (e.g., `BD ×3 ▾`). The button
  SHALL also carry the `.current` class when the role matches the
  currently-loaded file's role. Clicking the button SHALL toggle a
  dropdown menu, described below.

When the dropdown opens, it SHALL list each sibling DXF in
`files_by_role_all[role]` order (the backend orders `multi` first,
then `top`, `bottom`, `side`). Each item SHALL be labelled with the
file's `name` (the uploaded DXF filename); when `name` is missing the
item SHALL fall back to the file's `dxf_view` enum, then to its `id`.
Items SHALL be real `<a href="/viewer/{file_id}">` so middle-click
/ cmd-click open in a new tab — EXCEPT the item whose `file_id`
equals the currently-loaded viewer file, which SHALL be a non-link
element marked active (matching the `role-btn.current` cyan
accent).

Only one dropdown SHALL be open at a time. Opening one dropdown
SHALL close any other open dropdown in the switcher. The dropdown
SHALL close on: outside-click, selecting an item (browser navigates
away), pressing `Esc`, or opening a different role's dropdown.
Pressing `Esc` while no dropdown is open SHALL NOT intercept the
viewer's existing Esc handlers (mark-mode cancel, measure-tool
cancel, etc.).

The four role names SHALL remain hardcoded in the client so that
the toolbar always renders four slots in a stable order — empty
roles included — acting as an upload-progress checklist for the
engineer.

#### Scenario: Single-DXF role still renders as a one-click button
- **WHEN** the current file's product has exactly one DXF under role `POD`
- **AND** the currently-loaded file is NOT that POD file
- **THEN** the `POD` slot renders as a plain `<a>` with `href="/viewer/{pod_file_id}"` and no dropdown affordance
- **AND** clicking it navigates directly to that file with no extra interaction

#### Scenario: Multi-DXF role exposes every sibling via a dropdown
- **WHEN** the product has DXFs `A.top` and `A.bottom` under role `BD`
- **AND** the currently-loaded file is `A.top`
- **THEN** the `BD` slot renders as a `role-btn role-btn--multi current` button labelled `BD ×2 ▾`
- **AND** clicking the button opens a menu listing `top` (marked active, non-link) and `bottom` (a link to `/viewer/{A.bottom.id}`)

#### Scenario: Multi-DXF on a non-current role still opens a menu
- **WHEN** the product has DXFs `B.multi` and `B.top` under role `SBT`
- **AND** the currently-loaded file is NOT either of those SBT files (it's the product's `BD` file)
- **THEN** the `SBT` slot renders as `role-btn role-btn--multi` (without `.current`) labelled `SBT ×2 ▾`
- **AND** clicking opens a menu of both SBT siblings, both of which are real navigable links

#### Scenario: Middle-click on a dropdown item opens a new tab
- **WHEN** the engineer opens the dropdown for a multi-DXF role
- **AND** middle-clicks (or cmd-clicks) any sibling item that is not the current file
- **THEN** the browser opens that sibling's viewer URL in a new tab, matching the existing single-link-button behaviour

#### Scenario: Esc closes the dropdown without disturbing other Esc handlers
- **WHEN** the engineer presses `Esc` while a role dropdown is open
- **THEN** the dropdown closes and focus returns to its trigger button
- **WHEN** the engineer presses `Esc` while no role dropdown is open and a measure-tool operation is active
- **THEN** the measure-tool cancels exactly as before this change (the role-switcher's Esc handler is a no-op)

#### Scenario: Empty role keeps the dashed placeholder
- **WHEN** the product has zero DXFs under role `RING`
- **THEN** the `RING` slot renders as `role-btn.empty` (dashed border) with no dropdown
- **AND** clicking it does nothing — same as today

#### Scenario: The current file's role with a single DXF stays non-interactive
- **WHEN** the currently-loaded file is the product's only DXF under role `BD`
- **THEN** the `BD` slot renders as `role-btn.current` with no dropdown — the engineer is already viewing the only choice

#### Scenario: Opening one dropdown closes another
- **WHEN** the engineer opens the `BD` dropdown
- **AND** clicks the `SBT` dropdown trigger
- **THEN** the `BD` dropdown closes
- **AND** the `SBT` dropdown opens
- **AND** at most one dropdown is open at any given moment

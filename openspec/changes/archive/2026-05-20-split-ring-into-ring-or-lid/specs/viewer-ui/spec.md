## MODIFIED Requirements

### Requirement: Per-role sibling-DXF dropdown switcher

The viewer header SHALL render four conceptual role positions, in
left-to-right order:

1. `SBT`
2. `BD`
3. `POD`
4. A **split sub-slot pair** with `RING` on the left and `LID` on
   the right (see "Per-product 4th-slot pair rendering" below)

Positions 1–3 are immutable single-role slots and follow the
existing per-slot rules below. Position 4 is two adjacent role
buttons (`RING` on the left, `LID` on the right) sharing one
conceptual position so the toolbar still presents four columns —
each half is an independent role-btn for hit-testing.

**Per-product 4th-slot pair rendering:**

- Product has 0 DXFs under `RING` AND 0 under `LID` → both halves
  render as enabled empty placeholders (`role-btn.empty`); the
  engineer MAY click or drag to either half.
- Product has ≥1 DXF under `RING` and 0 under `LID` → the left
  (RING) half renders by the usual single-role rules below; the
  right (LID) half renders as a **disabled** placeholder
  (`role-btn.empty.disabled`) with `cursor: not-allowed`, no click
  / drop response, and a `title` attribute identifying one file id
  that locked the product into the RING configuration.
- Product has 0 under `RING` and ≥1 under `LID` → mirror of the
  above: right (LID) half renders normally; left (RING) half is
  disabled with the symmetric explanatory `title`.
- Product has ≥1 under BOTH → unreachable in normal operation
  (upload enforces mutual exclusion in `product-files`). If the
  front-end nonetheless encounters this state (stale cache, manual
  DB edit), both halves SHALL render normally and the front-end
  SHALL log a console warning naming both file id sets.

For slots 1–3 (always `SBT`/`BD`/`POD`) and for each half of the
4th-slot pair (each treated as its own single-role slot keyed by
`"RING"` or `"LID"`), the appearance and interaction SHALL be
determined by the number of DXFs the current file's product has
under that role, read from `files_by_role_all[role]` on the
`GET /api/products/{id}` payload (the backend already serves this
list — see the `product-files` capability):

- **Zero DXFs** in the role → render an unclickable
  `role-btn role-btn.empty` (dashed border, dimmed colour, `title`
  attribute states the role is not uploaded). For the 4th-slot pair
  specifically, when the opposite half holds ≥1 file the empty half
  SHALL additionally carry the `.disabled` modifier (see "Per-product
  4th-slot pair rendering" above).
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

The four slot positions SHALL remain hardcoded in the client so that
the toolbar always renders four conceptual columns in a stable
order — empty roles included — acting as an upload-progress
checklist for the engineer. The 4th position SHALL always render
both halves (RING on the left, LID on the right); only their
enabled/disabled/populated states vary per product. Positions 1–3
are immutable single-role slots.

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
- **WHEN** the product has zero DXFs under role `BD`
- **THEN** the `BD` slot renders as `role-btn.empty` (dashed border) with no dropdown
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

#### Scenario: 4th-slot pair both halves empty when product has neither RING nor LID
- **WHEN** the product has zero DXFs under both `RING` and `LID`
- **THEN** the 4th position renders two adjacent role-btns: a `RING` half and a `LID` half
- **AND** both halves carry `role-btn.empty` (dashed border) and are enabled (click / drop targets active)
- **AND** neither half carries `.disabled`

#### Scenario: LID half is disabled when product already holds a RING file
- **WHEN** the product has one DXF under `RING` and zero under `LID`
- **THEN** the left (RING) half renders by the single-DXF rules (button or `.current` per current-file membership)
- **AND** the right (LID) half renders as `role-btn.empty.disabled` with `cursor: not-allowed`
- **AND** the disabled half's `title` attribute names at least one RING file id that locked the configuration
- **AND** clicking or dropping onto the disabled half SHALL be a no-op

#### Scenario: RING half is disabled when product already holds a LID file
- **WHEN** the product has one DXF under `LID` and zero under `RING`
- **THEN** the right (LID) half renders by the single-DXF rules
- **AND** the left (RING) half renders as `role-btn.empty.disabled` and is non-interactive
- **AND** the disabled half's `title` attribute names at least one LID file id that locked the configuration

#### Scenario: Current-file highlighting works on the LID half
- **WHEN** the currently-loaded viewer file has `dxf_role = "LID"` and is the product's only LID file
- **THEN** the right (LID) half renders as `role-btn.current` (cyan accent, non-link)
- **AND** the left (RING) half is `role-btn.empty.disabled`

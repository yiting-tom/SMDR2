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

**Sibling navigability gate:**

A sibling DXF SHALL be **navigable** from the switcher only when its
`status` is `ready_to_match`. A sibling still in the pipeline
(`discovering_layers`, `awaiting_layers`, or `preprocessing`) or in
`error` SHALL render as a **disabled, non-navigable** control: it
SHALL NOT carry an `href` (or, in a dropdown, SHALL be a non-link
element), SHALL carry the `.disabled` (or
`role-menu__item--disabled`) styling, and SHALL carry a `title`
stating why it is locked. The localised reasons SHALL be: `error` →
`<ROLE> 處理失敗`; `awaiting_layers` → `<ROLE> 尚未挑選圖層`;
otherwise (`discovering_layers` / `preprocessing`) →
`<ROLE> 尚未完成 preprocess`. This prevents opening a file whose
`/primitives` fetch would return HTTP 425 and leave a blank canvas.
The `status` field is already present on every entry of
`files_by_role_all[role]` (the backend serialises it via
`FileRecord.to_dict()`), so no extra payload is required.

**Per-product 4th-slot pair rendering:**

Each half of the 4th-slot pair SHALL be rendered independently
according to the per-slot rules below, using its own role key
(`"RING"` for the left half, `"LID"` for the right). Neither half's
rendering SHALL depend on the file count of the opposite half — both
halves MAY be enabled placeholders, both MAY hold ≥1 DXF, or any
mixed combination. The `.disabled` modifier SHALL NOT be applied to
either half on the basis of files held under the opposite role.

For slots 1–3 (always `SBT`/`BD`/`POD`) and for each half of the
4th-slot pair (each treated as its own single-role slot keyed by
`"RING"` or `"LID"`), the appearance and interaction SHALL be
determined by the number of DXFs the current file's product has
under that role, read from `files_by_role_all[role]` on the
`GET /api/products/{id}` payload (the backend already serves this
list — see the `product-files` capability):

- **Zero DXFs** in the role → render an unclickable
  `role-btn role-btn.empty` (dashed border, dimmed colour, `title`
  attribute states the role is not uploaded).
- **Exactly one DXF**, and that DXF IS the currently-loaded viewer
  file → render a `role-btn.current` (cyan accent, non-link, no
  cursor) — the engineer is already there.
- **Exactly one DXF**, that DXF is NOT the currently-loaded file,
  **and its `status` is `ready_to_match`** → render a plain
  `<a class="role-btn" href="/viewer/{file_id}">` that navigates to
  that file.
- **Exactly one DXF**, that DXF is NOT the currently-loaded file,
  **and its `status` is NOT `ready_to_match`** → render a
  `role-btn.disabled` with no `href` and a `title` giving the
  localised lock reason; it SHALL NOT navigate.
- **Two or more DXFs** in the role → render a
  `<button class="role-btn role-btn--multi" aria-haspopup="menu">`
  whose label includes the count (e.g., `BD ×3 ▾`). The button
  SHALL also carry the `.current` class when the role matches the
  currently-loaded file's role. Clicking the button SHALL toggle a
  dropdown menu, described below. The trigger SHALL remain clickable
  regardless of sibling statuses (it only opens the menu); the menu
  gates each sibling individually.

When the dropdown opens, it SHALL list each sibling DXF in
`files_by_role_all[role]` order (the backend orders `multi` first,
then `top`, `bottom`, `side`). Each item SHALL be labelled with the
file's `name` (the uploaded DXF filename); when `name` is missing the
item SHALL fall back to the file's `dxf_view` enum, then to its `id`.
A sibling whose `status` is `ready_to_match` and whose `file_id` is
NOT the currently-loaded file SHALL be a real `<a href="/viewer/{file_id}">`
so middle-click / cmd-click open in a new tab. The item whose
`file_id` equals the currently-loaded viewer file SHALL be a non-link
element marked active (matching the `role-btn.current` cyan accent).
A sibling whose `status` is NOT `ready_to_match` SHALL be a non-link
`role-menu__item--disabled` `<span>` carrying the localised lock
reason as its `title`, and SHALL NOT navigate.

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
enabled/populated states vary per product. Positions 1–3 are
immutable single-role slots.

#### Scenario: Single-DXF role still renders as a one-click button
- **WHEN** the current file's product has exactly one DXF under role `POD`
- **AND** the currently-loaded file is NOT that POD file
- **AND** the POD file's `status` is `ready_to_match`
- **THEN** the `POD` slot renders as a plain `<a>` with `href="/viewer/{pod_file_id}"` and no dropdown affordance
- **AND** clicking it navigates directly to that file with no extra interaction

#### Scenario: Single-DXF role whose file is not ready renders disabled
- **WHEN** the current file's product has exactly one DXF under role `POD`
- **AND** that POD file's `status` is `preprocessing` (still in the pipeline)
- **THEN** the `POD` slot renders as a `role-btn.disabled` with no `href`
- **AND** it carries the `title` `POD 尚未完成 preprocess`
- **AND** clicking it does not navigate

#### Scenario: Multi-DXF role exposes every sibling via a dropdown
- **WHEN** the product has DXFs `A.top` and `A.bottom` under role `BD`
- **AND** the currently-loaded file is `A.top`
- **AND** both `A.top` and `A.bottom` are `ready_to_match`
- **THEN** the `BD` slot renders as a `role-btn role-btn--multi current` button labelled `BD ×2 ▾`
- **AND** clicking the button opens a menu listing `top` (marked active, non-link) and `bottom` (a link to `/viewer/{A.bottom.id}`)

#### Scenario: Not-ready sibling in a dropdown is non-navigable
- **WHEN** the product has DXFs `B.multi` (ready) and `B.top` (`status` `awaiting_layers`) under role `SBT`
- **AND** the currently-loaded file is `B.multi`
- **THEN** the dropdown lists `B.top` as a non-link `role-menu__item--disabled` `<span>` with the `title` `SBT 尚未挑選圖層`
- **AND** the dropdown trigger is still clickable so `B.multi` remains reachable

#### Scenario: Multi-DXF on a non-current role still opens a menu
- **WHEN** the product has DXFs `B.multi` and `B.top` under role `SBT`
- **AND** the currently-loaded file is NOT either of those SBT files (it's the product's `BD` file)
- **AND** both SBT siblings are `ready_to_match`
- **THEN** the `SBT` slot renders as `role-btn role-btn--multi` (without `.current`) labelled `SBT ×2 ▾`
- **AND** clicking opens a menu of both SBT siblings, both of which are real navigable links

#### Scenario: Middle-click on a dropdown item opens a new tab
- **WHEN** the engineer opens the dropdown for a multi-DXF role
- **AND** middle-clicks (or cmd-clicks) any ready sibling item that is not the current file
- **THEN** the browser opens that sibling's viewer URL in a new tab, matching the existing single-link-button behaviour

#### Scenario: Esc closes the dropdown without disturbing other Esc handlers
- **WHEN** the engineer presses `Esc` while a role dropdown is open
- **THEN** the dropdown closes and focus returns to its trigger button
- **WHEN** the engineer presses `Esc` while no role dropdown is open and a measure-tool operation is active
- **THEN** the measure-tool cancels exactly as before this change (the role-switcher's Esc handler is a no-op)

#### Scenario: Empty role keeps the dashed placeholder
- **WHEN** the product has zero DXFs under role `BD`
- **THEN** the `BD` slot renders as `role-btn.empty` (dashed border) with no dropdown

## ADDED Requirements

### Requirement: Viewer surfaces a not-ready file instead of a blank canvas

The viewer bootstrap (`load()` in `app/static/canvas.js`) SHALL check the
`ok` status of its `GET /api/files/{id}/primitives` response before parsing
the body, and SHALL NOT call `.json()` or render a canvas when the response
is not OK. The primitives endpoint returns HTTP 425 for any file not in
`ready_to_match` (its `_resolve_file` guard), so on a non-OK response the
viewer SHALL set a human-readable status message and stop, so the operator
never sees a permanently blank canvas with the status line stuck on
`fetching…`.

On HTTP 425 specifically the message SHALL state that the drawing has
not finished preprocessing and direct the operator back to the
Products page to wait; on any other non-OK status the message SHALL
include the HTTP status code.

This is a defence-in-depth layer behind the switcher gate: the
switcher prevents the operator from clicking through to a not-ready
file, and this handler ensures that even a direct/bookmarked
`/viewer/{id}` URL for a not-ready file fails legibly rather than
silently.

#### Scenario: Opening a not-ready file's viewer fails legibly

- **WHEN** the operator navigates directly to `/viewer/{id}` for a file whose `status` is `preprocessing`
- **AND** the bootstrap's `GET /api/files/{id}/primitives` returns HTTP 425
- **THEN** the viewer SHALL NOT parse the response as primitives and SHALL NOT render a canvas
- **AND** the status line SHALL show a message that the drawing is not ready and to wait on the Products page

#### Scenario: A non-425 fetch error shows the status code

- **WHEN** the bootstrap's primitives fetch returns a non-OK status other than 425 (e.g. 500)
- **THEN** the status line SHALL show a message including that HTTP status code
- **AND** the viewer SHALL NOT render a blank canvas as if the fetch had succeeded

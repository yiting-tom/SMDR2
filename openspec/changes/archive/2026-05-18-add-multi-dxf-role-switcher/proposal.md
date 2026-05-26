## Why

Commit `c01a923 Product files: allow multiple DXFs per (product, role)`
let each `(product, role)` carry any number of DXFs (see the
`product-files` capability — "Multiple DXFs per (product, role)" and
"Per-DXF view rectangles"). The backend already exposes the full list
via `files_by_role_all` on `GET /api/products/{id}`. But the viewer
header's role switcher still hardcodes one `<a>` per role and reads
only `files_by_role[role]` (the primary/first file), so:

- Engineers viewing one DXF of a multi-DXF role cannot reach the
  sibling DXFs without dropping back to the dashboard, opening the
  product, and clicking the sibling row.
- The current file's view (`multi` / `top` / `bottom` / `side`) is
  invisible in the header — there's no signal that a `BD` role has,
  say, both a `top` and a `bottom` DXF that the engineer might want
  to flip between while reviewing the same part.

The viewer-as-iteration-tool is the user's primary surface
([[project_smdr2_workflow]], [[project_smdr2_template_flow]]), so any
context switch that forces a return to the dashboard is a real
productivity hit when sibling DXFs are common (a substrate plus its
flip-side, a board's top and bottom, etc.).

## What Changes

- The viewer header's role switcher SHALL surface every sibling DXF
  for the four hardcoded roles (`SBT`, `BD`, `POD`, `RING`) via the
  `files_by_role_all` payload (which the backend already returns).
- Each role button keeps its single-button footprint when its role
  has ≤ 1 file. Roles with ≥ 2 files SHALL display a small count
  badge / caret affordance, and clicking the button SHALL open a
  dropdown listing every sibling DXF by `dxf_view` label, with the
  currently-viewed DXF marked as active.
- For the viewer's own role (the role of the currently-loaded file),
  the dropdown SHALL always open on click (even when the role has
  exactly one DXF — the engineer can confirm "this is the only one")
  if and only if the role has ≥ 2 DXFs. With exactly one DXF the
  current behaviour holds: button shows `current` styling, no
  interaction.
- Each dropdown item SHALL be a real `<a href="/viewer/{file_id}">`
  so middle-click / cmd-click open in a new tab, matching the
  existing single-link button.
- Empty roles (no DXFs) keep the existing `empty` styling and
  unclickable behaviour.
- The four role names SHALL remain hardcoded (`["SBT", "BD", "POD",
  "RING"]`) — they're tracked alongside the server-side
  `VALID_ROLES = ("SBT", "BD", "POD", "RING")` tuple and the
  hardcoding gives the toolbar a stable visual layout including
  empty placeholders. Future role additions will require touching
  both ends, with the existing `if (role not in VALID_ROLES)`
  server-side guard catching mismatches.
- No backend changes required — `files_by_role_all` is already
  served. No new API surface, no new persisted state.

## Capabilities

### Modified Capabilities
- `viewer-ui`: add a new requirement for the per-role
  sibling-DXF dropdown switcher in the viewer header.

## Impact

- **Frontend (`app/static/canvas.js`)**: `loadFileInfo` reads
  `files_by_role_all[role]` instead of `files_by_role[role]`,
  branches on list length, and renders either a single `<a>` (≤ 1
  file) or a `<button>` + `<ul>` dropdown (≥ 2 files). Click-outside
  and Esc close the menu.
- **Frontend (`app/static/style.css`)**: new selectors for the
  dropdown container, the count badge / caret, and the menu items;
  re-uses the existing `role-btn` colour palette for visual
  consistency.
- **No template change** — the existing `<span id="role-switcher">`
  container is sufficient; the dropdown DOM is fully constructed in
  JS.
- **No backend change** — `files_by_role_all` already round-trips
  through `GET /api/products/{id}` (see `_group_files_by_role` in
  `app/main.py`).
- **No persisted-state change**.
- **Tests**: extend the viewer-frontend smoke (Playwright or a
  jsdom-style harness if one exists; otherwise leave for manual
  verification — see Open Questions in design.md).

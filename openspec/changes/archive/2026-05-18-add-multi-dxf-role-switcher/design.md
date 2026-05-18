# Design

## Dropdown vs. inline buttons vs. sub-row

Three plausible UX shapes were considered:

| Shape | Pros | Cons |
|---|---|---|
| Inline per-DXF buttons (`SBT·multi` `BD·top` `BD·bot`) | No hidden state — every DXF is one click away | Toolbar grows linearly with siblings; on a 4-role product with two views per role the header becomes 8 chips, then 12, and there's already a chain/sides row competing for vertical space ([[viewer_autocad_ux]]) |
| Sub-row under current role | Cheap visual cue for which role is "active" | Only surfaces siblings of the *current* role; other roles' siblings stay hidden, which is the exact same regression we're fixing for the current role |
| Click dropdown per role (chosen) | One button per role keeps the header layout fixed; siblings reachable in 2 clicks; same affordance for every role | One extra click vs. inline; needs outside-click + Esc plumbing |

The dropdown shape was chosen to keep the header's role-button row
visually identical to today on the common single-DXF-per-role case
(zero regression for users who haven't started uploading siblings)
while making siblings reachable uniformly for every role.

## Trigger: click, not hover

Hover-open menus are unreliable on touch devices (the user wants
this on a laptop trackpad but the rest of the viewer is
deliberately AutoCAD-style mouse-first per [[feedback_autocad_ux]],
which means click-driven). Hover-only menus also accidentally open
during pan / zoom mouse travel across the header. Decision: open
only on explicit click (or keyboard activation on the trigger
button); close on outside-click, on Esc, on selecting an item, or
on navigating to a new viewer page (the `<a>` does that for us).

## Affordance: count + caret, not just caret

A bare caret (▾) tells the user "there's a menu" but not "how
much." Showing `BD ×3 ▾` makes the multi-DXF case visible at a
glance and gives the engineer something to scan for ("which roles
on this product have siblings I should review?"). The badge is
only shown when count ≥ 2; single-DXF roles stay visually
identical to today.

## Marking the current DXF inside the dropdown

When the role of the dropdown is the *current* file's role, the
dropdown item for the current file's `id` SHALL be marked active
(e.g., reusing the existing `.current` styling on the menu item
itself). The item SHALL still be a non-link element so clicking it
is a no-op — matches the "no self-nav" pattern of the current
role-button.

## Labels: file `name` (uploaded filename)

The dropdown labels each sibling with the file's uploaded `name`
(the DXF filename) rather than the `dxf_view` enum. Rationale: the
engineer's mental model of "which DXF is this" is the filename
they uploaded (e.g., `BGA_top_rev3.dxf`), not the abstract view
tag. Multiple siblings often share the same `dxf_view` (e.g., two
`multi` revs of an SBT), so the view enum alone is ambiguous as
the disambiguator. The dropdown is `position: absolute` with
`white-space: nowrap`, so long filenames extend the menu width
without disturbing the header layout. Fallback chain
`name → dxf_view → id` keeps the item readable even if the name
field is somehow missing.

## Why no backend change

`_group_files_by_role` (`app/main.py:194-219`) already builds and
returns `files_by_role_all` keyed by role with the full sibling list
in stable view order. The viewer's `/api/products/{id}` call already
includes this field. Adding the dropdown is purely
client-side rendering off existing data.

## Why keep `["SBT", "BD", "POD", "RING"]` hardcoded

The four role names are the user-facing PCB/packaging vocabulary
and are unlikely to expand in the near term. The hardcode lets the
toolbar render 4 stable slots — including `empty`-styled
placeholders for roles the engineer hasn't uploaded yet, which acts
as a checklist nudge ("you haven't done POD yet"). Reading the
roles dynamically from the API payload would lose that nudge unless
the backend also returns empty roles, which it already does via
`by_role_all: dict[str, list[dict]] = {role: [] for role in VALID_ROLES}`
— so dynamic *would* work. The deliberate choice to keep the
hardcode is documented here so a future drift-detection PR doesn't
"clean it up" without reading the proposal.

## Outside-click + Esc plumbing

When the dropdown is open:

- Click anywhere outside the dropdown trigger or menu → close.
- Press `Esc` → close, return focus to the trigger button.
- Click a menu item that is a real `<a>` → browser navigates; the
  page reloads so the menu state resets naturally.
- Open another dropdown (different role) → close the previous one
  (only one menu open at a time).

The viewer already has global `keydown` handlers for measure / pan
/ chain modes; the new `Esc` handler MUST yield to those when a
measure / chain operation is in progress (i.e., the dropdown's Esc
handler should be a no-op unless the dropdown is open).

## Accessibility

- The trigger button is `<button type="button">` with
  `aria-haspopup="menu"` and `aria-expanded` toggled to reflect
  state.
- The menu is `<ul role="menu">` with each item a
  `<li role="none">` containing `<a role="menuitem">`.
- Keyboard activation on the trigger opens the menu; arrow keys
  navigate items; Esc closes. (Lower priority — if the harness
  doesn't allow easy keyboard testing, defer arrow-key nav to a
  follow-up.)

## Open Questions

- **Should the menu also support a "compare" action** (open two
  sibling DXFs side-by-side)? Out of scope for this change. Flag
  for a future change if the engineer asks.
- **Frontend test harness**: there's currently no Playwright or
  jsdom suite for the viewer header. The new behaviour is verified
  manually (see tasks.md §3). If a harness lands, port the menu
  behaviour scenarios from `specs/viewer-ui/spec.md` into it.

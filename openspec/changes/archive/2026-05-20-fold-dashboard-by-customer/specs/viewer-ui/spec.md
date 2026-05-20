## ADDED Requirements

### Requirement: Dashboard products grouped into foldable customer sections

The dashboard at `GET /` SHALL group product cards by their
`library_id` (the customer dimension) into foldable sections.
Each section SHALL render:

- A header element with role `button` and `tabindex="0"` showing the
  customer name, the count of products in the section, and a
  chevron (`▸` when folded, `▾` when expanded). The whole header
  SHALL be clickable; Enter and Space SHALL toggle the section
  while the header has focus.
- A container of product cards, hidden when the section is folded.
  When expanded the cards SHALL retain their full existing
  appearance and behavior (header, slot grid, footer with Rule
  Check / Download All Match / Delete, etc.).

Customer sections SHALL be ordered by library name
(case-insensitive ascending), with `library_id` as the deterministic
tiebreak when two libraries share a name.

Libraries with zero products SHALL NOT render a section at all (no
empty headers, no zero-count placeholders).

Sections SHALL default to **folded** on first page load (i.e. when
no fold-state record exists in storage). Fold state SHALL persist
under `sessionStorage` key `smdr2.dashboard.foldedCustomers`, whose
value SHALL be a JSON array of `library_id` strings representing
the currently folded sections. The renderer SHALL treat the absence
of the key as "every section folded".

When the fold state references a `library_id` that no longer exists
(library deleted), the renderer SHALL ignore the stale entry; no
active pruning is required.

The library bar at the top of the page, the New Library / New
Product buttons, the per-card actions, and every existing endpoint
SHALL remain unchanged. This requirement is purely a presentation
layer transform.

#### Scenario: First page load shows all sections folded
- **WHEN** the dashboard loads with `smdr2.dashboard.foldedCustomers` absent from sessionStorage
- **AND** the user has products under at least two libraries
- **THEN** every customer section renders with the `▸` chevron
- **AND** no product cards are visible

#### Scenario: Clicking a folded header expands the section
- **WHEN** the user clicks a section header whose chevron is `▸`
- **THEN** the section's product cards become visible
- **AND** the header's chevron becomes `▾`
- **AND** the `aria-expanded` attribute updates to `"true"`
- **AND** `sessionStorage["smdr2.dashboard.foldedCustomers"]` no longer contains that library's id

#### Scenario: Clicking an expanded header folds the section
- **WHEN** the user clicks a section header whose chevron is `▾`
- **THEN** the section's product cards are hidden
- **AND** the header's chevron becomes `▸`
- **AND** `sessionStorage["smdr2.dashboard.foldedCustomers"]` contains that library's id

#### Scenario: Keyboard activation toggles the section
- **WHEN** a customer-section header has keyboard focus
- **AND** the user presses Enter or Space
- **THEN** the section toggles its fold state exactly as a click would
- **AND** the default page-scroll behavior of Space SHALL NOT fire

#### Scenario: Empty library is hidden
- **WHEN** a library exists but no product references its `library_id`
- **THEN** the dashboard renders no section for that library
- **AND** the library nonetheless remains selectable in the top-bar library dropdown

#### Scenario: Section header includes a product count
- **WHEN** a customer has N products (N ≥ 1)
- **THEN** the section header's text SHALL include `(N products)` (or `(1 product)` for N = 1)

#### Scenario: Stale folded id is ignored after library deletion
- **WHEN** `sessionStorage["smdr2.dashboard.foldedCustomers"]` contains a library id whose library no longer exists
- **THEN** the renderer SHALL skip that id silently
- **AND** the remaining customer sections render normally

#### Scenario: Customer sections render in alphabetical order
- **WHEN** the dashboard has products under libraries `Beta Co` and `acme corp`
- **THEN** the `acme corp` section renders above `Beta Co` (case-insensitive ascending)

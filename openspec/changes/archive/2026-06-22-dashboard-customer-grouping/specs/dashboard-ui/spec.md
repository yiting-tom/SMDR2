## ADDED Requirements

### Requirement: Dashboard groups products by customer

The dashboard product list SHALL group products under one header per customer,
showing the customer name and the count of products shown in that group. Groups
SHALL be ordered by customer name, with the `未分類` (uncategorized) group
ordered last. Within a group the existing product order SHALL be preserved.
Grouping SHALL use the `customer` name from `GET /api/products` (see
`product-files`).

#### Scenario: Products render under their customer header
- **WHEN** the dashboard loads products belonging to two customers
- **THEN** each product appears under a header showing its customer's name
- **AND** each header shows the count of products shown in that group

#### Scenario: Uncategorized group is ordered last
- **WHEN** products exist for a named customer and for `未分類`
- **THEN** the named customer's group renders before the `未分類` group

### Requirement: Customer groups are collapsible with persisted fold state

Each customer group SHALL be collapsible by clicking its header. The set of
collapsed groups SHALL persist in `localStorage` and be restored on load, so a
collapse survives reload and navigation. Groups SHALL default to expanded when
no persisted state exists.

#### Scenario: Collapsing a group hides its products
- **WHEN** the user clicks a customer group header that is expanded
- **THEN** that group's products are hidden and the group shows as collapsed

#### Scenario: Fold state survives a reload
- **WHEN** the user collapses a customer group and reloads the dashboard
- **THEN** that group is still collapsed
- **AND** groups the user did not collapse are expanded

### Requirement: Dashboard filter by customer and text, persisted

The dashboard SHALL provide a customer filter that narrows the list to the
selected customers, alongside a product-name text search. An empty customer
selection SHALL show all customers. Both the customer selection and the text
query SHALL persist in `localStorage` and be restored on load. Filters SHALL be
applied before grouping, so empty groups do not render and group counts reflect
only shown products. When the active filters match no products, the dashboard
SHALL show an empty state with a way to clear the filters.

#### Scenario: Customer filter narrows the list
- **WHEN** the user selects one customer in the filter
- **THEN** only that customer's products (and group) are shown

#### Scenario: Text search narrows within the shown customers
- **WHEN** the user types a query in the product-name search
- **THEN** only products whose name matches are shown, within the customer filter

#### Scenario: Filter state survives navigation
- **WHEN** the user sets a customer filter and/or text query, leaves the
  dashboard, and returns
- **THEN** the same filters are still applied

#### Scenario: Filters matching nothing offer a clear action
- **WHEN** the active filters match no products
- **THEN** an empty state is shown with a control to clear the filters

### Requirement: Dashboard restores scroll position across navigation

The dashboard SHALL restore the previous scroll position when the user
navigates away into a product or version and returns, restoring after the
product list has rendered and then clearing the saved position. The position
SHALL persist in `sessionStorage` (per-tab). Restoration SHALL clamp to the
current scrollable height so a shorter (e.g. filtered) list does not error or
overscroll.

#### Scenario: Returning to the dashboard restores scroll
- **WHEN** the user scrolls down the product list, opens a product, then returns
  to the dashboard
- **THEN** the list is scrolled back to approximately the previous position

#### Scenario: Restore clamps to a shorter list
- **WHEN** the saved scroll position exceeds the current content height (e.g. a
  filter now hides much of the list)
- **THEN** the dashboard scrolls to the bottom of the available content without
  error

#### Scenario: A fresh load starts at the top
- **WHEN** there is no saved scroll position for the tab
- **THEN** the dashboard loads at the top

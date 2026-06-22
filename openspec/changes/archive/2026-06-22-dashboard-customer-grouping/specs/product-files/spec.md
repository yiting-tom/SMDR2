## ADDED Requirements

### Requirement: Product read API exposes the customer name

The product read endpoints SHALL include the product's resolved customer
display name as a `customer` field, in addition to the existing `customer_id`,
so clients can show and group by customer without a separate lookup. The name
SHALL be resolved from the customers table (e.g. `AUTH_STORE.get_customer`).
When no customer row matches the product's `customer_id` (e.g. a deleted
customer still referenced), the field SHALL fall back to the `customer_id`
string so it is always a non-empty label. The field is additive and advisory;
it does not change what any endpoint allows.

#### Scenario: List includes customer name per product
- **WHEN** an authenticated caller requests `GET /api/products`
- **THEN** each product object includes `customer_id` and a `customer` name
- **AND** `customer` equals the name of the customer row referenced by
  `customer_id`

#### Scenario: Uncategorized product resolves to the seeded name
- **WHEN** a product's `customer_id` is `"uncategorized"`
- **THEN** its `customer` field is the seeded display name `"未分類"`

#### Scenario: Missing customer row falls back to the id
- **WHEN** a product's `customer_id` has no matching customer row
- **THEN** its `customer` field equals the `customer_id` string
- **AND** the field is non-empty

#### Scenario: Single product read includes the customer name
- **WHEN** an authenticated caller requests `GET /api/products/{id}`
- **THEN** the response includes both `customer_id` and `customer`

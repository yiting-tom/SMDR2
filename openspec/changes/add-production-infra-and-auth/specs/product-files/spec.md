## ADDED Requirements

### Requirement: Product creation is admin-only and customer-bound
Creating a product SHALL require admin and SHALL specify the owning customer; products created without an explicit customer (legacy/dev paths) SHALL land under the seed customer `uncategorized`. Deleting a product SHALL require admin.

#### Scenario: Editor attempts product creation
- **WHEN** a user whose highest role is editor POSTs a new product
- **THEN** the request is rejected with 403

#### Scenario: Admin creates a product under a customer
- **WHEN** an admin creates a product specifying customer C
- **THEN** the product row carries `customer_id = C` and customer-scope grants over C now cover it

### Requirement: Product visibility follows viewer scope
Product listings and reads SHALL only include products where the caller's effective role is at least viewer (global, matching customer, or matching product scope). Users with no grants SHALL see an empty product list.

#### Scenario: Dept viewer sees only their customer's products
- **WHEN** a user's only grant is dept-viewer over customer C
- **THEN** product listings contain exactly the products under C

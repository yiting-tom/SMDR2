## ADDED Requirements

### Requirement: Product read API exposes the caller's effective role

The product read endpoints SHALL include the caller's effective role for
each product, computed by the same authorization function the write guards
use, so clients can gate affordances without re-deriving authorization. The
field is additive and advisory; it does not change what any endpoint allows.

#### Scenario: List includes effective_role per product
- **WHEN** an authenticated caller requests `GET /api/products`
- **THEN** each product object includes `effective_role` with one of
  `"viewer"`, `"editor"`, or `"admin"`
- **AND** the value equals `app.guards.effective_role(caller, product_id)`
  for that product

#### Scenario: Single product includes effective_role
- **WHEN** an authenticated caller requests `GET /api/products/{id}`
- **THEN** the response includes `effective_role` for that product

#### Scenario: Bypass-admin resolves to admin
- **WHEN** the app runs in default bypass mode
- **THEN** `effective_role` is `"admin"` for every visible product

#### Scenario: Only visible products are returned (unchanged)
- **WHEN** a caller with a product-scoped viewer grant requests
  `GET /api/products`
- **THEN** only products they can read are returned, each carrying its
  `effective_role`

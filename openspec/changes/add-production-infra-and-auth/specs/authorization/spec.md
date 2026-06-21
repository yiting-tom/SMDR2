## ADDED Requirements

### Requirement: Grant model
Authorization SHALL be local (never read from Keycloak roles), expressed as grants of role (`admin` | `editor` | `viewer`) to a grantee (`user` userid | `dept` deptid) over a scope (`global` | `customer` | `product`). The system SHALL enforce: admin grants are global + per-user only; dept grants are viewer-only (current policy, not a schema constraint); `scope_id` is `''` for global and a concrete id otherwise; duplicate grants are rejected.

#### Scenario: Dept editor grant rejected
- **WHEN** an admin tries to grant role=editor to a dept
- **THEN** the request fails with a validation error

#### Scenario: Duplicate grant
- **WHEN** an identical (grantee, role, scope) grant already exists
- **THEN** the second insert is rejected

### Requirement: Effective role resolution
For a product P the effective role SHALL be the highest role among grants matching (grantee = caller's userid OR caller's stored deptid) AND (scope = global OR P's customer OR P), with admin > editor > viewer. Dept matching SHALL use the users-row deptid, not a live JWT value.

#### Scenario: Customer-scope editor edits a product under that customer
- **WHEN** a user holds editor@customer C and product P belongs to C
- **THEN** their effective role for P is editor

#### Scenario: No grants
- **WHEN** a logged-in user holds no grants (and their dept holds none)
- **THEN** their effective role everywhere is none and they see no products

### Requirement: Endpoint access matrix
Read endpoints SHALL require effective viewer or higher on the target's scope; write endpoints (upload, version create, library/template edits, match runs, rule-check, sign-off) SHALL require effective editor or higher; grant management, customer management, product create/delete, sign-off revert, and lock force-release SHALL require admin. Editors SHALL be able to sign off versions within their scope, including versions they created.

#### Scenario: Viewer attempts a write
- **WHEN** a user with only viewer role POSTs a template edit
- **THEN** the request is rejected with 403

#### Scenario: Editor signs off own version
- **WHEN** an editor with scope over product P signs off a version they created under P
- **THEN** the sign-off succeeds and is audited

### Requirement: Customer grouping
`customer` SHALL be a grouping level above product (one customer, many products). Creating, renaming, and deleting customers SHALL be admin-only; a customer with products SHALL NOT be deletable; the seed customer (`uncategorized`) SHALL NOT be deletable.

#### Scenario: Delete non-empty customer
- **WHEN** an admin deletes a customer that still has products
- **THEN** the request is rejected

### Requirement: Bootstrap admins
At startup the system SHALL idempotently grant global admin to every userid listed in `BOOTSTRAP_ADMINS`.

#### Scenario: Repeated startup
- **WHEN** the app boots twice with the same `BOOTSTRAP_ADMINS`
- **THEN** exactly one admin grant per listed user exists

### Requirement: Audit log
The system SHALL append audit entries (actor, action, target, detail, timestamp) for at least: sign-off/unsign, grant create/revoke, lock force-release, first login, product/customer create/delete, and template add/delete/modify including class strategy changes.

#### Scenario: Grant revoked
- **WHEN** an admin revokes a grant
- **THEN** an audit entry records the revoked grant's full content

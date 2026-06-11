## ADDED Requirements

### Requirement: Keycloak BFF login
The system SHALL authenticate users via Keycloak OIDC using the BFF pattern: the backend performs the Authorization Code + PKCE exchange and issues an HttpOnly session cookie; tokens SHALL never reach frontend JavaScript. The userid SHALL be the JWT `preferred_username` claim.

#### Scenario: Unauthenticated request in oidc mode
- **WHEN** `SMDR2_AUTH_MODE=oidc` and a request carries no valid session
- **THEN** API requests receive 401 and page requests are redirected to Keycloak login

#### Scenario: Successful callback
- **WHEN** Keycloak redirects to `/auth/callback` with a valid code
- **THEN** the backend exchanges it via `OIDC_INTERNAL_BASE`, validates `iss` against `OIDC_ISSUER`, upserts the user row, and sets an HttpOnly SameSite=Lax cookie

### Requirement: First-login provisioning and claim refresh
On first login the system SHALL auto-create the user row with NO grants and write a `user.first_login` audit entry. On every login the system SHALL refresh `deptid`, `deptname`, `email`, `name` from the JWT so department-based grants follow transfers.

#### Scenario: User changes department
- **WHEN** a user whose stored deptid is D100 logs in with a JWT carrying deptid D200
- **THEN** the users row is updated to D200 and D100 dept grants no longer apply to them

### Requirement: Server-side session lifecycle
Sessions SHALL be stored server-side keyed by the SHA-256 of the cookie token (no plaintext tokens at rest), with an idle timeout of 8 hours and an absolute lifetime of 24 hours. Expired sessions SHALL be pruned periodically.

#### Scenario: Idle expiry
- **WHEN** a session sees no request for more than 8 hours
- **THEN** the next request is treated as unauthenticated

### Requirement: CSRF protection
State-changing requests (non-GET) SHALL require a CSRF token bound to the session, sent via the `X-CSRF-Token` header.

#### Scenario: Missing CSRF token
- **WHEN** a POST arrives with a valid session cookie but no/incorrect CSRF token
- **THEN** the request is rejected with 403

### Requirement: Bypass mode preserves current behaviour
With `SMDR2_AUTH_MODE=bypass` (the default until cutover) the system SHALL synthesize an admin identity from `SMDR2_DEV_USER` without contacting Keycloak, so dev environments and the test suite behave exactly as before auth landed.

#### Scenario: Test suite under bypass
- **WHEN** the suite runs with no auth-related env set
- **THEN** all endpoints behave as a global admin and no login is required

### Requirement: Internal endpoint exemption
Health/liveness/metrics endpoints SHALL be reachable without a session in all modes.

#### Scenario: Probe in oidc mode
- **WHEN** the k8s liveness probe hits the health endpoint with no cookie
- **THEN** it receives 200

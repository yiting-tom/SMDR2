## MODIFIED Requirements

### Requirement: Sign-off may carry an optional evidence image

`POST /api/versions/{vid}/sign-off` SHALL accept an optional multipart field
`evidence` (PNG/JPEG/WebP, validated by magic bytes, ≤10MB). When present, the
image SHALL be stored at the deterministic blob key
`sign_off_evidence/{version_id}` and `versions.evidence_name`/`evidence_type`
SHALL be set atomically with the sign-off. A request without the field SHALL
behave byte-identically to the pre-change endpoint. Evidence SHALL be readable
via `GET /api/versions/{vid}/sign-off/evidence` (viewer guard), SHALL be
deleted on admin unsign (it belongs to the revoked sign-off event), SHALL NOT
be cloned to new versions, and SHALL be included in the product-delete
cascade's enumerated keys.

#### Scenario: Sign-off with evidence

- **WHEN** an editor signs off a version attaching a PNG
- **THEN** the version freezes as today, `evidence_name` carries the original
  filename, the audit detail records it, and any viewer in scope can fetch the
  image with the stored MIME type

#### Scenario: Sign-off without evidence (back-compat)

- **WHEN** the existing UI posts to sign-off with no body
- **THEN** the version freezes exactly as before and `evidence_name` is NULL

#### Scenario: Non-image rejected

- **WHEN** the uploaded bytes are not PNG/JPEG/WebP by magic bytes (or exceed
  10MB)
- **THEN** the request is rejected 415/413 and the version is NOT signed off

#### Scenario: Unsign clears evidence

- **WHEN** an admin unsigns a version that has evidence
- **THEN** the evidence blob and columns are cleared; a later re-sign-off may
  attach a fresh image

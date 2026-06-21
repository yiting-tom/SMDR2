## MODIFIED Requirements

### Requirement: Background pre-processing with pre-match

For every uploaded file the system SHALL run a background pipeline that
parses the DXF, builds the entity shape index, runs scan-all against
the file's library snapshot, and persists the parsed primitives and the
pre-match handle-by-class to disk under `data/parsed/{file_id}.json`
and `data/prematch/{file_id}.json`.

The persisted pre-match snapshot SHALL additionally carry a
`library_revision` field set to the library's `current_revision` (see
`template-library`) at the time the snapshot is computed, so a later read
can detect whether the library has changed since the snapshot was written.

#### Scenario: Pre-match against an empty library
- **WHEN** preprocessing completes for a file whose library has no templates
- **THEN** `data/prematch/{file_id}.json` exists with `{by_class: {}, total: 0}`
- **AND** it carries the library's current `library_revision`

#### Scenario: Pre-match against a populated library
- **WHEN** preprocessing completes for a file whose library has at least one template
- **THEN** `data/prematch/{file_id}.json` contains handles grouped by class
- **AND** the totals match the sum of unique handles across classes
- **AND** it carries the library's current `library_revision`

## ADDED Requirements

### Requirement: Pre-match endpoint reports staleness

`GET /api/files/{file_id}/prematch` SHALL return the cached pre-match snapshot
together with a boolean `stale` flag. The flag SHALL be `true` when the snapshot
is absent, carries no `library_revision`, or carries a `library_revision` that
differs from the file's library `current_revision`; otherwise it SHALL be
`false`. The endpoint SHALL NOT recompute the snapshot — it only reports
freshness so the client can decide whether to fall through to a live scan.

#### Scenario: Fresh snapshot reports not stale
- **WHEN** the snapshot's `library_revision` equals the library's current revision
- **THEN** the response carries the snapshot's `by_class`/`total`
- **AND** `stale` is `false`

#### Scenario: Snapshot from before a library mutation reports stale
- **WHEN** a template is committed to the library after the snapshot was written
- **AND** the snapshot's `library_revision` no longer matches the current revision
- **THEN** the response sets `stale` to `true`

#### Scenario: Missing or unstamped snapshot reports stale
- **WHEN** no snapshot exists for the file, or the snapshot carries no `library_revision`
- **THEN** the response sets `stale` to `true`
- **AND** `total` is `0`

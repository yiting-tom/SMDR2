## MODIFIED Requirements

### Requirement: Auto-shown pre-match on viewer load

When the viewer loads a file, it SHALL fetch the cached pre-match snapshot via
`GET /api/files/{file_id}/prematch` and display the overlay automatically so the
user sees the library's coverage of the file without manual intervention.

When that response is **fresh** (`stale: false` and `total > 0`), the viewer
SHALL render the snapshot directly without issuing a live scan.

When that response is **not usable** — `stale: true`, the snapshot is missing,
or `total == 0` — and the file is past `awaiting_layers`, the viewer SHALL fall
through to a single live Scan All (the same path as a manual Scan All) instead of
silently rendering nothing, so the auto overlay is complete on arrival. The
fall-through SHALL fire at most once per load and SHALL NOT loop. The viewer
SHALL NOT write the live result back into the cached snapshot.

#### Scenario: Viewer shows fresh pre-match without user action
- **WHEN** the viewer page loads a file whose snapshot is fresh and non-empty
- **THEN** the per-class overlay is rendered automatically from the snapshot
- **AND** no `GET /api/files/{file_id}/scan-all` request is issued

#### Scenario: Stale snapshot self-heals to a live scan
- **WHEN** the viewer loads a ready file whose snapshot is `stale: true`
- **THEN** the viewer issues a single live Scan All
- **AND** the overlay reflects the current library, not the stale snapshot

#### Scenario: Missing or empty snapshot self-heals to a live scan
- **WHEN** the viewer loads a ready file whose snapshot is missing or has `total == 0`
- **THEN** the viewer issues a single live Scan All
- **AND** the overlay is not left silently empty

#### Scenario: Not-ready file does not self-heal
- **WHEN** the viewer loads a file that is still at or before `awaiting_layers`
- **THEN** no live Scan All is issued

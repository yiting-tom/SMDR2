## ADDED Requirements

### Requirement: Save Match refreshes the pre-match snapshot

A completed Save Match job SHALL rewrite `data/prematch/{file_id}.json` from the
same live library scan it runs to build the Match JSON, so the auto-shown
pre-match overlay on a later viewer load reflects every template in the file's
current library — including templates committed after the file was
preprocessed. The refreshed snapshot SHALL use the same not-side-aware
`{by_class: {display_name: [handle, ...]}, total}` contract `_preprocess_worker`
writes: a per-display-class handle **union** taken before view-split
(`split_matches_by_side`) and before contained-match suppression (the union is
invariant to suppression).

Refreshing the snapshot SHALL be best-effort and SHALL NOT fail the Match JSON
the job has already persisted: if the snapshot cannot be written, the system
SHALL log a warning and leave the previous snapshot in place.

#### Scenario: Save Match adds a post-preprocess class to the snapshot

- **WHEN** a file's stored pre-match snapshot was written at preprocess time, when its library had no template matching class `C`
- **AND** a template for class `C` is later committed and the operator runs Save Match, whose live scan matches handles for `C`
- **THEN** `data/prematch/{file_id}.json` SHALL be rewritten to include class `C` with the handles the live scan matched
- **AND** the next viewer load's auto-shown overlay SHALL show class `C` without a manual Scan All

#### Scenario: Snapshot keeps the not-side-aware union shape

- **WHEN** Save Match refreshes the pre-match snapshot
- **THEN** the snapshot SHALL contain a per-class handle union, not the view-split per-instance shape of the Match JSON
- **AND** its `total` SHALL equal the sum of unique handles across classes

#### Scenario: A snapshot-refresh failure does not fail Save Match

- **WHEN** the Match JSON has been written but the pre-match snapshot cannot be rewritten (e.g. a filesystem error)
- **THEN** the Save Match job SHALL still complete successfully with its Match JSON persisted
- **AND** the system SHALL log a warning and retain the previous snapshot

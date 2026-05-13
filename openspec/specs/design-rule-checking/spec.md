# design-rule-checking Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
### Requirement: RuleChecking JSON output shape

`check_rules(dxf_path, match_json, entity_shapes=None)` SHALL return a
dict keyed by rule name; each value SHALL be a dict with the keys
`checkRule` (string description), `pass` (bool), and `handleIds` (list
of DXF entity handles relevant to the rule). The shape SHALL match the
contract of the downstream design rule checker.

#### Scenario: Output is a dict of rule payloads
- **WHEN** `check_rules("ignored.dxf", {})` is called
- **THEN** the result is a dict where every value has keys `checkRule`, `pass`, `handleIds`
- **AND** `pass` is a `bool` and `handleIds` is a `list`

### Requirement: Mock Rule1 — substrate-to-first-SMD distance

The mock checker SHALL implement Rule1: the distance between the
substrate's combined centroid and the first SMD match's combined
centroid SHALL exceed 5 mm. `handleIds` SHALL contain the substrate's
handle(s) plus all entities of the first SMD match.

#### Scenario: Far-apart substrate and SMD passes
- **WHEN** the substrate is at (0,0) and the first SMD is at (100,0)
- **THEN** Rule1 passes
- **AND** `handleIds` contains the substrate and SMD entity handles

#### Scenario: Close substrate and SMD fails
- **WHEN** the distance between substrate and first SMD is below 5 mm
- **THEN** Rule1 fails
- **AND** the description text contains the threshold value

#### Scenario: Missing substrate fails Rule1
- **WHEN** the Match JSON has no `substrate.*` entries
- **THEN** Rule1 fails with a description explaining what is missing

### Requirement: Rule check API and persistence

`POST /api/files/{file_id}/rule-check` SHALL load the file's persisted
Match JSON, invoke `check_rules` with the file's entity shapes, and
write the result to `data/rule_check/{file_id}.json`. The response
SHALL include the per-rule results and pass/fail counts. `GET` on the
same path SHALL return the most recently persisted result.

#### Scenario: Run rule check after Save Match
- **WHEN** the user has saved a Match JSON for a file
- **AND** invokes `POST /api/files/{id}/rule-check`
- **THEN** the response contains `rule_count`, `pass_count`, `fail_count`, `results`
- **AND** the result is persisted to `data/rule_check/{file_id}.json`

#### Scenario: Rule check before Save Match fails clearly
- **WHEN** the user invokes rule check on a file with no saved Match JSON
- **THEN** the API returns 400 with a message indicating Match JSON is missing

### Requirement: Rule panel hover and pinned highlight

In the viewer, the rule-check panel SHALL highlight a rule's `handleIds`
on the canvas when the rule item is hovered (ephemeral) and pin them
when clicked (persistent until clicked again or another rule is
clicked). The pinned rule's card SHALL have a distinct visual indicator
(left border + tint). Closing the panel and re-running rule check SHALL
clear any pinned state.

#### Scenario: Hover highlights then clears
- **WHEN** the user hovers a rule row
- **THEN** the rule's handleIds are highlighted in yellow on the canvas
- **WHEN** the cursor leaves the row
- **THEN** the yellow highlight clears

#### Scenario: Click pins the highlight and marks the card
- **WHEN** the user clicks a rule row
- **THEN** the rule's handleIds remain highlighted after the cursor leaves
- **AND** the card shows a yellow left-border and tinted background

#### Scenario: Click again unpins
- **WHEN** the user clicks the already-pinned rule
- **THEN** the highlight clears and the card returns to its default style


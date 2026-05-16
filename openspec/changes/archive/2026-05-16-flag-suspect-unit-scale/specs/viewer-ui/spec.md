## ADDED Requirements

### Requirement: Dashboard flags suspect unit scale on a per-file basis

For each file shown on the dashboard, the server SHALL compute a
`unit_scale_warning` field derived from the persisted INSUNITS value
and the bbox diagonal, and the dashboard SHALL display a yellow
`⚠ unit` badge in the file's slot cell whenever the field is
non-null. Hovering the badge SHALL show a human-readable detail
string spelling out the raw INSUNITS value, the bbox diagonal, and
the reason the file is flagged. The warning SHALL be informational
only — it SHALL NOT block opening the file or running rule-check.

The derivation SHALL follow this table:

| insunits | bbox diagonal | warning value |
|---|---|---|
| any         | ≤ 100           | `null` |
| 4 / 5 / 6   | 100 ≤ D ≤ 1000  | `null` |
| 4 / 5 / 6   | > 1000          | `"suspect_scale"` |
| 0           | > 100           | `"suspect_scale"` |
| 0           | ≤ 100           | `"unitless"` |
| other / null| > 1000          | `"suspect_scale"` |
| other / null| otherwise       | `null` |

Legacy file rows whose INSUNITS column is `NULL` SHALL return `null`
warning (no badge) until they are re-preprocessed.

#### Scenario: A unitless file with packaging-scale bbox gets a mild warning
- **WHEN** a file with `insunits == 0` and bbox diagonal of 80 mm is rendered on the dashboard
- **THEN** the slot cell shows a `⚠ unit` badge
- **AND** the badge's `title` text contains `"INSUNITS=0"` and `"diagonal=80"`
- **AND** the warning kind is `"unitless"`

#### Scenario: A 1000×-scale file gets a strong warning
- **WHEN** a file with `insunits == 0` and bbox diagonal of 42_000 is rendered on the dashboard
- **THEN** the slot cell shows a `⚠ unit` badge
- **AND** the warning kind is `"suspect_scale"`

#### Scenario: A normal mm-scale file shows no badge
- **WHEN** a file with `insunits == 4` and bbox diagonal of 300 mm is rendered on the dashboard
- **THEN** the slot cell does not contain a `warn-badge`

#### Scenario: A legacy file with NULL insunits shows no badge
- **WHEN** a file uploaded before this change has `insunits == None` in its record
- **THEN** the slot cell does not contain a `warn-badge`
- **AND** re-preprocessing the file populates `insunits` and surfaces the badge if the heuristic now fires

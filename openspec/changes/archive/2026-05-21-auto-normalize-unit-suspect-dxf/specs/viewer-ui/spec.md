## MODIFIED Requirements

### Requirement: Dashboard flags suspect unit scale on a per-file basis

For each file shown on the dashboard, the server SHALL compute a
`unit_scale_warning` field derived from the persisted INSUNITS value
and the bbox diagonal, and SHALL include the per-file `applied_scale`
multiplier on the same payload. The dashboard SHALL render the
file's slot cell based on these fields as follows:

- If `applied_scale != 1.0` — the file was auto-rescaled — the slot
  SHALL display a neutral informational pill `ℹ rescaled <human>`
  (no warning colour). `<human>` SHALL be derived from the factor:
  - `M = 0.001` → `"÷1000"`
  - `M = 0.01`  → `"÷100"`
  - `M = 0.1`   → `"÷10"`
  - `M = 10`    → `"×10"`
  - `M = 100`   → `"×100"`
  - `M = 1000`  → `"×1000"`
  - `M = 25.4`  → `"×25.4 (inch)"`
  - any other declared-unit factor → `"×<factor>"` plus the unit
    suffix from the source INSUNITS
  The pill's `title` SHALL include the raw INSUNITS value, the
  **pre-rescale** bbox diagonal, and the applied factor.
- Else if `unit_scale_warning` is non-null — the file looks
  suspicious but no rescale was applied — the slot SHALL display
  the existing yellow `⚠ unit` badge with the existing detail text.
- Else the slot SHALL display nothing for unit scale.

The warning / pill SHALL be informational only — it SHALL NOT block
opening the file or running rule-check.

The `unit_scale_warning` derivation SHALL follow this table, applied
to the **pre-rescale** bbox diagonal so the heuristic is stable
regardless of auto-rescale:

| insunits | bbox diagonal (pre-rescale) | warning value |
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

The payload contract:

- `unit_scale_warning`: `null` | `"unitless"` | `"suspect_scale"`
- `unit_scale_warning_detail`: human-readable text. When
  `applied_scale != 1.0`, the text SHALL spell out the factor and
  the source unit, e.g.
  `"INSUNITS=0, pre-rescale diagonal=42000 → auto-rescaled ×0.001 (mm)"`
  or
  `"INSUNITS=1 (inch) → auto-rescaled ×25.4 (mm)"`.
- `applied_scale`: numeric multiplier, defaults to `1.0`.

#### Scenario: A 1000×-too-big rescaled file shows the informational pill
- **WHEN** a file with `insunits == 0`, pre-rescale bbox diagonal of 42 000, and `applied_scale == 0.001` is rendered on the dashboard
- **THEN** the slot cell shows a `ℹ rescaled ÷1000` pill (not the yellow warning badge)
- **AND** the pill's `title` text contains `"INSUNITS=0"`, `"diagonal=42000"`, and `"×0.001"`

#### Scenario: A declared-inch rescaled file shows the inch pill
- **WHEN** a file with `insunits == 1` and `applied_scale == 25.4` is rendered on the dashboard
- **THEN** the slot cell shows a `ℹ rescaled ×25.4 (inch)` pill
- **AND** the pill's `title` text contains `"INSUNITS=1"` and `"×25.4"`

#### Scenario: A unitless file with packaging-scale bbox still warns
- **WHEN** a file with `insunits == 0`, bbox diagonal of 80 mm, and `applied_scale == 1.0` is rendered on the dashboard
- **THEN** the slot cell shows a `⚠ unit` badge
- **AND** the badge's `title` text contains `"INSUNITS=0"` and `"diagonal=80"`
- **AND** the warning kind is `"unitless"`

#### Scenario: A 1000×-scale file that wasn't auto-rescaled still warns
- **WHEN** a file with `insunits == 4`, bbox diagonal of 42 000 mm, and `applied_scale == 1.0` is rendered on the dashboard
- **THEN** the slot cell shows a `⚠ unit` badge (declared mm + large bbox is not auto-rescaled)
- **AND** the warning kind is `"suspect_scale"`

#### Scenario: A normal mm-scale file shows nothing
- **WHEN** a file with `insunits == 4`, bbox diagonal of 300 mm, and `applied_scale == 1.0` is rendered on the dashboard
- **THEN** the slot cell does not contain a `warn-badge`
- **AND** the slot cell does not contain a `rescaled-pill`

#### Scenario: A legacy file with NULL insunits shows no badge
- **WHEN** a file uploaded before the auto-rescale change has `insunits == None` and `applied_scale == 1.0`
- **THEN** the slot cell does not contain a `warn-badge`
- **AND** re-preprocessing the file populates `insunits` (and may set `applied_scale` if it triggers the auto-rescale rule), then surfaces the appropriate pill / badge

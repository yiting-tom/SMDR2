## ADDED Requirements

### Requirement: Dashboard surfaces DXF recover notes

The per-file dashboard payload (the JSON returned by `GET /api/files`
and `GET /api/files/{file_id}`) SHALL include the field
`dxf_recover_notes`, mirroring the value stored in
`FileRecord.dxf_recover_notes`. The field SHALL be `null` for files
that parsed via strict mode and a JSON object for files that took
the recover fallback. When present the object SHALL carry, at
minimum, the keys `strict_error`, `n_fixed`, `n_unrecoverable`, and
`audit_messages` (a list).

For each file shown on the dashboard, when `dxf_recover_notes` is
non-null the slot cell SHALL display a neutral informational pill
reading `ℹ recovered (Nfixed/Munrecoverable)` — where `Nfixed` is
`n_fixed` and `Munrecoverable` is `n_unrecoverable`. The pill's
visual style SHALL match the existing `ℹ rescaled` pill (same
colour family, same monospace label form, same neutral chrome).
The pill's `title` attribute SHALL include the value of
`strict_error` so hover inspection surfaces the original parser
error.

When the file ALSO carries a `rescaled` pill (the existing
unit-scale pattern) the dashboard SHALL render both pills side by
side; the recover pill SHALL appear after the rescale pill in
visual order.

#### Scenario: Strict-OK file shows no recover pill
- **WHEN** a file with `dxf_recover_notes IS NULL` is rendered on
  the dashboard
- **THEN** no recover-related pill is shown on its slot cell
- **AND** the file's payload includes `"dxf_recover_notes": null`

#### Scenario: Recovered file shows a recover pill with counts
- **WHEN** a file with
  `dxf_recover_notes == {"strict_error": "DXFStructureError: …",
  "n_fixed": 12, "n_unrecoverable": 1, "audit_messages": […]}`
  is rendered on the dashboard
- **THEN** the slot cell shows a pill reading
  `ℹ recovered (12/1)`
- **AND** the pill's `title` attribute contains
  `"DXFStructureError: …"`

#### Scenario: Recovered file with rescale also rendered shows both pills
- **WHEN** a file carries both a non-null `dxf_recover_notes` and
  `applied_scale != 1.0`
- **THEN** the slot cell shows the rescale pill first, then the
  recover pill
- **AND** both pills use the same neutral informational style

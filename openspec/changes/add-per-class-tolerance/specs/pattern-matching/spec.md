## MODIFIED Requirements

### Requirement: Transform-invariant matching

The matcher SHALL find candidates that match a template under any
combination of translation, rotation (any angle), mirroring (any axis),
and isotropic scaling within the closed interval [0.95, 1.05]. The
match acceptance threshold SHALL be a chamfer distance ε (default
`TOLERANCE_ABS = 0.05` in drawing units).

When the caller supplies a per-class tolerance override (set via the
template-library API), the matcher SHALL use that value in place of the
global default. The override is threaded through the `tolerance` kwarg
on `find_matches` / `find_matches_from_pointsets`. Callers that scan
class-by-class (`scan_all`, `save_match_json`, prematch worker) SHALL
resolve each class's tolerance before iterating its templates;
add-mode preview (`POST /api/files/{file_id}/match`) SHALL accept an
optional `class_name` in the request body and resolve tolerance from
that class when present.

#### Scenario: Translated copy matches
- **WHEN** the candidate is the template translated by a non-zero vector
- **THEN** `align_score` returns a chamfer distance below 1e-6

#### Scenario: Rotated copy matches at arbitrary angles
- **WHEN** the candidate is the template rotated by 30°, 90°, 137°, or 270°
- **THEN** `align_score` returns a chamfer distance below 1e-3

#### Scenario: Mirrored copy matches
- **WHEN** the candidate is the template mirrored across the y-axis
- **THEN** `align_score` returns a chamfer distance below 1e-3

#### Scenario: Within-tolerance scale matches
- **WHEN** the candidate is the template scaled by a factor in (0.95, 1.05)
- **THEN** `align_score` returns a non-None result with chamfer below 1e-2
- **AND** the reported scale lies inside [0.95, 1.05]

#### Scenario: Out-of-tolerance scale is rejected
- **WHEN** the candidate is the template scaled by 1.5
- **THEN** `align_score` returns None (caller treats as a near-miss)

#### Scenario: Per-class tolerance broadens acceptance
- **WHEN** class "Substrate" has `tolerance = 0.5` set
- **AND** a candidate template-substrate pair chamfers at 0.46 (above the 0.05 default but below 0.5)
- **AND** `scan_all` runs against that library
- **THEN** the candidate appears in `matches`, not `near_misses`

#### Scenario: Add-mode preview uses class tolerance when class_name given
- **WHEN** `POST /api/files/{file_id}/match` is called with `{"handles": [...], "class_name": "Substrate"}`
- **AND** the Substrate class has `tolerance = 0.5` set
- **THEN** matching uses 0.5 instead of `TOLERANCE_ABS = 0.05`

#### Scenario: Add-mode preview falls back to default when class_name absent or unknown
- **WHEN** `POST /api/files/{file_id}/match` is called without `class_name` (or with a name not present in the library)
- **THEN** matching uses `TOLERANCE_ABS = 0.05`

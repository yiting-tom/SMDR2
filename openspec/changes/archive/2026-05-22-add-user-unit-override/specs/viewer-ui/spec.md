## ADDED Requirements

### Requirement: Viewer unit-override picker

The viewer SHALL render a unit-override picker control in the
viewer header, co-located with the existing `library-switcher`
dropdown so it sits next to the other file-level interpretation
controls. The control SHALL:

- Present a dropdown labelled `Unit:` with exactly five options, in
  this order: `mm`, `cm`, `m`, `inch`, `μm`.
- Pre-select the option whose implied multiplier matches the file's
  current `applied_scale`:

  | `applied_scale` | selected option |
  |---|---|
  | `1.0`    | `mm`   |
  | `10.0`   | `cm`   |
  | `1000.0` | `m`    |
  | `25.4`   | `inch` |
  | `0.001`  | `μm`   |

  For any other multiplier (e.g. an unrecognised power-of-10 from a
  legacy auto-rescale), the dropdown SHALL select `mm` and display a
  trailing badge `(actual ×<scale>)` so the operator is not misled.
- Display a `set by you` badge to the right of the dropdown when the
  file row has `user_unit_override IS NOT NULL`. The badge is absent
  when authority is the detector.
- Display an inline soft hint `⚠ Differs from file declaration (<unit>)`
  to the right of the dropdown whenever the currently selected
  option's multiplier disagrees with the source `INSUNITS` mapping
  (e.g. selected = `mm` but `insunits == 1` → hint says
  `Differs from file declaration (inch)`). The hint is informational
  only — it SHALL NOT disable the dropdown or block submission.
- Be disabled (greyed out, non-interactive) while a recompute job
  triggered by this picker is in flight; the in-flight job id SHALL
  be displayed adjacent to the dropdown so cross-session recovery
  works the same way as rule-check.

When the operator picks a value that differs from the currently
selected option, the viewer SHALL open a confirm modal **before**
firing any POST. The modal SHALL state, plainly:

1. Preprocess will re-run for this file.
2. Cached connectivity and pre-match for this file will be rebuilt.
3. Match JSON for every product containing this file will be cleared
   and need to be re-run; the modal SHALL include the count of
   affected products and the names of the first three (then "and N
   more" if applicable).
4. The override can be undone by picking the detector's choice
   again — the modal SHALL state which unit that is.

Only when the operator confirms the modal SHALL the viewer POST to
`/api/files/{file_id}/unit-override` and switch the picker into the
disabled / job-in-flight state.

#### Scenario: Picker default reflects detector-derived applied_scale
- **WHEN** a file with `applied_scale == 25.4` and `user_unit_override IS NULL` is opened in the viewer
- **THEN** the dropdown's selected option is `inch`
- **AND** no `set by you` badge is rendered
- **AND** if `insunits == 1`, no soft hint is rendered (selection matches declaration)

#### Scenario: Picker shows "set by you" when override is active
- **WHEN** a file with `user_unit_override == "mm"` and `applied_scale == 1.0` is opened
- **THEN** the dropdown's selected option is `mm`
- **AND** a `set by you` badge is rendered next to the dropdown

#### Scenario: Soft hint appears when selection contradicts INSUNITS
- **WHEN** a file with `insunits == 1` (inch) has its override set to `"mm"` and is opened
- **THEN** the dropdown's selected option is `mm`
- **AND** the inline hint reads `⚠ Differs from file declaration (inch)`

#### Scenario: Changing the picker opens the confirm modal first
- **WHEN** the operator picks a new unit different from the current selection
- **THEN** a confirm modal appears with the four enumerated points above
- **AND** the affected-products count and first-three names are shown
- **AND** no POST has been fired yet

#### Scenario: Cancelling the modal does not change state
- **WHEN** the confirm modal is open and the operator clicks cancel
- **THEN** the dropdown reverts to the prior selection
- **AND** no POST is fired
- **AND** the file row is unchanged

#### Scenario: Confirming the modal POSTs and disables the picker
- **WHEN** the operator confirms the modal
- **THEN** the viewer POSTs `{"unit": <selected>}` to `/api/files/{file_id}/unit-override`
- **AND** on `202`, the picker enters the disabled / job-in-flight state with the returned `job_id` shown
- **AND** on `409`, the picker enters the same disabled state but displays the conflict's `job_id`

#### Scenario: Picker re-enables after the recompute job completes
- **WHEN** the in-flight recompute job for this file finishes successfully
- **THEN** the picker becomes interactive again
- **AND** the dropdown re-selects the option matching the post-recompute `applied_scale`
- **AND** the `set by you` badge reflects the post-recompute `user_unit_override`

### Requirement: Dashboard rescaled pill annotates user-override origin

When the dashboard renders the `ℹ rescaled <human>` pill (per the
existing "Dashboard flags suspect unit scale on a per-file basis"
requirement), and the file row has `user_unit_override IS NOT NULL`,
the pill text SHALL be suffixed with ` (user override)`. The pill's
colour SHALL remain the same neutral informational style — the
suffix is the sole visible difference.

The per-file dashboard payload SHALL include a `user_unit_override`
field carrying the string value or `null`. Existing fields
(`applied_scale`, `unit_scale_warning`, `unit_scale_warning_detail`,
`insunits`) SHALL retain their existing semantics.

The pill's `title` text SHALL additionally include
`user_unit_override=<value>` when the override is set, so hover
inspection makes the origin explicit.

#### Scenario: Override-driven rescale shows the suffix
- **WHEN** a file with `applied_scale == 25.4` and `user_unit_override == "inch"` is rendered on the dashboard
- **THEN** the slot cell shows a pill reading `ℹ rescaled ×25.4 (inch) (user override)`
- **AND** the pill's `title` includes `"user_unit_override=inch"`

#### Scenario: Detector-driven rescale shows no suffix
- **WHEN** a file with `applied_scale == 0.001` and `user_unit_override IS NULL` is rendered on the dashboard
- **THEN** the slot cell shows a pill reading `ℹ rescaled ÷1000`
- **AND** the pill's `title` does not contain `"user_unit_override"`

#### Scenario: Override that lands at applied_scale == 1.0 shows no rescale pill but still annotates
- **WHEN** a file with `applied_scale == 1.0` and `user_unit_override == "mm"` is rendered on the dashboard
- **THEN** the existing requirement's rule applies — no rescale pill is shown (because `applied_scale == 1.0`)
- **AND** the payload still carries `"user_unit_override": "mm"` for clients that need it

## MODIFIED Requirements

### Requirement: RuleChecking JSON output shape

The function `check_rules(product_id, bundle_dir)` SHALL return a
dict keyed by rule name. Each value SHALL be a dict with the keys
`pass` (bool), `text` (string, the overall rule description / failure
reason), and `rules` (list of zero or more sub-rules).

Each sub-rule SHALL be a dict with these keys:

| Key | Type | Meaning |
|---|---|---|
| `part` | `"SBT"` \| `"BD"` \| `"POD"` \| `"RING"` \| `"LID"` | The role whose viewer should render this annotation. |
| `file_id` | `str` \| `null` | Full id of the DXF the sub-rule's geometry lives in. |
| `from` | `handleID` \| `null` | Single source DXF handle, raw / unprefixed. |
| `to` | `handleID` \| `null` | Single target DXF handle, raw / unprefixed. |
| `text` | `str` | Per-sub-rule message. |
| `tol` | `handleID` \| `null` | Annotation-only entity to highlight. Independent of `from` / `to` — may be set alone or in combination with them. |
| `tol_text` | `str` \| `null` | Label to render adjacent to `tol`. Only meaningful when `tol` is set. |

The shape SHALL satisfy these invariants:

- The outer `rules` array MAY be empty. When empty, the rule's
  overall `pass` / `text` SHALL still be present.
- When `rules` is non-empty, every sub-rule SHALL carry a non-empty
  `text` string.
- A sub-rule that sets any of `from`, `to`, or `tol` SHALL also set
  `file_id` to a non-null DXF id. A sub-rule with all three handle
  fields null SHALL also have `file_id` null.
- A sub-rule MAY have all of `from`, `to`, `tol`, and `tol_text`
  null. Such a "text-only" sub-rule carries only its `part` and
  `text` and is accepted by the adapter — it surfaces as an
  informational entry in the viewer sidebar with no geometry
  highlighted.
- `to` MAY only be set when `from` is also set. A sub-rule with
  `to` set but `from` null is rejected by the adapter.
- `tol_text` MAY only be set when `tol` is also set. A sub-rule
  with `tol_text` set but `tol` null is rejected by the adapter.

The viewer SHALL render each sub-rule per these display rules:

- **`from` + `to` both set**: draw a dashed line between the two
  entities along the shortest segment across their geometries
  (vertex-vs-edge perpendicular-foot search, so the line pins to the
  closest actual edges rather than bbox centres); render `text` at
  the midpoint of that segment.
- **`from` only (no `to`)**: highlight the `from` entity; render
  `text` adjacent to it.
- **`tol` set**: highlight the `tol` entity. When `tol_text` is also
  set, render `tol_text` adjacent to it.
- **Text-only (`from` / `to` / `tol` all null)**: render nothing on
  the canvas; the sub-rule still appears as an informational entry
  in the sidebar.
- The `from`/`to` rendering and the `tol` rendering MAY both occur
  for a single sub-rule when both groups are populated.

#### Scenario: Output is a dict of rule payloads
- **WHEN** `check_rules` returns
- **THEN** the result is a dict where every value has keys `pass`, `text`, `rules`
- **AND** `pass` is a `bool`, `text` is a `str`, and `rules` is a `list`
- **AND** every sub-rule in `rules` has the keys `part`, `file_id`, `from`, `to`, `text`, `tol`, `tol_text` with the documented types

#### Scenario: Empty rules list is valid
- **WHEN** a rule's `rules` array is empty
- **THEN** the envelope is still valid
- **AND** `pass` and `text` are still required and present

#### Scenario: Sub-rule with handle requires file_id
- **WHEN** a sub-rule sets `from`, `to`, or `tol` to a handle
- **THEN** `file_id` SHALL also be set to a non-null DXF id

#### Scenario: Text-only sub-rule is accepted
- **WHEN** a sub-rule has all of `from`, `to`, `tol`, and `tol_text` set to `null`
- **AND** `part` is a valid role value and `text` is a non-empty string
- **THEN** the adapter SHALL NOT reject the rule-check result
- **AND** the sub-rule persists into the rule-check JSON unchanged

#### Scenario: `to` without `from` is invalid
- **WHEN** a sub-rule sets `to` but leaves `from` null
- **THEN** the adapter rejects the rule-check result

#### Scenario: `tol_text` without `tol` is invalid
- **WHEN** a sub-rule sets `tol_text` but leaves `tol` null
- **THEN** the adapter rejects the rule-check result

#### Scenario: Handle without file_id is invalid
- **WHEN** a sub-rule sets any of `from`, `to`, or `tol` to a handle but leaves `file_id` null
- **THEN** the adapter rejects the rule-check result

#### Scenario: LID is a valid sub-rule part value
- **WHEN** an external rule emits a sub-rule with `part: "LID"`
- **THEN** the output validates against the RuleChecking schema and routes to the LID DXF's viewer

## REMOVED Requirements

### Requirement: Sub-rule must reference at least one entity
**Reason**: The external rule team needs to emit purely informational
sub-rules (status messages, category headers, notes) that don't pin
to any DXF entity. The constraint forced authors to invent
placeholder handles, and every downstream consumer already tolerates
all-null handles via `?? null` fallbacks — see `app/static/canvas.js`
`focusSubRule`. Replaced by the new "Text-only sub-rule is accepted"
scenario above.
**Migration**: None — the change only relaxes acceptance. Previously
valid payloads continue to validate. Authors who used placeholder
handles to satisfy the old constraint can drop them but don't have
to.

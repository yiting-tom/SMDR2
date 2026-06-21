## MODIFIED Requirements

### Requirement: RuleChecking JSON output shape

The function `check_rules(product_id, bundle_dir)` SHALL return a
dict keyed by rule name. Each value SHALL be a dict with the keys
`pass` (bool), `text` (string, the overall rule description / failure
reason), and `rules` (list of zero or more sub-rules).

Each sub-rule SHALL be a dict. It supports two presentation groups — a
**handle group** (entities referenced by DXF handle in the open file) and a
**coordinate group** (geometry given as raw points already in the open
file's world frame) — and a sub-rule MAY use either or both. Keys:

| Key | Type | Meaning |
|---|---|---|
| `part` | `"SBT"` \| `"BD"` \| `"POD"` \| `"RING"` \| `"LID"` | The role whose viewer should render this annotation. |
| `file_id` | `str` \| `null` | Full id of the DXF the sub-rule's handle geometry lives in. Required only for the handle group. |
| `from` | `handleID` \| `null` | Single source DXF handle, raw / unprefixed. |
| `from_entity` | `handleID` \| `null` | Alias of `from`. Normalised to `from`; if both are set they MUST be equal. |
| `to` | `handleID` \| `list[handleID]` \| `null` | Target DXF handle(s), raw / unprefixed. A single string is a one-target sub-rule; a non-empty list is a fan from `from` to each element. |
| `text` | `str` | Per-sub-rule message. Always rendered (sidebar), in both modes. |
| `tol` | `handleID` \| `null` | Annotation-only entity to highlight. Independent of `from` / `to`. |
| `tol_text` | `str` \| `null` | Label to render adjacent to `tol`. Only meaningful when `tol` is set. |
| `from_coordinates` | `[number, number]` \| `null` | Source point, in the open file's world frame (DXF mm). |
| `to_coordinates` | `[number, number]` \| `null` | Target point, in the open file's world frame. Paired with `from_coordinates`. |
| `to_entity` | `list[[number, number]]` \| `null` | Outline of a target entity (e.g. from another product's DXF), as raw points already in the open file's world frame. |

The shape SHALL satisfy these invariants:

- The outer `rules` array MAY be empty. When empty, the rule's
  overall `pass` / `text` SHALL still be present.
- Every sub-rule SHALL carry a non-empty `text` string.
- A sub-rule carries at least one **renderable group** (a handle group via
  `from`/`tol`, or a coordinate group via `from_coordinates`+`to_coordinates`
  and/or `to_entity`), OR is a text-only informational entry (no handle and
  no coordinate group) shown in the sidebar with nothing drawn on canvas.
- **Handle group:** a sub-rule that sets any of `from`, `to`, or `tol` SHALL
  also set `file_id` to a non-null DXF id. `to` MAY only be set when `from`
  (or its alias `from_entity`) is also set, for both the scalar and list
  forms. When `to` is a list it SHALL be non-empty and every element SHALL be
  a non-empty string handle; `to: []` is rejected (emit `null` for "no `to`").
  `from_entity` is validated as a handle and normalised to `from`.
- **Coordinate group:** `from_coordinates` and `to_coordinates` are each a
  length-2 array of finite numbers, and are **paired** — one present requires
  the other. `to_entity`, when set, is a **non-empty** list whose every
  element is a length-2 array of finite numbers; an empty `to_entity: []` is
  rejected (emit `null`). The coordinate group does **not** require `file_id`
  (its points are self-located in the open file's world frame).
- `tol_text` MAY only be set when `tol` is also set.

The viewer SHALL render each sub-rule per these display rules:

- **`from`/`from_entity` + scalar `to`**: draw a dashed line between the two
  entities along the shortest segment across their geometries
  (vertex-vs-edge perpendicular-foot search); render `text` at the segment
  midpoint.
- **`from` + list `to`**: for each element `to_i`, draw a dashed segment from
  `from` to `to_i` using the same shortest-path search. The sub-rule's `text`
  SHALL render at the midpoint of the **first** segment only.
- **`from` only (no `to`)**: highlight the `from` entity; render `text`
  adjacent to it.
- **`tol` set**: highlight the `tol` entity; when `tol_text` is set, render
  it adjacent.
- **`from_coordinates` + `to_coordinates`**: draw a **solid** line between
  the two points and render a **distance label in millimetres** at its
  midpoint.
- **`to_entity` set**: draw a **closed dashed polygon** — connect the points
  in order and join the last point back to the first — outlining the target
  entity.
- All applicable renderings MAY occur together for a single sub-rule.

#### Scenario: Output is a dict of rule payloads
- **WHEN** `check_rules` returns
- **THEN** the result is a dict where every value has keys `pass`, `text`, `rules`
- **AND** `pass` is a `bool`, `text` is a `str`, and `rules` is a `list`
- **AND** every sub-rule carries `part` and a non-empty `text`, with all present handle and coordinate fields matching their documented types

#### Scenario: Empty rules list is valid
- **WHEN** a rule's `rules` array is empty
- **THEN** the envelope is still valid
- **AND** `pass` and `text` are still required and present

#### Scenario: Sub-rule with handle requires file_id
- **WHEN** a sub-rule sets `from`, `to`, or `tol` to a handle
- **THEN** `file_id` SHALL also be set to a non-null DXF id

#### Scenario: Text-only informational sub-rule is accepted
- **WHEN** a sub-rule sets `part` and `text` but no handle group and no coordinate group
- **THEN** the envelope is valid
- **AND** the viewer shows `text` in the sidebar and draws nothing on canvas

#### Scenario: `to` without `from` is invalid (scalar form)
- **WHEN** a sub-rule sets `to: "AB12"` but leaves `from` and `from_entity` null
- **THEN** the adapter rejects the rule-check result

#### Scenario: `to` list without `from` is invalid
- **WHEN** a sub-rule sets `to: ["AB12", "CD34"]` but leaves `from` null
- **THEN** the adapter rejects the rule-check result

#### Scenario: `to` accepts a single string for one-target rules
- **WHEN** a sub-rule emits `to: "AB12"` with `from: "AA00"`
- **THEN** the envelope is valid
- **AND** the viewer renders one dashed segment from `from` to `to` with `text` at its midpoint

#### Scenario: `to` accepts a non-empty list of strings for fan-target rules
- **WHEN** a sub-rule emits `to: ["AB12", "CD34", "EF56"]` with `from: "AA00"`
- **THEN** the envelope is valid
- **AND** the viewer renders three dashed segments — `from`→`AB12`, `from`→`CD34`, `from`→`EF56`
- **AND** the sub-rule's `text` is rendered at the midpoint of the first segment only

#### Scenario: Empty list `to: []` is rejected
- **WHEN** a sub-rule emits `to: []`
- **THEN** the adapter rejects the rule-check result

#### Scenario: List `to` with a non-string element is rejected
- **WHEN** a sub-rule emits `to: ["AB12", 42]`
- **THEN** the adapter rejects the rule-check result

#### Scenario: LID is a valid sub-rule part value
- **WHEN** an external rule emits a sub-rule with `part: "LID"`
- **THEN** the output validates against the RuleChecking schema and routes to the LID DXF's viewer

#### Scenario: from_entity is accepted as an alias of from
- **WHEN** a sub-rule emits `from_entity: "AA00"` with `to: "AB12"` and a non-null `file_id`
- **THEN** the envelope is valid
- **AND** the sub-rule renders identically to one that used `from: "AA00"`

#### Scenario: from_entity conflicting with from is rejected
- **WHEN** a sub-rule sets both `from: "AA00"` and `from_entity: "BB11"` (different handles)
- **THEN** the adapter rejects the rule-check result

#### Scenario: Point-to-point coordinates render a distance line
- **WHEN** a sub-rule emits `from_coordinates: [10, 20]` and `to_coordinates: [13, 24]`
- **THEN** the envelope is valid without a `file_id`
- **AND** the viewer draws a solid line between the two points with a distance label in mm at its midpoint

#### Scenario: Unpaired coordinates are rejected
- **WHEN** a sub-rule sets `from_coordinates: [10, 20]` but leaves `to_coordinates` null
- **THEN** the adapter rejects the rule-check result

#### Scenario: Malformed coordinate is rejected
- **WHEN** a sub-rule emits `from_coordinates: [10]` or `to_coordinates: [10, "x"]`
- **THEN** the adapter rejects the rule-check result

#### Scenario: to_entity renders a closed dashed polygon
- **WHEN** a sub-rule emits `to_entity: [[0, 0], [5, 0], [5, 5], [0, 5]]`
- **THEN** the envelope is valid without a `file_id`
- **AND** the viewer draws a closed dashed polygon connecting the points in order and the last back to the first

#### Scenario: Empty to_entity is rejected
- **WHEN** a sub-rule emits `to_entity: []`
- **THEN** the adapter rejects the rule-check result
- **AND** the emitter is expected to send `to_entity: null` to mean "no outline"

#### Scenario: to_entity with a malformed point is rejected
- **WHEN** a sub-rule emits `to_entity: [[0, 0], [5]]`
- **THEN** the adapter rejects the rule-check result

#### Scenario: Cross-product measurement combines distance and outline
- **WHEN** a sub-rule emits `from_coordinates` + `to_coordinates` and a `to_entity` outline
- **THEN** the envelope is valid
- **AND** the viewer draws the solid distance line with its mm label and the closed dashed polygon together, with `text` shown in the sidebar

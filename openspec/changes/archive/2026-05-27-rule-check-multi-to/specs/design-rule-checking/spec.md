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
| `to` | `handleID` \| `list[handleID]` \| `null` | Target DXF handle(s), raw / unprefixed. A single string is a one-target sub-rule; a non-empty list is a fan from `from` to each element. |
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
- A sub-rule SHALL set at least one of `from`, `tol`. A sub-rule
  with both `from` and `tol` null carries no entity to highlight and
  is rejected by the adapter (see "External rule function contract").
- `to` MAY only be set when `from` is also set. A sub-rule with
  `to` set but `from` null is rejected by the adapter, whether `to`
  is a string or a list.
- When `to` is a list, it SHALL be non-empty and every element SHALL
  be a non-empty string handle. `to: []` is rejected by the adapter;
  emitters that mean "no `to`" SHALL send `null`. Mixed-type lists
  and lists containing non-string / empty-string entries are
  rejected.
- `tol_text` MAY only be set when `tol` is also set.

The viewer SHALL render each sub-rule per these display rules:

- **`from` + scalar `to` set**: draw a dashed line between the two
  entities along the shortest segment across their geometries
  (vertex-vs-edge perpendicular-foot search, so the line pins to the
  closest actual edges rather than bbox centres); render `text` at
  the midpoint of that segment.
- **`from` + list `to` set**: for each element `to_i` in the list,
  draw a dashed segment from `from` to `to_i` using the same
  shortest-path search as the scalar form. The sub-rule's `text`
  SHALL render at the midpoint of the **first** segment in the list
  (i.e. between `from` and `to[0]`), to avoid overlapping labels.
- **`from` only (no `to`)**: highlight the `from` entity; render
  `text` adjacent to it.
- **`tol` set**: highlight the `tol` entity. When `tol_text` is also
  set, render `tol_text` adjacent to it.
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

#### Scenario: Sub-rule must reference at least one entity
- **WHEN** a sub-rule has all of `from`, `tol` set to `null`
- **THEN** the adapter rejects the rule-check result rather than persisting it

#### Scenario: `to` without `from` is invalid (scalar form)
- **WHEN** a sub-rule sets `to: "AB12"` but leaves `from` null
- **THEN** the adapter rejects the rule-check result

#### Scenario: `to` list without `from` is invalid
- **WHEN** a sub-rule sets `to: ["AB12", "CD34"]` but leaves `from` null
- **THEN** the adapter rejects the rule-check result

#### Scenario: `to` accepts a single string for one-target rules
- **WHEN** a sub-rule emits `to: "AB12"` with `from: "AA00"`
- **THEN** the envelope is valid
- **AND** the viewer renders one dashed segment from `from` to `to`
  with `text` at its midpoint

#### Scenario: `to` accepts a non-empty list of strings for fan-target rules
- **WHEN** a sub-rule emits `to: ["AB12", "CD34", "EF56"]` with `from: "AA00"`
- **THEN** the envelope is valid
- **AND** the viewer renders three dashed segments — `from`→`AB12`,
  `from`→`CD34`, `from`→`EF56` — each using the shortest
  vertex-vs-edge path
- **AND** the sub-rule's `text` is rendered at the midpoint of the
  first segment (`from`→`AB12`) only

#### Scenario: Empty list `to: []` is rejected
- **WHEN** a sub-rule emits `to: []`
- **THEN** the adapter rejects the rule-check result
- **AND** the emitter is expected to send `to: null` to mean "no `to`"

#### Scenario: List `to` with a non-string element is rejected
- **WHEN** a sub-rule emits `to: ["AB12", 42]` (non-string entry)
- **THEN** the adapter rejects the rule-check result

#### Scenario: List `to` with an empty-string element is rejected
- **WHEN** a sub-rule emits `to: ["AB12", ""]`
- **THEN** the adapter rejects the rule-check result

#### Scenario: LID is a valid sub-rule part value
- **WHEN** an external rule emits a sub-rule with `part: "LID"`
- **THEN** the output validates against the RuleChecking schema and routes to the LID DXF's viewer

### Requirement: External rule function contract

SMDR2 SHALL delegate rule logic to a Python module contributed by
the external rule-checking team and checked into this repository.
The adapter `app/rule_check.py:check_rules(product_id: str,
bundle_dir: str | Path)` SHALL be the only call site for the
external team's entry point.

The adapter SHALL pass the external function:

1. The `product_id` string (opaque identifier; the external
   function MAY use it for logging but SHALL NOT rely on it for
   correctness).
2. A path to a materialised handoff bundle directory containing
   `manifest.json` plus `dxfs/<file_id>.dxf` and
   `match/<file_id>.json` per role-attached file. The directory
   contents SHALL conform to the layout that
   `app/drc_bundle.py:build_bundle` writes inside its zip — same
   manifest schema, same per-file unprefixed handles in every
   Match JSON.

The external function SHALL return RuleChecking JSON in the shape
defined by the "RuleChecking JSON output shape" requirement. The
adapter SHALL validate the envelope before returning to the caller
and SHALL raise on any of:

- A sub-rule that sets `from`, `to`, or `tol` without setting
  `file_id`.
- A sub-rule that has both `from` and `tol` null (nothing to
  highlight).
- A sub-rule that sets `to` (string or list) without `from`.
- A sub-rule whose `to` is an empty list `[]`, a list containing a
  non-string element, or a list containing an empty string.
- A sub-rule with a non-empty `rules` list missing `text`.

Validation failures SHALL be surfaced as exceptions that propagate
out of `check_rules` (the worker maps them to job-level errors via
the existing `error` field on `GET /api/jobs/{job_id}`). The
adapter SHALL NOT mutate, normalise, or pad the external function's
output — pass through verbatim once validation succeeds. In
particular, the adapter SHALL NOT auto-promote a scalar `to` to a
one-element list or auto-collapse a one-element list to a scalar:
the on-the-wire form is preserved.

The adapter SHALL NOT pre-merge per-role Match JSONs, apply
`<file_id[:8]>:` prefixes, or otherwise transform the bundle before
the external call. The bundle directory contract is the only
boundary; everything the external function needs lives inside it.

#### Scenario: Adapter forwards bundle path to external function
- **WHEN** `check_rules("p", "/tmp/bundle-p")` is called
- **AND** `/tmp/bundle-p` contains `manifest.json`, `dxfs/...`, and `match/...` per the bundle layout
- **THEN** the external rule function is invoked with that path
- **AND** the external function's return value is returned by `check_rules` verbatim (after envelope validation)

#### Scenario: Adapter rejects sub-rule missing file_id with handle
- **WHEN** the external function returns a sub-rule with `from: "AB12"` but `file_id: null`
- **THEN** `check_rules` raises an exception
- **AND** the worker maps the exception to a job-level `error`
- **AND** no `rule_check.json` is written

#### Scenario: Adapter rejects sub-rule with neither from nor tol
- **WHEN** the external function returns a sub-rule with `from: null`, `to: null`, `tol: null`
- **THEN** `check_rules` raises an exception
- **AND** the worker maps the exception to a job-level `error`

#### Scenario: Adapter rejects `to` without `from`
- **WHEN** the external function returns a sub-rule with `to: "AB12"` but `from: null`
- **THEN** `check_rules` raises an exception

#### Scenario: Adapter rejects list `to` without `from`
- **WHEN** the external function returns a sub-rule with `to: ["AB12", "CD34"]` but `from: null`
- **THEN** `check_rules` raises an exception

#### Scenario: Adapter rejects empty list `to`
- **WHEN** the external function returns a sub-rule with `to: []`
- **THEN** `check_rules` raises an exception
- **AND** the adapter does NOT silently normalise the empty list to
  `null`

#### Scenario: Adapter rejects list `to` with a non-string element
- **WHEN** the external function returns a sub-rule with
  `to: ["AB12", 42]`
- **THEN** `check_rules` raises an exception

#### Scenario: Adapter preserves scalar vs list form verbatim
- **WHEN** the external function returns a sub-rule with `to: "AB12"`
- **THEN** the persisted `rule_check.json` carries `to: "AB12"`
  (string), not `to: ["AB12"]` (list)
- **AND** when the external function returns `to: ["AB12"]`
  the persisted JSON carries the same one-element list verbatim

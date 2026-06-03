# design-rule-checking Specification

## Purpose
TBD - created by archiving change initial-build. Update Purpose after archive.
## Requirements
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

### Requirement: Rule check API and persistence

`POST /api/products/{product_id}/rule-check` SHALL validate that the
product is ready (every uploaded role-bearing file has `match_saved`
true and its persisted Match JSON exists on disk), submit a
background job to the existing worker pool, and return
**`202 Accepted`** with a JSON body containing the `job_id`. The
handler SHALL NOT load Match JSON files or invoke `check_rules`
itself; that work runs in a worker process so the FastAPI event loop
is never blocked by DRC.

The background worker SHALL materialise the product's DRC handoff
bundle on disk (the same layout `app/drc_bundle.py:build_bundle`
writes inside its zip — `manifest.json` at the bundle root plus
`dxfs/<file_id>.dxf` and `match/<file_id>.json` per role-attached
file), invoke `check_rules(product_id, bundle_dir)`, and persist
the result to `data/rule_check/{product_id}.json`. The worker SHALL
remove the temporary bundle directory after `check_rules` returns
(success or failure). The worker SHALL NOT pre-merge per-role Match
JSONs or apply any handle prefix — the bundle ships per-file,
unprefixed handles per the existing handoff-bundle requirement.

`GET /api/jobs/{job_id}` SHALL serve the job's status. While the job
is queued or running, the response SHALL contain `kind:
"rule_check"`, `status`, `submitted_at`, `started_at`, and a null
`completed_at`. Once the job completes successfully, the response
SHALL include `status: "done"`, `completed_at`, and a `result`
object with `saved_to`, `rule_count`, `pass_count`, `fail_count`,
and `roles_covered`. On worker failure, the response SHALL include
`status: "error"` and a human-readable `error` string.

`GET /api/products/{product_id}/rule-check` SHALL continue to
return the most recently persisted `rule_check.json` for the
product — independent of the job system.

`GET /api/products` and `GET /api/products/{product_id}` SHALL
include a `latest_rule_check_job` field per product. When no rule
check job has ever been submitted for that product within the
server's current lifetime, the field SHALL be `null`. Otherwise the
field SHALL contain `{ job_id, status, submitted_at,
completed_at, error, result }` mirroring the most recent
job's state, where `result` carries the same summary shape as the
job-status endpoint (`saved_to`, `rule_count`, `pass_count`,
`fail_count`, `roles_covered`) when `status` is `done`. This
allows a dashboard reloaded after the user has navigated away to
resume polling for an in-flight job, or surface the completed
result of a job that finished while they were elsewhere.

#### Scenario: Submit rule check after Save Match
- **WHEN** every uploaded role-bearing file for the product has
  `match_saved` true
- **AND** the client invokes `POST /api/products/{product_id}/rule-check`
- **THEN** the response status is `202 Accepted`
- **AND** the response body contains a `job_id`
- **AND** the response is returned before `check_rules` runs

#### Scenario: Poll a running rule check job
- **WHEN** the client calls `GET /api/jobs/{job_id}` for a rule
  check job that has been submitted but not yet finished
- **THEN** the response contains `kind: "rule_check"`
- **AND** `status` is either `"queued"` or `"running"`
- **AND** `completed_at` is null
- **AND** no `result` field is present

#### Scenario: Poll a finished rule check job
- **WHEN** the worker finishes successfully
- **AND** the client calls `GET /api/jobs/{job_id}`
- **THEN** the response contains `status: "done"`
- **AND** `result.saved_to` references the written
  `data/rule_check/{product_id}.json`
- **AND** `result.rule_count`, `result.pass_count`,
  `result.fail_count` describe the run
- **AND** `result.roles_covered` lists the roles the run consumed
- **AND** the persisted `rule_check.json` exists on disk and is
  readable via `GET /api/products/{product_id}/rule-check`

#### Scenario: Rule check before Save Match fails clearly
- **WHEN** the user invokes rule check on a product where at
  least one role-bearing file has not had Save Match performed
- **THEN** the API returns `400` with a message listing the roles
  still missing Save Match
- **AND** no job is created

#### Scenario: Worker error surfaces via job status
- **WHEN** the rule check worker raises an exception (e.g., the
  external rule function raises, or bundle materialisation fails)
- **THEN** the job record transitions to `status: "error"` with a
  human-readable `error` string
- **AND** `GET /api/jobs/{job_id}` returns that error message
- **AND** the persisted `rule_check.json` is not overwritten

#### Scenario: Bundle directory is removed after the job ends
- **WHEN** `check_rules` returns or raises in the worker
- **THEN** the temporary bundle directory the worker materialised
  is removed before the job transitions out of `running`

#### Scenario: Event loop stays responsive during long DRC
- **WHEN** a rule check job is running on the worker pool
- **AND** another client issues a concurrent request to any
  unrelated endpoint (for example, dashboard polling or viewer
  highlight lookup)
- **THEN** that unrelated request is served without waiting for
  `check_rules` to finish

#### Scenario: Dashboard reload picks up an in-flight job
- **WHEN** the user submits a rule check job and then reloads
  the dashboard (or navigates away and back) before the job
  finishes
- **AND** the dashboard fetches `GET /api/products`
- **THEN** the response includes `latest_rule_check_job` for that
  product with the live `status` (`queued` or `running`) and
  `job_id`
- **AND** the dashboard can resume polling `GET /api/jobs/{job_id}`
  without any local browser state having survived the reload

#### Scenario: Result of a completed job is recoverable after navigation
- **WHEN** the user submits a rule check job, navigates away, and
  returns after the worker has completed
- **AND** the dashboard fetches `GET /api/products`
- **THEN** the response includes `latest_rule_check_job` with
  `status: "done"`, `completed_at`, and a non-null `result`
  summary
- **AND** the dashboard can render the completion without polling
  the job-status endpoint again

### Requirement: External DRC handoff bundle format

SMDR2 SHALL package every product's DRC inputs into a self-describing
**handoff bundle** the external rule-checking team consumes — a
directory (or zip) containing one `manifest.json` at the root plus
the DXF and Match JSON files referenced from it. (Production rule
checking is performed by a separate team; this requirement defines
the contract at that boundary.)

`manifest.json` SHALL conform to
`openspec/specs/design-rule-checking/drc-manifest.schema.json`
(JSON Schema draft 2020-12). The top-level object SHALL carry:

| Field | Type | Required | Meaning |
|---|---|---|---|
| `bundle_version` | semver string | yes | Manifest contract version. Consumers MUST refuse a major version they do not understand. Current value: `"1.4.0"` (minor bumped from `1.3.0` when the per-file `view` array was added; `1.3.0` added `user_unit` / `original_unit`; `1.2.0` added `customer` / `customer_id`; each from `1.1.0`). |
| `product_id` | string | yes | SMDR2 internal product id, opaque to the consumer. |
| `product_name` | string | no | Human-readable name for cross-referencing reports. |
| `customer_id` | string | yes | SMDR2 internal `library_id` the product is bound to. Opaque to the consumer; stable across library renames. Mirrors how `product_id` is treated. |
| `customer` | string | no | Human-readable customer / library name. Omitted when the underlying library has no name; consumers that need a stable display name SHOULD key by `customer_id` and join against their own customer table. |
| `exported_at` | ISO 8601 string | no | Bundle generation time, second precision or finer. |
| `files` | array of `file_entry` | yes | Every (DXF, Match JSON) pair in the bundle. |

Every `file_entry` SHALL carry exactly these seven keys:

| Field | Type | Meaning |
|---|---|---|
| `role` | `"SBT"` \| `"BD"` \| `"POD"` \| `"RING"` \| `"LID"` | Functional role this DXF plays. The same role MAY appear in multiple entries — that is the multi-DXF case. All five roles are independent; a single product MAY carry entries under any subset of them, including both `"RING"` and `"LID"` simultaneously. |
| `file_id` | lowercase-hex string | SMDR2's content-hash-derived file identifier. The first 8 hex chars are the canonical short form used internally. |
| `dxf` | bundle-relative POSIX path | The DXF file. MUST resolve to a regular file inside the bundle. |
| `match_json` | bundle-relative POSIX path | The Match JSON for this DXF. Keys are `<class>.<index>` or `<view>.<class>.<index>` (see "RuleChecking JSON output shape" requirement above for `<view>` values). |
| `user_unit` | `"mm"` \| `"m"` \| `"inch"` \| `"cm"` \| `"um"` \| `"km"` \| `null` | The unit currently in force for the operator: the operator's unit-override if one is set, otherwise the effective unit derived from the applied auto-rescale factor. `null` only when no named unit applies (a unitless file the detector rescaled to a non-standard factor). `um` is ASCII (internal `μm` is translated); in practice `user_unit` never takes `km`. |
| `original_unit` | `"mm"` \| `"m"` \| `"inch"` \| `"cm"` \| `"um"` \| `"km"` \| `null` | The DXF's declared `$INSUNITS` header mapped to a unit string (`1`→`inch`, `4`→`mm`, `5`→`cm`, `6`→`m`, `7`→`km`, `13`→`um`). `null` when the header is unitless (`0`), an unsupported unit (e.g. `2` foot), or missing. Reports the header verbatim, independent of whether the rescaler acted on it. |
| `view` | array of `"top"` \| `"bottom"` \| `"side"` | The views the DXF carries, in canonical order top → bottom → side — one entry per side-region the operator has set (`top_view_rect` / `bottom_view_rect` / `side_view_rect`). `[]` when no side regions are set. Values correspond to the Match JSON key prefixes `top_view` / `bottom_view` / `side_view` (the `_view` suffix is dropped here). |

The unit fields SHALL draw their non-null values exclusively from the
vocabulary `{"mm", "m", "inch", "cm", "um", "km"}` — micrometre SHALL be
emitted as ASCII `"um"`, not the internal Unicode `"μm"`.

The `view` array SHALL list a view if and only if that view's side-region
rectangle is set on the file, ordered top → bottom → side regardless of the
order the operator painted them; it SHALL carry the view presence only, not the
rectangle geometry.

Each Match JSON in the bundle SHALL be the file's own per-DXF Match
JSON exactly as persisted at `data/match/{file_id}.json` — **not**
the merged role-bundle form produced internally by
`run_product_rule_check`. Handles SHALL NOT carry the
`<file_id[:8]>:` prefix that the internal merge applies; the external
team's per-file consumption keeps every DXF in its own coordinate
space without needing to know the prefix scheme.

Within-file view scoping (top/bottom/side) SHALL remain encoded in
the Match JSON key prefix; no separate side-region rect data is
required in the bundle.

The bundle builder SHALL resolve `customer_id` directly from the
product's `library_id` and SHALL look up the human-readable
`customer` name via the library registry at export time. If the
referenced library cannot be resolved (e.g., it was deleted
out-of-band between product creation and bundle export), the
builder SHALL raise a `ValueError` naming the offending library id
rather than emit a manifest with a missing or guessed customer.

#### Scenario: Single-DXF-per-role product (RING configuration)
- **WHEN** a product has exactly one DXF under each of `SBT`, `BD`, `POD`, `RING`
- **THEN** `manifest.files` has length 4
- **AND** each role appears in exactly one entry
- **AND** no entry carries `role: "LID"`
- **AND** every `dxf` and `match_json` path resolves to a file inside the bundle

#### Scenario: Single-DXF-per-role product (LID configuration)
- **WHEN** a product has exactly one DXF under each of `SBT`, `BD`, `POD`, `LID`
- **THEN** `manifest.files` has length 4
- **AND** exactly one entry carries `role: "LID"`
- **AND** no entry carries `role: "RING"`

#### Scenario: Product carries both RING and LID
- **WHEN** a product has one DXF under `SBT`, `BD`, `POD`, `RING`, and `LID`
- **THEN** `manifest.files` has length 5
- **AND** the manifest validates against `drc-manifest.schema.json`
- **AND** the bundle export SHALL NOT raise on the RING+LID combination

#### Scenario: Multi-DXF-per-role product
- **WHEN** a product has two DXFs under `BD` (e.g., top + bottom siblings) and one each under `SBT`, `POD`, `RING`
- **THEN** `manifest.files` has length 5
- **AND** exactly two entries carry `role: "BD"` with different `file_id` values
- **AND** each entry's `match_json` is the per-DXF Match JSON with raw, unprefixed handles

#### Scenario: Match JSON handles are not pre-merged
- **WHEN** a consumer reads any Match JSON referenced from a `file_entry`
- **THEN** every handle in every match group SHALL be a raw DXF handle
- **AND** no handle SHALL begin with `^[0-9a-f]{8}:` (the internal merge prefix)

#### Scenario: Major version mismatch is refused
- **WHEN** a consumer reads a manifest whose `bundle_version` major component does not match a major version it implements
- **THEN** the consumer SHALL refuse to process the bundle and SHALL surface a version-mismatch error to its operator

#### Scenario: Manifest carries customer_id and customer for a named library
- **WHEN** a product is bound to a library `lib-1` named `"ACME Corp"`
- **AND** the bundle is exported
- **THEN** `manifest.customer_id` equals `"lib-1"`
- **AND** `manifest.customer` equals `"ACME Corp"`

#### Scenario: customer is omitted when the library has no name
- **WHEN** a product is bound to a library whose `name` is an empty string or unset
- **AND** the bundle is exported
- **THEN** `manifest.customer_id` is present
- **AND** the `customer` key SHALL either be omitted entirely or set to an empty string — consumers that care about display names MUST tolerate both forms

#### Scenario: Missing library raises at export time
- **WHEN** a product references a `library_id` that no longer resolves through the library registry
- **AND** the export endpoint is invoked
- **THEN** the bundle builder SHALL raise (no manifest is written)
- **AND** the raised error SHALL name the unresolved `library_id`

#### Scenario: Every file_entry carries user_unit and original_unit
- **WHEN** a bundle is exported for any product
- **THEN** every `file_entry` carries both `user_unit` and `original_unit` keys
- **AND** the manifest validates against `drc-manifest.schema.json` with `bundle_version` `"1.4.0"`
- **AND** each non-null value is one of `{"mm", "m", "inch", "cm", "um", "km"}`

#### Scenario: user_unit reflects the operator override
- **WHEN** a file has `user_unit_override` set to `μm`
- **THEN** its `file_entry.user_unit` is `"um"` (ASCII, translated from the internal `μm`)

#### Scenario: user_unit falls back to the effective unit when no override
- **WHEN** a file has no `user_unit_override`
- **AND** its applied auto-rescale factor maps to a named unit (e.g. `25.4` → inch, or `1.0` → mm)
- **THEN** its `file_entry.user_unit` is that effective unit (e.g. `"inch"` or `"mm"`)

#### Scenario: original_unit is null for a unitless DXF
- **WHEN** a file's `$INSUNITS` header is `0` (unitless) or missing
- **THEN** its `file_entry.original_unit` is `null`

#### Scenario: original_unit reports km and micron headers
- **WHEN** a file's `$INSUNITS` header is `7` (km) or `13` (micron)
- **THEN** its `file_entry.original_unit` is `"km"` or `"um"` respectively

#### Scenario: file_entry.view lists all declared views in canonical order
- **WHEN** a DXF has its top, bottom, and side side-region rectangles all set
- **THEN** its `file_entry.view` is `["top", "bottom", "side"]`

#### Scenario: file_entry.view lists only the declared subset
- **WHEN** a DXF has only its top side-region rectangle set
- **THEN** its `file_entry.view` is `["top"]`

#### Scenario: file_entry.view is empty when no regions are set
- **WHEN** a DXF has none of its side-region rectangles set
- **THEN** its `file_entry.view` is `[]`

### Requirement: Rule panel hover and pinned highlight

In the viewer, the rule-check panel SHALL highlight a sub-rule's
entities on the canvas when the rule item is hovered (ephemeral) and
pin them when clicked (persistent until clicked again or another rule
is clicked). The pinned rule's card SHALL have a distinct visual
indicator (left border + tint). Closing the panel and re-running rule
check SHALL clear any pinned state.

The set of entities a hover or pin highlights SHALL be the union of
whichever of `from`, `to`, `tol` are present on the focused sub-rule.
When `from` + `to` are both present the viewer SHALL additionally
draw a dashed segment along the shortest path between the two
entities — computed by a vertex-vs-edge perpendicular-foot search so
the line lands on the geometrically closest pair of points across the
two primitives' edges, not their bbox centres. The `text` label
SHALL be rendered at the midpoint of that shortest segment when both
`from` and `to` are present, adjacent to `from` when only `from` is
present. When `tol_text` is present alongside `tol`, it SHALL be
rendered adjacent to the `tol` entity (independent of any from/to
label).

#### Scenario: Hover highlights then clears
- **WHEN** the user hovers a rule row
- **THEN** the rule's entities (`from`/`to`/`tol`, whichever are present) are highlighted in yellow on the canvas
- **WHEN** the cursor leaves the row
- **THEN** the yellow highlight clears

#### Scenario: Click pins the highlight and marks the card
- **WHEN** the user clicks a rule row
- **THEN** the rule's entities remain highlighted after the cursor leaves
- **AND** the card shows a yellow left-border and tinted background

#### Scenario: Click again unpins
- **WHEN** the user clicks the already-pinned rule
- **THEN** the highlight clears and the card returns to its default style

#### Scenario: from + to draws a connecting segment and centred label
- **WHEN** a focused sub-rule has both `from` and `to` set
- **THEN** the viewer draws a dashed segment along the shortest path between the two entities (vertex-vs-edge perpendicular-foot search)
- **AND** the sub-rule's `text` is rendered at the midpoint of that segment

#### Scenario: from only renders an adjacent label
- **WHEN** a focused sub-rule has `from` set but `to` is null
- **THEN** the viewer highlights `from` and does not draw a segment
- **AND** the sub-rule's `text` is rendered adjacent to `from`

#### Scenario: tol with tol_text renders an annotation-only highlight
- **WHEN** a focused sub-rule has `tol` set and `tol_text` set
- **THEN** the viewer highlights the `tol` entity
- **AND** `tol_text` is rendered adjacent to the `tol` entity
- **AND** the `tol` rendering is independent of any from/to rendering on the same sub-rule

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

#### Scenario: Adapter does not normalise external output
- **WHEN** the external function returns a valid result
- **THEN** `check_rules` returns the same object verbatim
- **AND** no keys are renamed, added, removed, or coerced


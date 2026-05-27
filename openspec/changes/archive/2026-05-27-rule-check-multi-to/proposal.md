## Why

The RuleChecking JSON sub-rule's `to` field is `str | null` today,
forcing the DRC team to emit one sub-rule per `(from, to_i)` pair
when a rule involves one source entity and a set of target
entities (e.g. "this BGA ball is too close to these three nearby
balls"). Operators see N near-identical rows for what's logically
one constraint. We can compress these to a single sub-rule by
letting `to` carry a list of handles, with the viewer fanning out
dashed segments from `from` to each `to_i`.

## What Changes

- The `to` field on sub-rules SHALL accept `str | list[str] | null`.
  - `null` → no `to` (as today).
  - `str` → single target handle (as today; **backward compatible**
    — old emitters need zero changes).
  - `list[str]` (non-empty) → multiple target handles; the viewer
    fans dashed segments from `from` to each element.
- **Empty list `to: []` is rejected** — the emitter SHALL send
  `null` instead. This keeps the "non-null `to` means something is
  rendered" invariant easy to reason about.
- `from` stays `str | null` (single source). The asymmetry is
  intentional: the use case is "one entity related to a group", not
  "many-to-many".
- The existing invariant "`to` MAY only be set when `from` is also
  set" extends to the list form: `to: ["AB12"]` with `from: null`
  is rejected exactly like `to: "AB12"` with `from: null`.
- **Viewer rendering for list `to`**: from each element of the
  list, the viewer draws a dashed segment from `from` to that
  `to_i`, picking the shortest vertex-vs-edge path per pair as
  today. The sub-rule's `text` SHALL render at the midpoint of the
  first segment in the list (avoids overlapping labels when there
  are many).
- **`shortestSegmentBetween(from, to)` and the focused sub-rule
  pipeline** in `app/static/canvas.js` SHALL normalise `to` to a
  list at the top of each render call, so the rest of the
  rendering path stays a single code path.
- **Dashboard `isLocatable` predicate** in
  `app/static/dashboard.js` SHALL treat a non-empty list `to` as
  locatable. `to: []` is treated as no-`to` (text-only if `from`
  and `tol` are also null), which is consistent with the rejected-
  empty-list policy: at the dashboard layer we're defensive against
  upstream bugs.
- The DRC integration contract (`INTEGRATION.md`) SHALL be updated
  to document the new shape, fan-render semantics, and the
  empty-list rejection.

No change to the rule-check **request** API, bundle manifest
schema, or persistence layer. The only schema change is on the
RuleChecking JSON output side.

## Capabilities

### New Capabilities
<!-- None. -->

### Modified Capabilities
- `design-rule-checking`: MODIFY the `RuleChecking JSON output
  shape` requirement to allow `to: list[str]`, document the
  empty-list rejection, and extend the viewer rendering rules
  (`from + to` segment becomes `from + to-list` fan). MODIFY the
  existing from-to scenario to include the list case and ADD
  scenarios for the list-rendering + empty-list-rejection paths.
  MODIFY the `External rule function contract` requirement so the
  adapter validation accepts list-form `to`, rejects empty lists,
  and still rejects `to: [...]` with `from: null`.

## Impact

- **Code**:
  - `app/rule_check.py::_validate_sub_rule` — split the `to`
    validation off `_typed_handle` (which still treats `from` /
    `tol` as `str | None`) into a dedicated path that accepts
    `str | list[str] | None`, rejects empty lists, and rejects
    non-string elements.
  - `app/static/canvas.js::drawFocusedSubRule` +
    `drawFocusedLabel` — normalise `to` to a list at the top of
    each function; loop the segment-drawing branch over the list;
    render the label at the first segment's midpoint.
  - `app/static/dashboard.js::isLocatable` — extend the predicate
    to treat a non-empty list `to` as locatable.
- **Specs**:
  - `openspec/specs/design-rule-checking/spec.md` — table row for
    `to`, invariants block, rendering bullets, scenarios.
  - `openspec/specs/design-rule-checking/INTEGRATION.md` — sibling
    doc; update the type column, the "Viewer 顯示語意" table, and
    the invariants list to mirror the spec.
- **APIs / persistence**: no change.
- **Tests**: extend `tests/test_rule_check.py` (envelope validation
  test suite) — add scenarios for list-form `to`, empty-list
  rejection, list-with-non-string-element rejection, and
  `to: [...] + from: null` rejection. Frontend changes are
  exercised manually as today.
- **Backward compatibility**: emitters that today emit `to: "X"`
  see no behaviour change. Empty list `to: []` is the only new
  "rejection" surface, and there's no legitimate reason to emit it
  — the emitter SHALL send `null` instead.
- **Operator-visible**: rule reports become more compact for
  fan-of-targets constraints; the viewer paints all targets at
  once with the same focus colour and pass/fail state.

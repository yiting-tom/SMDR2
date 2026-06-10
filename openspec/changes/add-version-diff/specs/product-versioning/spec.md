# product-versioning Specification (delta)

## ADDED Requirements

### Requirement: Version diff

`GET /api/products/{pid}/version-diff?from={vid_a}&to={vid_b}` SHALL
compare two versions of the same product and return:

- `templates.added` / `templates.removed` — template entries present in
  `to` but not `from` (and vice versa), matched per class by the
  canonical `template_signature` (translation/entity-order/vertex-order
  invariant), NOT by row id — a cloned row with identical geometry is
  the "same" template. Entries carry enough geometry
  (`entity_point_sets`, bbox, counts) for thumbnail rendering.
- `configs` — classes whose `(match_strategy, bbox_ratio)` differ
  between the two versions (including classes present in only one).
- `bindings` — per role: file ids added / removed, and for file ids
  bound in both versions, the per-version state fields that differ
  (`selected_layers`, view rects, `user_unit_override`,
  `chosen_layout`, `dxf_view`).
- `summary` — counts of the above.

Both versions MUST belong to `{pid}` (mismatch → 400; unknown ids →
404). The endpoint is a pure read: it SHALL work regardless of either
version's sign-off state and SHALL NOT modify anything.

#### Scenario: Template added in the newer version
- **WHEN** `v2` was cloned from `v1` and one new template was committed in `v2`
- **AND** the client requests the diff from `v1` to `v2`
- **THEN** `templates.added` has exactly that one entry
- **AND** `templates.removed` is empty (clone copies are signature-equal, so
  carried-over templates do not appear)

#### Scenario: Config change is reported per class
- **WHEN** `v2` changed `SMD-2T` from chamfer to signature/0.4
- **THEN** the diff's `configs` contains one entry for `SMD-2T` with
  both sides' strategy values

#### Scenario: Replaced role file appears as binding change
- **WHEN** `v2` carried `v1`'s SBT file but replaced the POD file
- **THEN** `bindings` reports the old POD file id as removed and the
  new one as added, and SBT does not appear (no change)

#### Scenario: Cross-product comparison is rejected
- **WHEN** `from` references a version of another product
- **THEN** the response is HTTP 400

#### Scenario: Signed-off versions are comparable
- **WHEN** both versions are signed off
- **THEN** the diff returns HTTP 200

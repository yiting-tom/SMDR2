## ADDED Requirements

### Requirement: Contained-match suppression rule

The system SHALL provide `suppress_contained_matches(out)` which removes any match instance whose consumed DXF-handle set is a **proper subset** of another same-class instance's handle set, retaining the instance with more handles (the superset). The function SHALL take and return the prefixed Match-JSON dict shape `{"<view>.<class_snake>.<idx>": [[handle, ...], ...]}` and SHALL drop keys that become empty.

When two same-class instances have **identical** handle sets, the function SHALL collapse them to exactly one retained instance; the tie-break SHALL prefer more handles (a tie for equal sets) and then the earliest template index. Suppression SHALL be evaluated against the full post-deduplication instance set (not iteratively), so a containment chain `A ⊊ B ⊊ C` drops both `A` and `B` and keeps `C`.

#### Scenario: Partial match contained by fuller match is dropped
- **WHEN** class `SMD-2T` has a mask-only instance with handles `{m1, m2}` and a mask+body instance with handles `{m1, m2, body}` at the same location
- **THEN** the `{m1, m2}` instance is removed
- **AND** the `{m1, m2, body}` instance is retained

#### Scenario: Mask-only location with no body survives
- **WHEN** a location has only the two masks `{m3, m4}` (no body present, so the mask+body template did not fire there)
- **THEN** the `{m3, m4}` instance is retained

#### Scenario: Disjoint instances are both kept
- **WHEN** two same-class instances have disjoint handle sets `{a, b}` and `{c, d}`
- **THEN** both instances are retained

#### Scenario: Partial overlap with no containment keeps both
- **WHEN** two same-class instances have handle sets `{a, b, c}` and `{c, d, e}` (intersecting but neither a subset of the other)
- **THEN** both instances are retained

#### Scenario: Identical handle sets collapse to one
- **WHEN** two instances of the same class under different template indices have the identical handle set `{a, b}`
- **THEN** exactly one instance with `{a, b}` remains in the output
- **AND** it is the instance under the earliest template index

#### Scenario: Containment chain drops all but the largest
- **WHEN** three same-class instances have handle sets `{a}`, `{a, b}`, and `{a, b, c}`
- **THEN** only the `{a, b, c}` instance is retained

### Requirement: Same-class scope across view prefixes

Suppression SHALL pool match instances by class only — across every view prefix (`top_view.<class>.*`, `bottom_view.<class>.*`, `side_view.<class>.*`, and unprefixed `<class>.*`) — and SHALL NOT compare or suppress instances belonging to different classes. When an instance is dropped, the surviving superset instance SHALL keep its own original key (and therefore its view prefix) unchanged.

#### Scenario: Same class, different view prefixes, still suppressed
- **WHEN** a class's mask-only instance `{m1, m2}` is keyed under `top_view.smd_2t.0` and its mask+body superset `{m1, m2, body}` is keyed under `bottom_view.smd_2t.1`
- **THEN** the `top_view.smd_2t.0` instance is removed
- **AND** the `bottom_view.smd_2t.1` instance is retained under its original key

#### Scenario: Different classes are never suppressed against each other
- **WHEN** a `FiducialCircle` instance has handles `{c1}` and a different class's instance has handles `{c1, r1, r2}` that contain `c1`
- **THEN** the `FiducialCircle` instance is retained
- **AND** no cross-class instance is removed

### Requirement: Integration with persisted Match JSON serialisation

The persisted Match JSON builder (`_save_match_worker`) SHALL apply `suppress_contained_matches` to `out` after the per-class matching loop and the view split (`split_matches_by_side`) have completed, and before the dict is written to `data/match/{file_id}.json`. The worker response SHALL retain `total_matches` as the raw matches found (unchanged semantics), SHALL recompute the `top_view`/`bottom_view`/`side_view`/`unassigned` parts of `side_counts` from the surviving instances while retaining the `dropped` count from the view-split phase, and SHALL add a `suppressed_count` field reporting the number of instances removed. The reported counts SHALL satisfy the invariant `total_matches == (top_view + bottom_view + side_view + unassigned) + dropped + suppressed_count`.

#### Scenario: Persisted Match JSON does not double-count a subsumed feature
- **WHEN** a file is saved whose library has both a mask-only and a mask+body `SMD-2T` template and whose DXF has a mask+body SMD
- **THEN** the written `match/{file_id}.json` contains the mask+body handle-list once
- **AND** does not contain the mask-only handle-list for that location

#### Scenario: Response counts reflect suppression
- **WHEN** suppression removes N instances during a save-match build
- **THEN** the response `suppressed_count` equals N
- **AND** the `top_view`/`bottom_view`/`side_view`/`unassigned` counts sum to the number of instances written to the file
- **AND** `total_matches` (raw matches found) equals that written-instance count plus `side_counts["dropped"]` plus `suppressed_count`

### Requirement: Preview paths remain invariant under suppression

The scan-all and prematch preview responses SHALL be byte-identical whether or not suppression runs, because both collapse matches to per-class handle **unions** that contained-match suppression leaves unchanged. `scan_all` (`app/main.py`) and `_preprocess_worker` (`app/jobs.py`) SHALL therefore require no code change for this capability.

#### Scenario: scan-all by_class union is unchanged by suppression
- **WHEN** the same file is scanned with a library that produces a mask-only instance contained by a mask+body instance of the same class
- **THEN** the `by_class` handle set for that class includes every handle of the surviving superset instance
- **AND** the `by_class` result is identical to the result computed without suppression

### Requirement: Default-on with a module-level toggle

Contained-match suppression SHALL be enabled by default via a module-level `CONTAINED_SUPPRESSION_ENABLED` flag that `suppress_contained_matches` reads live on each call (a bare global reference, so an in-process attribute set takes effect on the next call). When the flag is `False`, the function SHALL return `out` unchanged. The flag is a source-level constant and is NOT registered in the developer-override store; disabling it in a running deployment therefore requires a code change and restart (or an in-process attribute set), not a dev-panel toggle.

#### Scenario: Disabled flag is a pass-through
- **WHEN** `CONTAINED_SUPPRESSION_ENABLED` is set to `False` (in-process attribute set)
- **THEN** `suppress_contained_matches(out)` returns `out` with every instance preserved

#### Scenario: Enabled by default
- **WHEN** the module is imported with no override applied
- **THEN** `CONTAINED_SUPPRESSION_ENABLED` is `True`

### Requirement: Deterministic suppression

Suppression SHALL be deterministic: repeated runs over the same `out` SHALL produce byte-identical output and an identical `suppressed_count`, independent of dict iteration order.

#### Scenario: Repeated runs are identical
- **WHEN** `suppress_contained_matches` is run twice over the same input `out`
- **THEN** the two returned dicts are equal
- **AND** the same set of instances is removed in both runs

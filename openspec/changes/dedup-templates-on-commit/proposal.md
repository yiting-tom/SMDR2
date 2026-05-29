## Why

Operators repeatedly frame-select the same template instance — either by accident (double-Enter) or as a workflow habit (re-framing the same shape elsewhere to confirm "yes, that's the same part"). Today every commit unconditionally inserts a new row into the library, so the same canonical shape can end up duplicated N times under the same class. Each duplicate makes scan-all iterate redundantly across all of them (no extra matches, just wall-clock cost) and bloats the on-disk library with rows that are bit-identical up to translation.

A `template_signature()` over the entity point sets — translation-invariant, rotation/scale/reflection-distinct, bucketed at 10⁻⁴ mm to absorb FP noise — gives us a cheap canonical key. On commit we look up the key inside the same `(library_id, class_name)` scope; on hit we short-circuit and return the existing row's id so the operator's "Save Match" follow-up still references something real, but no new row lands and the scan-all overlay does not churn.

## What Changes

- Add `template_signature(entity_point_sets) -> tuple` pure function in `app/library.py`, with a module-level `TEMPLATE_DEDUP_BUCKET = 10**4` constant so a future precision change is one line.
- **BREAKING** (internal API only): `Library.add_template(template) -> tuple[Template, bool]` instead of returning `None`. Second element is `already_existed`. There are exactly two real call sites (`main.py::commit` and tests), both updated.
- `POST /api/.../commit` response payload gains an `already_existed: bool` field. When true, the response's `template_id` is the EXISTING row's id (not the discarded fresh one).
- Viewer status bar in `app/static/canvas.js::commitCurrentTemplate` branches: on `already_existed=true` it reads `template already in library (#${count})`, and the scan-all auto-refresh path (Wave 5) is skipped because re-merging an already-present handle set into the overlay is a no-op-with-render.
- Startup duplicate detection: `Library` (or `LibraryStore.load_library`) groups loaded rows by `(class_name, template_signature)`; each group with count > 1 emits one WARNING log line naming the library, class, and count. Pre-dedup rows stay in place; the invariant applies to new commits only.
- New test file `tests/test_library_dedup.py` covering signature invariances (translation / entity-order / vertex-order all collapse), distinctness (rotation / above-bucket drift / sub-bucket drift), dedup branches (same class / cross-class / cross-library), commit endpoint round-trip via TestClient, and the startup WARNING via caplog.

## Capabilities

### New Capabilities

(none — this is a refinement of an existing capability)

### Modified Capabilities

- `template-library`: Adds the "templates are deduplicated on commit by canonical signature within `(library, class)` scope" requirement, and the API contract that commit responses now carry `already_existed`.

## Impact

- **Code**: `app/library.py` (signature function + add_template return-shape + startup check), `app/main.py::commit` (tuple unpack + payload field), `app/static/canvas.js::commitCurrentTemplate` (status branch + suppressed auto-refresh).
- **API**: `POST /api/libraries/{lib_id}/classes/{cls}/templates` response gains `already_existed: bool`; existing clients ignore unknown fields, so non-breaking on the wire.
- **Tests**: New `tests/test_library_dedup.py`; one pre-existing test (`test_all_templates_returns_indexed_tuples`) is updated to use distinct geometry for the second template since dedup would otherwise collapse them.
- **Dependencies**: None.
- **Data**: Existing libraries are NOT migrated; the WARNING log surfaces pre-existing dupes so the operator can clean them up manually if they want.
- **Performance**: One extra dict lookup per commit (signature → existing). Per-template signature is memoized (`_signature` attribute, computed on first read) so the in-class scan is O(N) hash comparisons.

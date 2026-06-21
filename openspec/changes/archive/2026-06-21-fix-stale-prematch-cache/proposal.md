## Why

The auto-shown pre-match overlay on viewer load reads a cached snapshot
(`prematch_key(version_id, file_id)`) computed **once** at preprocess time. It
is never invalidated when the library changes, and `GET /api/files/{id}/prematch`
only reads it. So when the library grows from **other** files of the version (the
operator commits templates while viewing a different drawing, or another file's
Save Match adds classes), the overlay shown on arrival is stale and silently
**under-shows** — the operator must cancel it and trigger a manual Scan All
(which runs live against the current library) to see everything.

`save-match-refreshes-prematch` already keeps the snapshot fresh on the Save
Match path, but its proposal explicitly defers the broader case: *"a file whose
library grew from other drawings, where the operator never re-runs Save Match,
still shows a stale overlay until they do … a follow-up could add
snapshot-staleness detection with a load-time fall-through to a live scan."*
This change is that follow-up.

Two compounding defects make it worse than "merely stale":
- The snapshot carries no library version, so nothing can tell it is stale.
- `loadPrematch()` silently `return`s on a missing/empty snapshot, so a not-yet-
  written or stale-empty cache renders **nothing** with no signal to the user —
  it looks like "scan ran, found little."

## What Changes

- **(A — root fix) The library gains a monotonic `revision`** bumped on every
  mutation that can change matching results: `insert_template`,
  `delete_template`, `update_template_class`, `update_class_strategy`. The
  preprocess worker stamps the library's current `revision` into the pre-match
  snapshot. `GET /api/files/{id}/prematch` reads the snapshot's stamped revision,
  compares it to the library's current revision, and returns `stale: true` when
  they differ (or the snapshot is missing) — the endpoint already reserves a
  `stale` field.
- **(B — stop-gap) `loadPrematch()` self-heals instead of silently bailing.**
  When the snapshot is missing, `stale: true`, or `total == 0`, the viewer runs
  the live Scan All once so the auto overlay is complete on arrival. When the
  snapshot is fresh, it is used as-is (instant, no live scan). B depends on A to
  detect the *populated-but-stale* case; on its own it can only react to
  missing/empty.

Net effect: fresh cache → instant overlay (0 s); changed library or unfinished
worker → one live scan, complete results; never again a silent incomplete
overlay.

Out of scope (unchanged by this change): the snapshot stays not-side-aware and
constraint-free at preprocess time (no side rects exist yet); over-counting of
same-radius classes in the *preview before side rects are drawn* is inherent and
resolves once the operator draws side regions / the self-heal falls through to
the live scan. Save Match's own refresh (`save-match-refreshes-prematch`) is
untouched.

## Capabilities

### Modified Capabilities

- `template-library`: ADDS a library-level monotonic `revision` bumped on every
  template/class write path, exposed so other capabilities can detect "the
  library changed since X".
- `dxf-pipeline`: the preprocess pre-match snapshot is stamped with the library
  `revision` it was computed against, and `GET /api/files/{id}/prematch` returns
  `stale: true` when the stamped revision no longer matches the library's current
  revision (or the snapshot is absent).
- `viewer-ui`: the auto-shown pre-match on viewer load self-heals — a missing,
  `stale`, or empty snapshot triggers a single live Scan All instead of silently
  rendering nothing; a fresh snapshot is used directly with no live scan.

## Impact

- **Code**:
  - `app/library.py` — `revision` storage (column or derived signal) + bump in
    `insert_template`, `delete_template`, `update_template_class`,
    `update_class_strategy`; a `current_revision(library_id)` accessor.
  - `app/jobs.py` — preprocess worker stamps `revision` into the prematch blob
    body.
  - `app/main.py` — `prematch()` compares stamped vs current revision, sets
    `stale`.
  - `app/static/canvas.js` — `loadPrematch()` falls through to `runScanAll()` on
    missing/stale/empty.
- **Schema**: one additive column (a `revision` integer on `libraries`, or a
  bump-counter table) — additive, no destructive migration. (A zero-migration
  variant using `MAX(templates.created_at)` is possible but misses
  deletes/class-config/move; rejected in design in favour of an explicit bump.)
- **API**: `GET /api/files/{id}/prematch` response gains a meaningful `stale`
  flag (field already present); no new endpoints.
- **Tests**: prematch endpoint returns `stale:true` after a post-preprocess
  library mutation; `loadPrematch` self-heal path; revision bumps on each write
  path.
- **Relationship**: builds on `save-match-refreshes-prematch` (keep that change's
  behaviour); this closes its documented coverage gap.

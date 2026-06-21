## Context

The auto-shown pre-match overlay reads a cached snapshot written once by the
preprocess worker (`app/jobs.py`) at `prematch_key(version_id, file_id)` and read
by `prematch()` in `app/main.py`. The snapshot encodes only `(version_id,
file_id)` — no library version — so it is never invalidated when templates are
added/removed/reclassed after preprocess. The live Scan All endpoint
(`app/main.py` `scan_all()`) sidesteps the cache entirely with a fresh
`load_library(...)`, which is why manual Scan All is always complete while the
auto overlay is stale.

`save-match-refreshes-prematch` (implemented, not yet archived) rewrites the
snapshot on the Save Match path. It does **not** cover the library growing from
other files when the operator never re-saves — its proposal names that gap and
the "load-time fall-through to a live scan" as the follow-up. This change is that
follow-up.

Today the `libraries` table is `(id, name, created_at)`; templates carry a
per-row `created_at` stamped in `Store.insert_template`. There is no
library-level version, revision, or content hash. The `prematch()` endpoint
already returns a `stale` field (currently only `True` on a missing blob).

## Goals / Non-Goals

**Goals:**
- Make a populated-but-outdated snapshot **detectable** (not just missing/empty).
- Make the viewer's auto overlay **complete on arrival** whenever the snapshot is
  stale/missing/empty, without paying a live scan when the snapshot is fresh.
- Cover every library mutation that can change matching results
  (add/delete/reclass/strategy-change), not just template adds.

**Non-Goals:**
- Making the preprocess snapshot side-aware / constraint-correct. It stays the
  raw not-side-aware `{by_class, total}` union (side rects don't exist at
  preprocess); same-radius over-count in the pre-side-rect preview is inherent
  and out of scope.
- Changing the Save Match refresh path (`save-match-refreshes-prematch` stays).
- Eliminating the live scan cost when the library genuinely changed — that scan
  is the correctness fall-through, paid only when stale.

## Decisions

### D1 — Explicit monotonic `revision` on the library, not `MAX(created_at)`

Add `revision INTEGER NOT NULL DEFAULT 0` to `libraries`. A private
`Store._bump_revision(library_id)` does `UPDATE libraries SET revision =
revision + 1 WHERE id = ?` and is called inside the same write as every
result-affecting mutation: `insert_template`, `delete_template`,
`update_template_class`, `update_class_strategy`. A `current_revision(library_id)
-> int` accessor does a single indexed row read.

*Alternatives considered:*
- **`MAX(templates.created_at)`** (zero migration): rejected — moves on inserts
  only; misses deletes, class moves, and per-class strategy changes, all of which
  change scan results. It would fix the reported symptom (missing templates) but
  silently leave the other three stale.
- **Content hash of the library** (templates + class config): correct but
  expensive to compute on every read and every preprocess; an integer counter is
  sufficient because we only need *inequality*, never ordering or content.

Only inequality matters, so the absolute value and even occasional double-bumps
are harmless; concurrent bumps still differ from any older stamp.

### D2 — Stamp the revision in the blob **body**, keep the key unchanged

The preprocess worker writes `library_revision: <current_revision>` into the
snapshot JSON alongside `by_class`/`total`. The blob key stays
`prematch/{version_id}/{file_id}.json` — no key churn, no orphaned blobs.
`prematch()` reads `body.library_revision` and compares it to
`current_revision(version.library_id)`; mismatch or absent stamp ⇒ `stale: true`.
A snapshot written before this change has no stamp and is treated as stale (one
self-heal scan, then it self-corrects on the next preprocess/Save Match write).

### D3 — Self-heal lives in `loadPrematch()`, and does NOT rewrite the blob

When `prematch()` returns `stale: true`, a missing snapshot, or `total == 0`,
`loadPrematch()` calls the existing `runScanAll()` once (the live, side-aware,
constraint-correct path) instead of silently returning. A fresh snapshot is
rendered directly with no live scan.

The self-heal deliberately does **not** write the live result back into the blob:
the live scan is side-aware and view-constrained, whereas the blob contract is
the raw not-side-aware union (`dxf-pipeline`). Persisting the constrained result
would corrupt that contract. Snapshot writes remain the preprocess worker's and
Save Match's responsibility. Convergence comes from those writers stamping the
current revision; the load-time scan is purely a display fall-through.

Self-heal only fires once per load and only when the file is past
`awaiting_layers` (so the live scan-all has parsed data); otherwise it no-ops,
matching today's behaviour for not-ready files.

## Risks / Trade-offs

- **A mutation path forgets to bump** → that path's change won't invalidate the
  snapshot, reintroducing staleness for it. → Mitigation: single
  `_bump_revision` helper, one call site per write method, one unit test per
  path asserting `current_revision` increased.
- **Live-scan cost on load after a big library change** (~seconds for many
  templates) → only on user-initiated viewer load, only when actually stale, and
  only once. Fresh snapshots stay instant. Acceptable and strictly better than
  today's silent-incomplete.
- **Schema migration on the live DB** (SQLite today, MariaDB per the infra
  plan) → additive `DEFAULT 0` column, safe on both; no backfill needed
  (absent-stamp blobs already treated as stale).
- **`version_id` (blob key) vs `library_id` (revision owner)** → `prematch()`
  already loads the file/version, so it resolves `version.library_id` to read the
  current revision; no extra lookup table.

## Migration Plan

1. Add `revision` column (additive, default 0). Existing rows start at 0.
2. Deploy backend: writers bump; preprocess stamps; `prematch()` compares.
   Pre-existing un-stamped snapshots read as stale → self-heal covers them until
   the next preprocess/Save Match rewrites with a stamp.
3. Deploy frontend `loadPrematch()` self-heal. Backward compatible: against an
   old backend that never sets `stale`, behaviour is unchanged except the
   `total == 0` fall-through (a strict improvement).
4. Rollback: revert frontend and/or backend independently; the unused `revision`
   column is inert.

## Open Questions

- Should a future change make the preprocess snapshot itself revision-cheap to
  rebuild (so the fall-through scan can be skipped more often)? Out of scope here;
  the load-time scan is correct and bounded.

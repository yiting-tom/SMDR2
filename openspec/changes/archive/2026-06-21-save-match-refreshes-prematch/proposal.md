## Why

The auto-shown pre-match overlay on viewer load reads
`data/prematch/{file_id}.json` — a snapshot computed **once** by
`_preprocess_worker` at preprocess time and never rewritten afterward
(`GET /api/files/{id}/prematch` only reads the file). Committing templates or
saving a match does not refresh it.

So on a file that already has saved matches, the auto overlay is stale: it
was frozen when the library had fewer (or zero) templates, and silently
under-shows. The operator has to cancel the overlay and trigger a manual
Scan All — which runs live against the current library (`GET /scan-all`) — to
see everything.

Tellingly, the **Match JSON is always complete and correct**, because
`_save_match_worker` does a fresh `Store.load_library(...)` and a live scan on
every Save Match (this exact fresh-load pattern was added to fix an earlier
stale-cache bug — see the comment at the top of `_save_match_worker`). The
pre-match snapshot is the one artifact that never got the same "stay fresh"
treatment.

## What Changes

- **`_save_match_worker` (`app/jobs.py`) refreshes the pre-match snapshot** from
  the same live scan it already runs for the Match JSON. As it iterates
  templates, it accumulates a raw per-display-class handle **union** from each
  template's `result.matches` (taken **before** `split_matches_by_side` and
  `suppress_contained_matches`), then rewrites `data/prematch/{file_id}.json`
  with the same not-side-aware `{by_class: {display_name: [handle, ...]}, total}`
  contract `_preprocess_worker` writes.
- The refresh is **best-effort**: it runs after the Match JSON is persisted and
  is wrapped so a write failure logs a warning and leaves the previous snapshot
  in place — it never fails the Save Match job (worst case is the pre-existing
  stale behaviour, i.e. no regression).
- No frontend change: `loadPrematch()` already applies client-side view
  constraints to whatever snapshot it loads, so a refreshed (raw) snapshot
  renders exactly like a freshly-preprocessed one.

## Why the union is the right shape (no double-counting risk)

The `contained-match-suppression` capability already establishes that the
pre-match / scan-all per-class handle **union** is invariant to suppression
(suppression only drops instances whose handle set is contained in a retained
one). Building the snapshot from the raw pre-split matches therefore yields the
same handle set the displayed overlay needs, and keeps the snapshot
not-side-aware exactly as the `dxf-pipeline` contract specifies.

## Capabilities

### Modified Capabilities

- `dxf-pipeline`: ADDS a requirement that a completed Save Match rewrites the
  pre-match snapshot from its live scan, so templates committed after preprocess
  appear in the auto-shown overlay on the next viewer load.

## Impact

- **Code**: `app/jobs.py` (`_save_match_worker`: one accumulator in the existing
  per-template loop + one best-effort write block). No API, no schema, no
  frontend change.
- **Tests**: adds a unit test in `tests/test_match_json_constraints.py` driving
  `_save_match_worker` and asserting a stale/empty snapshot is refreshed to
  include a class whose template matched in the live scan.
- **Coverage gap this leaves**: a file whose library grew from **other**
  drawings, where the operator never re-runs Save Match, still shows a stale
  overlay until they do. That broader staleness (also true across a library
  switch without reprocess) is out of scope here — a follow-up could add
  snapshot-staleness detection with a load-time fall-through to a live scan.
- **Behaviour**: purely additive — the Match JSON output is unchanged; only the
  pre-match snapshot is now kept fresh by Save Match.

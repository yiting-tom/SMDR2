## 1. Frontend self-heal (stop-gap B — no schema change, ship first)

- [x] 1.1 `app/static/canvas.js` `loadPrematch()`: replace the silent early
  returns. When the fetch fails, the response is `stale: true`, or `total == 0`,
  and the file is past `awaiting_layers`, call `runScanAll()` once instead of
  returning; guard with a one-shot flag so it cannot loop or double-fire with the
  `A`-key / button path. A fresh, non-empty response renders the snapshot
  directly with no live scan (unchanged behaviour).
- [x] 1.2 Confirm `loadPrematch()` no longer leaves the overlay silently empty:
  on the self-heal path the Scan All button ends up `active` via `runScanAll()`.

## 2. Library revision counter (root fix A — `app/library.py`)

- [x] 2.1 Schema: add `revision INTEGER NOT NULL DEFAULT 0` to the `libraries`
  table in the SCHEMA DDL; ensure a fresh DB and the migration path both create
  it (additive, no backfill). *(SQLite: SCHEMA + idempotent ALTER in `_migrate`;
  MariaDB: alembic `0008_libraries_revision`.)*
- [x] 2.2 Add `Store._bump_revision(library_id)` doing
  `UPDATE libraries SET revision = revision + 1 WHERE id = ?` within the same
  write/transaction as the mutation.
- [x] 2.3 Call `_bump_revision` from `insert_template`, `delete_template`,
  `update_template_class`, and `update_class_strategy`. *(delete/reclass resolve
  the row's `library_id` first since those take only `template_id`.)*
- [x] 2.4 Add `Store.current_revision(library_id) -> int` (single-row read);
  expose it on `Library` if the worker/endpoint reach it via the `Library`
  wrapper.

## 3. Stamp + report staleness (A — `app/jobs.py`, `app/main.py`)

- [x] 3.1 `app/jobs.py` preprocess worker: read the library's `current_revision`
  at scan time and write it as `library_revision` into the prematch snapshot body
  alongside `by_class`/`total`.
- [x] 3.2 `app/main.py` `prematch()`: read the snapshot's `library_revision`,
  resolve the file's `library_id`, compare to `current_revision`. Set
  `stale: true` when absent, unstamped, or mismatched; `false` when equal. Do not
  recompute. Keep returning `{by_class:{}, total:0, stale:true}` for a missing
  blob.

## 4. Tests

- [x] 4.1 `app/library.py` revision: unit test that each of the four write paths
  (`insert_template`, `delete_template`, `update_template_class`,
  `update_class_strategy`) strictly increases `current_revision`, and a pure read
  does not. *(`tests/test_library.py::test_revision_bumps_on_every_result_affecting_write`
  + `test_current_revision_unknown_library_is_zero`.)*
- [x] 4.2 `prematch()` staleness: test fresh snapshot → `stale:false`; commit a
  template post-preprocess → same file's `prematch` now `stale:true`. *(`tests/
  test_layer_preview.py::test_prematch_reports_staleness_after_library_change`.
  Missing-blob → `stale:true,total:0` was already the endpoint's existing branch.)*
- [x] 4.3 Preprocess stamping: snapshot written by the worker carries
  `library_revision` equal to the library's revision at preprocess time. *(extended
  `tests/test_match_json_constraints.py::test_preprocess_prematch_clean_when_radii_differ`.)*
- [x] 4.4 Frontend self-heal: no JS test harness exists in the repo (no
  package.json / jest / vitest) → **manual-verify** at 5.1. The endpoint contract
  the self-heal keys off (`stale`) is covered by 4.2.
- [x] 4.5 Run the suite green: `pytest -q` → 740 passed, 11 skipped, 0 new
  failures. (`tests/test_blobstore.py::test_backend_selection` fails on the clean
  tree too — optional S3/`boto3` dep not installed locally; unrelated.)

## 5. Verify & archive

- [ ] 5.1 Manual: open a viewer file, add a template from another file of the
  version, return to the first file — auto overlay now reflects the new template
  without a manual Scan All; a file with an unchanged library still opens
  instantly with no live scan.
- [ ] 5.2 `openspec validate fix-stale-prematch-cache --strict`.
- [ ] 5.3 `/opsx:archive fix-stale-prematch-cache` after verification.

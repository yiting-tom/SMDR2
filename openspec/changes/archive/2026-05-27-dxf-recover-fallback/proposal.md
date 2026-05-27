## Why

Some DXFs that open cleanly in AutoCAD fail with an unrecoverable
error when uploaded to SMDR2. The pipeline today calls
`ezdxf.readfile` (strict mode) at `app/dxf.py:506`; AutoCAD silently
tolerates many real-world spec violations (truncated sections,
out-of-order group codes, malformed headers) that strict ezdxf
rejects with `DXFStructureError` / `DXFTagError`. The result is
operator-facing errors on files that should have been usable,
plus zero visibility in the server log about *why* a given file
failed.

## What Changes

- `flatten_for_render` (and any other call site that opens a user
  DXF) SHALL try `ezdxf.readfile` first. On the same exception
  classes that strict mode raises (`DXFStructureError`,
  `DXFTagError`, `IOError`, etc.) the parser SHALL fall back to
  `ezdxf.recover.readfile` and continue with the recovered `doc`.
- A successful recover path SHALL emit a `WARNING`-level server log
  carrying: the file id (or path), the strict-mode exception class
  and message, and an Auditor summary
  (`n_fixed`, `n_unrecoverable`, and the first few audit messages).
- The `FileRecord` SHALL gain a new field `dxf_recover_notes`
  (nullable JSON-serialisable dict) that captures the same recover
  summary persisted on the file row. Files that succeed via strict
  SHALL leave the field `null`.
- When **recover also fails**, the existing `error` lifecycle status
  SHALL be reached with an `ERROR`-level log including both the
  strict exception and the recover exception, and the
  `FileRecord.error` field SHALL contain the same combined detail.
- The dashboard per-file payload SHALL include `dxf_recover_notes`
  so the viewer / dashboard can surface a non-blocking pill
  (e.g. `ℹ recovered (N entities patched)`) for any file that took
  the fallback path. The actual UI rendering follows the existing
  `rescaled` / `unit_scale_warning` pill pattern.

No behavioural change for DXFs that already parse via strict — they
remain on the strict path and produce identical numeric output.

## Capabilities

### New Capabilities
<!-- None; this change reshapes existing capabilities. -->

### Modified Capabilities
- `dxf-pipeline`: ADD a requirement that the parser uses
  `ezdxf.readfile` first and falls back to `ezdxf.recover.readfile`,
  with the logging + `dxf_recover_notes` semantics described above.
  ADD a clarifying scenario to the existing `File lifecycle status`
  requirement so the `error` state includes the recover failure
  detail when both paths fail.
- `viewer-ui`: ADD a dashboard pill / per-file payload requirement
  modelled on the existing `rescaled` pill, surfacing the
  `dxf_recover_notes` summary so operators can see which files
  travelled the recover path.

## Impact

- **Code**:
  - `app/dxf.py:506` — wrap the `ezdxf.readfile` call in
    strict-first / recover-fallback. Returns both the `doc` and a
    `recover_notes: dict | None` for the caller to thread through.
  - `app/jobs.py::_preprocess_worker` — propagate the recover notes
    into the parsed-JSON output / FileRecord update so the field
    lands on the DB row.
  - `app/files.py` — add the `dxf_recover_notes` column (with a
    schema migration in the existing `if "<column>" not in cols`
    pattern) and surface it in `to_dict()` for the dashboard.
  - `app/static/dashboard.js` — render the pill from the new
    payload field. (Optional: viewer can do the same.)
- **APIs**: no endpoint signature changes. `GET /api/files` and
  `GET /api/files/{file_id}` payloads gain a new optional field
  `dxf_recover_notes`. Old clients ignoring it continue to work.
- **Dependencies**: `ezdxf.recover` is part of the existing ezdxf
  install — no new package.
- **Data migration**: the new column is added on startup via the
  existing `ALTER TABLE files ADD COLUMN …` idiom. Pre-existing
  rows have `NULL` and the dashboard treats `NULL` exactly like a
  clean strict parse — no backfill needed.
- **Tests**: a fixture DXF that strict rejects but recover saves is
  needed; if one isn't readily available the test can monkeypatch
  `ezdxf.readfile` to raise and `ezdxf.recover.readfile` to return
  a stub doc. Add lifecycle tests for the three outcomes
  (strict OK, recover OK, both fail).
- **Operator-visible**: server log gains `WARNING` lines for any
  file that took the recover path — useful for spotting recurrent
  DXF authoring quirks.

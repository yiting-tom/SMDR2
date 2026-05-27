## 1. Parser wrapper (`app/dxf.py`)

- [x] 1.1 Add a private helper `_open_dxf(dxf_path: str | Path, *, file_id: str | None = None) -> tuple[Document, dict | None]` near the top of `app/dxf.py` (above `flatten_for_render`). The helper SHALL try `ezdxf.readfile(str(dxf_path))` first. On exception classes `ezdxf.DXFStructureError`, `ezdxf.DXFTagError`, and any other subclass of `ezdxf.DXFError` raised by `readfile`, it SHALL call `ezdxf.recover.readfile(str(dxf_path))` and unpack the returned `(doc, auditor)`. Non-DXF exceptions (`FileNotFoundError`, `PermissionError`, OS errors) SHALL propagate untouched. On the recover-OK path the helper SHALL: (a) emit a `WARNING` log via `logger.warning(...)` carrying `file_id` (or path), `type(strict_exc).__name__`, the strict message, `len(auditor.fixes)`, `len(auditor.errors)`, and the first `min(5, len(auditor.fixes))` audit messages; (b) build and return a `recover_notes` dict shaped `{"strict_error": f"{type(strict_exc).__name__}: {strict_exc}", "n_fixed": int(len(auditor.fixes)), "n_unrecoverable": int(len(auditor.errors)), "audit_messages": [str(f) for f in list(auditor.fixes)[:5]]}`. On both-fail it SHALL raise a `RuntimeError` whose message is `f"strict: {type(strict_exc).__name__}: {strict_exc} | recover: {type(recover_exc).__name__}: {recover_exc}"` (the worker's exception handler upgrades this to ERROR-level logging via the existing pipeline).
- [x] 1.2 Replace the `doc = ezdxf.readfile(str(dxf_path))` line at `app/dxf.py:506` with `doc, recover_notes = _open_dxf(dxf_path, file_id=…)`. `flatten_for_render`'s signature gains an optional `file_id: str | None = None` parameter so the helper can thread it into the log line. Update `RenderOutput` (or the call site below) to carry `recover_notes` out to the caller — the simplest path is to add `recover_notes: dict | None = None` to the `RenderOutput` dataclass.

## 2. Worker plumbing (`app/jobs.py`)

- [x] 2.1 In `_preprocess_worker` (`app/jobs.py:63` area), pass the file's `file_id` to `flatten_for_render(...)`, capture `render.recover_notes`, and thread it into the parsed-JSON written to `data/parsed/{file_id}.json` (so the data survives a worker restart). Persist the same value onto the FileRecord via a new `FILE_STORE.set_dxf_recover_notes(file_id, notes)` call in the worker's success path (or via the existing `update_parsed` method — extend it to accept the new field). When `recover_notes is None` the worker SHALL clear the field (or leave it null on a fresh row).
- [x] 2.2 No change to the existing error-status handling: when `_open_dxf` raises (recover also failed), the worker's existing `try/except` already calls `FILE_STORE.update_status(file_id, ERROR, error=str(exc))`. Verify the resulting message contains both `strict:` and `recover:` segments end-to-end (covered by tests in §4).

## 3. Persistence (`app/files.py`)

- [x] 3.1 Add the column to the `files` schema: in the `__init__` migration block (around `app/files.py:299`), add `if "dxf_recover_notes" not in cols: self.conn.execute("ALTER TABLE files ADD COLUMN dxf_recover_notes TEXT")`. Store as TEXT containing JSON; null means strict-OK.
- [x] 3.2 Add `dxf_recover_notes: dict | None = None` to the `FileRecord` dataclass and include it in `_row_to_record` (decoding the TEXT column via `json.loads` when non-null) and in `to_dict()` (passing the decoded dict through). Also include it in the `INSERT INTO files (...)` column list with the appropriate default in `register(...)`.
- [x] 3.3 Add `FileStore.set_dxf_recover_notes(self, file_id: str, notes: dict | None) -> None` mirroring `set_match_saved`'s shape (`UPDATE files SET dxf_recover_notes = ? WHERE id = ?` with `json.dumps(notes) if notes is not None else None`).

## 4. Tests

- [x] 4.1 Add `tests/test_dxf_recover.py::test_open_dxf_strict_ok_returns_none_notes` that monkeypatches `ezdxf.readfile` to return a stub doc, calls `_open_dxf(path)`, and asserts the returned `recover_notes` is `None`. No WARNING log line is emitted (capture with `caplog`).
- [x] 4.2 `test_open_dxf_strict_fail_recover_ok_emits_warning_and_notes`: monkeypatch `ezdxf.readfile` to raise `ezdxf.DXFStructureError("invalid header")` and `ezdxf.recover.readfile` to return `(stub_doc, stub_auditor)` where the stub auditor reports `fixes = [<5 msgs>]` and `errors = [<1 entry>]`. Assert: `_open_dxf` returns the stub doc + a notes dict with the documented shape (`strict_error` containing `"DXFStructureError"`, `n_fixed == 5`, `n_unrecoverable == 1`, `audit_messages` length 5). One WARNING-level log line is emitted.
- [x] 4.3 `test_open_dxf_both_fail_raises_combined_runtimeerror`: monkeypatch strict to raise `DXFStructureError` and `recover.readfile` to raise `DXFStructureError("unrecoverable header")`. Assert `_open_dxf` raises `RuntimeError` whose message contains both `"strict: DXFStructureError"` and `"recover: DXFStructureError"`.
- [x] 4.4 `test_open_dxf_non_parser_exception_propagates`: monkeypatch strict to raise `FileNotFoundError`. Assert recover is NOT called (e.g. via a side-effect counter) and the `FileNotFoundError` propagates.
- [x] 4.5 `tests/test_files.py` (or wherever schema tests live): assert a freshly-registered `FileRecord` round-trips `dxf_recover_notes` through `register` → `get` → `to_dict()`. Verify the schema migration path: open a `FileStore` against a pre-existing DB that lacks the column and assert the migration succeeds (the existing test for prior column adds is a template).
- [x] 4.6 End-to-end worker test (extending an existing preprocess test): patch `flatten_for_render` to return a `RenderOutput` carrying a non-null `recover_notes`, run the worker, assert the resulting `FileRecord.dxf_recover_notes` matches and the file reaches `ready_to_match`.

## 5. Dashboard (`app/static/dashboard.js` + supporting HTML/CSS)

- [x] 5.1 In whichever helper builds the per-file slot cell, after the existing `rescaled` / `unit_scale_warning` pill block, render an additional pill when `file.dxf_recover_notes` is truthy. The pill text follows the spec: `ℹ recovered (Nfixed/Munrecoverable)`. Use the same CSS class as the existing neutral-informational pill so the visual style is consistent.
- [x] 5.2 Set the pill's `title` attribute to `file.dxf_recover_notes.strict_error` so hover shows the original parser error.
- [x] 5.3 Verify visual order: when a file has both `rescaled` and recover, render rescale first, then recover.

## 6. Manual verification

- [ ] 6.1 Upload a known-good DXF; confirm dashboard shows no recover pill and server log shows no WARNING.
- [ ] 6.2 Upload one of the operator's currently-failing DXFs (the ones AutoCAD opens but SMDR2 today errors on). Confirm: file reaches `ready_to_match`, dashboard shows the recover pill with counts, server log carries one WARNING line, hover on the pill shows the original strict exception.
- [ ] 6.3 Upload a truly broken file (e.g. a renamed `.txt` or a zero-byte `.dxf`). Confirm: file status becomes `error`, dashboard shows the existing error indicator (not the recover pill), and the server log carries an ERROR line containing both `strict:` and `recover:` segments.

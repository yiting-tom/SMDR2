## Why

Matching and DXF preprocessing tolerances are currently compiled constants in `app/matching.py` and `app/dxf.py`. Tuning them today means editing source, restarting the server, and re-uploading test files — every experiment costs minutes. The engineer needs to iterate on these values against real customer DXFs to chase down false negatives in the matcher and over/under-segmentation in the parser, so the loop has to collapse to seconds.

## What Changes

- Add a Dashboard developer-only panel (gear button next to the existing Developer Mode toggle, shown only when Dev Mode is ON) that opens a modal exposing the matching and DXF tunables for live editing.
- Add `GET /api/dev/settings` returning the currently applied overrides plus the compiled defaults; add `POST /api/dev/settings` to apply overrides (or reset to defaults) by mutating the relevant module attributes in-memory.
- Add `POST /api/dev/reprocess-all` that re-runs the DXF preprocessing pipeline on every existing file so DXF-side parameter changes can be applied retroactively (existing files store baked primitives; new uploads would otherwise be the only way to see DXF param effects).
- Mirror the last-applied values in `localStorage` so the modal restores its previous view without a roundtrip; the backend remains the source of truth.
- Document, in the modal copy and in the dev-overrides spec, that overrides are **in-memory only** (restart returns to compiled defaults) and **not safe under concurrent jobs** — single-user dev usage only.

## Capabilities

### New Capabilities
- `dev-parameter-overrides`: live, in-memory overrides for matching and DXF tunables, exposed via a Dashboard developer-mode modal and a small JSON API.

### Modified Capabilities
- `viewer-ui`: dashboard header gains a dev-only gear button next to the existing Developer Mode toggle that opens the parameter modal.
- `dxf-pipeline`: preprocessing reads its tunables from the live module attributes so dev overrides take effect on the next preprocess call; gains a "re-preprocess all files" entry point used by the dev modal.
- `pattern-matching`: matcher reads its tunables from the live module attributes so dev overrides take effect on the next match call.

## Impact

- Code: `app/matching.py`, `app/dxf.py` (no behavioural change at default values — only ensures bare-name lookups resolve through module globals so overrides are picked up); `app/main.py` (new endpoints); `app/jobs.py` or a small new helper (re-preprocess job); `app/static/dashboard.js`, `app/templates/dashboard.html`, `app/static/style.css` (modal + gear button).
- APIs: three new endpoints under `/api/dev/`. Existing match/preprocess endpoints are unchanged on the wire.
- No persistence: no new files, no schema changes, no migrations. Overrides die with the process.
- Out of scope: per-product or per-request overrides, persistence across restarts, audit log of changes, exposing tunables outside Dev Mode.

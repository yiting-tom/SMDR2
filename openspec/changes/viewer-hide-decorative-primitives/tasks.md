## 1. Locate render dispatch + safety baseline

- [x] 1.1 Find the primitive render loop in `app/static/canvas.js`. **Located:** `render()` at `canvas.js:825`. Per-primitive gate is `isLayerVisible(p)` at `canvas.js:54`, called at the top of every render pass (main `:855`, scan-all `:882`, near-miss `:903`, selection/match `:922`). Extending that one gate is the lowest-common point.
- [x] 1.2 Confirm `tests/test_dxf.py` covers `prim["decorative"] = True` tagging. **Result:** `test_decorative_dxf_types_are_flagged_and_excluded_from_index` passes (1 passed in 0.76s).

## 2. Add the front-end filter

- [x] 2.1 Filter implemented inside `isLayerVisible(p)` at `canvas.js:54` — `if (p.decorative) return false;`. Applies uniformly to all 4 render passes because every pass already calls `isLayerVisible` as its first per-primitive gate.
- [x] 2.2 Multi-line comment added above `isLayerVisible` explaining (a) which two cases the gate hides, (b) the back-end tagging (TEXT/MTEXT/DIMENSION/HATCH), (c) the font-fallback artefact this fixes, (d) parity with matching/selection code paths.
- [x] 2.3 No other files touched — `app/dxf.py`, `app/library.py`, matching code, primitives endpoint all unchanged.

## 3. Verification

- [x] 3.1 Back-end test suite: `uv run pytest` → 419 passed / 5 skipped / 0 failed. No regression.
- [ ] 3.2 **[USER]** Start the dev server, load a DXF known to contain TEXT or MTEXT entities, and confirm: boxy "rectangles per character" artefact gone; other geometry unchanged; click-to-select / scan-all / save-match behave as before. Cannot verify on dev machine without one of the user's MTEXT-containing DXFs.
- [ ] 3.3 **[USER]** Direct API check: `curl /api/files/<file_id>/primitives | jq '[.primitives[] | select(.decorative == true)] | length'` SHALL return a non-zero count for any file with decorative entities, confirming the back end is unchanged. (Same constraint as 3.2 — needs a registered file.)

## 4. Archive

- [ ] 4.1 After tasks 1-3 pass, run the OpenSpec archive flow (`/opsx:archive viewer-hide-decorative-primitives`) to fold the new `viewer-ui` requirement into the live spec and mark the change archived.

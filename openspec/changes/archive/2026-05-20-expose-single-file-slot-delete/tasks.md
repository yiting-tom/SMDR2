## 1. Dashboard UI

- [x] 1.1 In `app/static/dashboard.js::buildFileActions`, remove the `if (compact)` gate around the Delete button so the `✕` button is appended for every file row regardless of slot mode. Update the inline comment that justifies the gate to instead document that single-file Delete is needed to detach a file without uploading a replacement (e.g. when switching a product between RING and LID).
- [x] 1.2 Verify the existing `deleteProductFile(product, role, file)` helper works unchanged for the single-file case (no code change expected, just confirmation).

## 2. Tests

- [x] 2.1 Add a Jest / Playwright-style UI test for the single-file Delete affordance only if the project's test harness already supports DOM-level tests; otherwise rely on existing backend coverage (`tests/test_api.py` covers `DELETE /api/products/{pid}/files/{fid}` already). Document the choice in the task list. **Decision**: project has only `node:test` for pure-function `measure_core.js` — no DOM/jsdom/Playwright harness. Relying on the existing backend coverage in `tests/test_api.py` per the task's own fallback clause.

## 3. Spec sync + smoke check

- [x] 3.1 Run `openspec validate expose-single-file-slot-delete` and resolve any drift.
- [ ] 3.2 Manual browser check: load the dashboard, confirm Delete appears on a single-file slot, click it, confirm the dialog → slot returns to empty drop-zone. Confirm Delete on a multi-DXF slot still removes only the targeted file.

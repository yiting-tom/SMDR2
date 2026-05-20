## 1. Implementation (already shipped in commit `b630c26`)

- [x] 1.1 `app/library.py` — insert `Protrusion` into `DEFAULT_CLASSES` at position 11 (between `BGABall` and `2DBarcode`)
- [x] 1.2 `app/library.py` — add `"Protrusion": "protrusion"` to `CLASS_JSON_KEY`
- [x] 1.3 `app/static/canvas.js` — add `"Protrusion": "#80d8ff"` to `CLASS_COLORS`
- [x] 1.4 `openspec/specs/template-library/spec.md` — bump count from 14 to 15 and add `Protrusion` at order position 11

## 2. Tests

- [x] 2.1 Existing `test_store_creates_default_classes` and `test_create_library_seeds_default_classes` parametrise over `DEFAULT_CLASSES` so they cover the new class automatically. No new test required.
- [x] 2.2 `uv run pytest -q` — full suite passes

## 3. Spec sync

- [x] 3.1 `openspec validate add-protrusion-class --strict` passes
- [ ] 3.2 At archive time, the canonical spec already carries the
       Protrusion entry (it was edited directly in commit `b630c26`).
       Archive is a no-op for this change beyond moving the change
       folder into `openspec/changes/archive/`.

## Notes

This OpenSpec record was added retroactively (after commit `b630c26`
shipped) so the change-tracking log remains complete. The canonical
spec change was already merged in that commit; this scaffolding exists
to document the *why*, *what*, and *design decisions* that the bare
spec update doesn't capture.

## 1. Strip HATCH at parse time

- [x] 1.1 In `app/dxf.py` `flatten_for_render`, after `msp = doc.modelspace()` and before `Frontend(ctx, backend).draw_layout(...)`, iterate `msp.query("HATCH")` and call `msp.delete_entity(h)` for each
- [x] 1.2 Remove `"HATCH"` from the `DECORATIVE_DXFTYPES` set (decorative flag mechanism stays for TEXT / MTEXT / DIMENSION)
- [x] 1.3 Update the docstring / comment for `DECORATIVE_DXFTYPES` to drop the HATCH mention

## 2. Tests

- [x] 2.1 Delete `test_hatch_bounded_by_circle_emits_filled_circle` from `tests/test_dxf.py`
- [x] 2.2 Delete `test_hatch_bounded_by_polyline_circle_emits_filled_circle` from `tests/test_dxf.py`
- [~] 2.3 No standalone multi-subpath HATCH test existed in `tests/test_dxf.py`; the multi-sub-path / annulus case is now covered as case (c) inside `test_hatch_emits_no_primitives`.
- [x] 2.4 Add `test_hatch_emits_no_primitives` to `tests/test_dxf.py` covering: (a) HATCH bounded by a circular edge, (b) HATCH bounded by a polyline N-gon, (c) HATCH with multiple sub-paths (annulus). Each case asserts zero primitives carry the HATCH's handle, and non-HATCH siblings still flatten.
- [x] 2.5 Run `uv run pytest tests/test_dxf.py -q` — all tests pass

## 3. Spec sync

- [x] 3.1 `openspec validate drop-hatch-entities --strict` passes
- [ ] 3.2 At archive time, merge the modified `Server-side DXF flatten` requirement (with its updated scenarios) into `openspec/specs/dxf-pipeline/spec.md`, replacing the existing requirement block

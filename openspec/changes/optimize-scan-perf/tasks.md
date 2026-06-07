## 1. Shared signature index (vectorised gate)

- [x] 1.1 `app/matching.py`: `_get_signature_index(drawing)` — per-drawing numpy arrays (vertex_count, path_length, radius, σ-ratio), cached by `id(drawing)` like `_radius_bucket_cache`.
- [x] 1.2 `app/matching.py`: `_gate_candidates(template, idx)` — vectorised mask, formula bit-identical to `signatures_compatible`; reads `PATH_LENGTH_RATIO`/`RADIUS_RATIO` at call time.
- [x] 1.3 `_match_single_serial`: replace the per-shape `signatures_compatible` loop with `_gate_candidates` over the shared index.

## 2. IO / concurrency quick wins

- [x] 2.1 `app/main.py`: `GZipMiddleware(minimum_size=1024)`.
- [x] 2.2 `app/jobs.py`: Match JSON written with `separators=(",", ":")` (was `indent=2`).
- [x] 2.3 `app/main.py`: `scan-all` `async def` → `def` (CPU-bound, no `await`) so Starlette threadpools it off the event loop.

## 3. Verify

- [x] 3.1 `pytest -q` — 547 passed (matcher candidate sets unchanged; compact JSON round-trips; GZip transparent; sync scan-all serves same response).
- [x] 3.2 Benchmark: scan_all_loop 2,245 → 1,330 ms (−40.7%); Match-JSON compact 2.31×; primitives gzip 13.7×. (`benchmarks/results/{baseline,opt_ab}.json`)
- [x] 3.3 `openspec validate optimize-scan-perf --strict`.
- [ ] 3.4 **[USER]** Optional live check: two files, scan one while operating the other — the second no longer blocks; reopen a saved-match file and the overlay is complete.

## 4. Archive

- [ ] 4.1 `/opsx:archive optimize-scan-perf` after merge + the optional live check.

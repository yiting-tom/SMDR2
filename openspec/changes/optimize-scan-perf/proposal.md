## Why

A benchmark (`benchmarks/perf_matcher.py`, 200k circles + 300 polylines, 50
templates) established that `scan-all` / `save-match` spend most of their time
re-walking the full drawing shape dict in Python once per template. The
single-entity chamfer hot loop (`_match_single_serial`) called
`signatures_compatible` per shape, per template — ~20× redundant full-dict scans
for the polyline classes. Measured: `scan_all_loop` = 2,245 ms.

Three smaller IO/concurrency costs sat alongside it: `GET /primitives` ships
tens of MB uncompressed on every viewer load; Match JSON was written with
`indent=2` (~2.3× larger on 10k-instance BGA saves); and `scan-all` was an
`async def` with a fully CPU-bound body, so it ran on the event loop and blocked
every other request for the whole scan (the "one drawing blocks everyone else"
symptom).

All four changes are behaviour-preserving — same matches, same response shapes,
same Match JSON contents — verified by the full suite (547 tests) and by the
benchmark's match counts being unchanged.

## What Changes

- **Shared per-drawing signature index (vectorised gate)** — `app/matching.py`.
  Precompute each shape's four gate scalars (`vertex_count`, `path_length`,
  `radius`, σ-ratio) into parallel numpy arrays ONCE per drawing, cached by
  `id(drawing)` (same lifetime invariant as `_radius_bucket_cache`). The chamfer
  single-entity loop gates candidates with a vectorised mask (`_gate_candidates`)
  whose formula is bit-for-bit identical to `signatures_compatible`, so the
  candidate set — and thus the matches — are unchanged. **Measured: scan_all_loop
  2,245 → 1,330 ms (−40.7%); polyline single-template 96 → 49 ms.**
- **GZip middleware** — `app/main.py`. `GZipMiddleware(minimum_size=1024)`
  compresses large JSON responses (notably `/primitives`). Transparent to the
  browser. Benchmark primitives payload gzip ratio ≈ 13.7× (synthetic; real data
  lower but still multi-x).
- **Compact Match JSON** — `app/jobs.py`. `json.dump(out, f,
  separators=(",", ":"))` instead of `indent=2`. Round-trip identical for the
  rule-checker; **2.31× smaller** on the benchmark's 10k-instance save.
- **Non-blocking `scan-all`** — `app/main.py`. `async def scan_all` → `def
  scan_all`; the body has no `await`, so as a sync path operation Starlette runs
  it in its threadpool, freeing the event loop so other files' requests proceed
  during a scan.

Out of scope (measured, deliberately NOT done): ProcessPool across templates. The
benchmark's worker sweep (2/4/6/8) showed a cold pool is a net loss (~4.2 s — each
worker rebuilds 200k shapes) and only a warm, shape-resident pool wins (6 workers
≈ 380 ms). That needs a persistent resident-shape pool — a separate architectural
change, tracked for later.

## Capabilities

### Modified Capabilities

- `pattern-matching`: ADDS a "Drawing-level signature index cache" requirement —
  the single-entity chamfer gate uses a shared per-drawing signature index whose
  candidate set is identical to evaluating `signatures_compatible` per shape.

## Impact

- **Code**: `app/matching.py` (signature index + vectorised gate), `app/main.py`
  (GZip middleware, sync `scan-all`), `app/jobs.py` (compact Match JSON).
- **Tooling**: `benchmarks/perf_matcher.py` + `benchmarks/results/{baseline,opt_ab}.json`
  hold the before/after (already on the `perf-benchmark` branch).
- **Tests**: 547 pass unchanged (incl. 168 matcher tests); the vectorised gate
  produces identical candidate sets, the compact JSON round-trips, GZip is
  transparent to `TestClient`, and the sync `scan-all` serves the same response.
- **Behaviour**: none changed — purely faster + smaller + non-blocking.
- **Note**: GZip compresses on the event loop in the ASGI middleware chain; for a
  very large `/primitives` response this is a small added event-loop cost. Net
  win on the wire; flagged for the warm-pool follow-up if it ever matters.

## Context

`app/dxf.py:_flatten_path` hard-codes `sub.flattening(0.01)`. The 0.01
is interpreted in raw drawing units. For mm-scale packaging files
(diagonal ~ 30 mm), this is fine — a BGA-ball arc of radius 0.15
flattens to about 7 chord segments. For files where the designer
intended mm but the DXF was authored at "1 unit = 1 µm" or where
`INSUNITS = 0` led to a 1000× downstream inflation, the same arc is
now radius 150 and ezdxf produces ~217 chord segments — without any
of those segments being geometrically meaningful, because the *visible*
deviation tolerance the user cares about is "smaller than a pixel"
not "smaller than 0.01 of whatever unit this file thinks it has".

`optimize-bga-render` solves the CIRCLE case structurally (no
flattening at all), but ELLIPSE / SPLINE / partial ARC entities still
go through `_flatten_path`. A 1 M-entity file built mostly from those
primitive types remains un-loadable until tolerance scales.

V8's hard limit on a single JavaScript string is `2^29 - 24` bytes
(~512 MiB). FastAPI's `response.json()` materialises the whole body
as one string before parsing, so anything past that ceiling fails
fatally at decode time.

## Goals / Non-Goals

**Goals:**
- A 1 M-entity DXF whose nominal scale is 1000× over the intended
  unit becomes openable in the viewer.
- For correctly-scaled files (packaging diagonal ~ 30–300 mm), the
  emitted primitives are byte-identical to today, so existing
  tests / fingerprints / matcher caches don't drift.
- Make the chosen tolerance visible in logs so a wonky DXF is
  diagnosable without re-running with prints.

**Non-Goals:**
- Honoring `$INSUNITS`. The spec value is unreliable in this
  workflow (often `0`); a purely geometric heuristic is more robust
  than trusting metadata. (`$INSUNITS` may show up in a future
  "warn user about unit scale" change but is out of scope here.)
- Detecting and *correcting* the 1000× scale (rewriting coords). That
  would change matching fingerprints and is a separate, riskier
  initiative.
- Touching the viewer / frontend.
- Per-entity tolerance. One file-wide tolerance is enough — the
  goal is "make this file loadable", not "perfectly tune each curve".

## Decisions

### 1. Geometric heuristic, not metadata

Tolerance is chosen from the modelspace bbox diagonal `D`:

```python
BASE_TOLERANCE  = 0.01    # drawing units; same as today
SCALE_FACTOR    = 1e-5    # diagonal / 100_000

def choose_flatten_tolerance(diagonal: float) -> float:
    return max(BASE_TOLERANCE, diagonal * SCALE_FACTOR)
```

Sanity-check at typical scales:

| diagonal | tolerance | comment |
|---|---|---|
| 30 mm     | 0.01    | normal packaging file — unchanged |
| 300 mm    | 0.01    | unchanged (3 mm × 1e-5 = 3e-5, floor wins) |
| 3 000 mm  | 0.03    | mild upscaling — barely a vertex difference |
| 30 000 mm | 0.30    | 100× scale — chord count ~ 10× lower |
| 300 000 mm| 3.0     | 1000× scale — chord count ~ 32× lower |

Why `1e-5`? Empirically `D / 1e5` is "much smaller than one screen
pixel at any reasonable zoom" — at zoom-to-fit, one pixel ≈ D /
canvas_width ≈ D / 1500, so D/100k is ~67× tighter than a pixel,
well below what any user will notice. Tighter would defeat the
purpose; looser would risk visible faceting at extreme zoom-in.

Alternatives considered:
- **Use `$INSUNITS` to compute a unit-aware tolerance**: rejected.
  The exact files that need this fix are the ones with
  `INSUNITS = 0`, so the metadata is useless on the population we
  care about.
- **Iterative flatten-then-resample**: rejected. Two passes over a
  1 M-entity file is way more expensive than one pre-scan + correct
  tolerance.
- **Decimate after flatten**: rejected. Saves bandwidth but not
  parse-time memory — we still allocate the bloated points list
  inside the backend before decimation could run.

### 2. Cheap pre-scan via `ezdxf.bounds.extents`

ezdxf provides `ezdxf.bounds.extents(modelspace, fast=True)` which
returns a bbox without fully resolving every entity — it samples each
entity's declared geometric extents (a CIRCLE's `(center, radius)`,
a LINE's endpoints, etc.). For a 1 M-entity file this completes in
~hundreds of ms vs. the full flatten which is multi-second.

Sketch:

```python
def flatten_for_render(dxf_path):
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    ext = ezdxf.bounds.extents(msp, fast=True)
    if ext.has_data:
        D = math.hypot(ext.size.x, ext.size.y)
        tol = choose_flatten_tolerance(D)
    else:
        tol = BASE_TOLERANCE
    backend = JSONBackend(flatten_tolerance=tol)
    Frontend(...).draw_layout(msp, finalize=True)
    return RenderOutput(...)
```

Backend ctor takes the tolerance and stores it; `_flatten_path` is
turned into a method or takes the tolerance as an argument.

### 3. Thread tolerance through `JSONBackend`, not via module-globals

Today `_flatten_path` is a module-level helper that reads the constant
directly. We turn it into a method (or pass `tolerance` explicitly)
so per-call instances of `JSONBackend` don't share state. This keeps
the change local — every call site in `app/dxf.py` lives in
`JSONBackend` already.

`CURVE_FLATTENING_DISTANCE = 0.01` stays as the module constant for
backward compat and clarity (it becomes the `BASE_TOLERANCE`); the
new logic uses it as the floor.

### 4. Telemetry

A single `logging.getLogger("app.dxf").info(...)` line emitted from
`flatten_for_render` reports
`"flatten: diagonal=… → tol=… (base=0.01)"`. When tolerance is
clamped to the base (normal files), the line is suppressed to keep
logs quiet — only pathological-scale files leave a trace.

## Risks / Trade-offs

- **[Risk] Visible faceting at extreme zoom-in on a "normal" file
  that happens to live near the threshold** — e.g., a 100 m-diagonal
  DXF gets tol = 1 mm, so a 1 mm-radius pin chamfer becomes ~3
  segments at the centre and looks angular.
  → Mitigation: `SCALE_FACTOR = 1e-5` keeps tol << one screen pixel
  at fit-zoom; only zooming in by > 100× hits the visible boundary,
  at which point the user can request a manual flatten override via
  a future setting.
- **[Risk] Matching engine fingerprints shift for files whose
  diagonal exceeds the floor** — different vertex counts feed into
  `EntityShape`.
  → Mitigation: for CIRCLE entities the matcher already gets a
  scale-invariant point cloud (`optimize-bga-render` §3.1). For
  non-CIRCLE flattened curves, fingerprints already depend on the
  file's flatten output; same DXF re-preprocessed yields the same
  fingerprint, only the cross-file comparability of unit-scale-busted
  templates shifts. Acceptable — those files were unmatchable before
  this change (un-openable).
- **[Risk] `ezdxf.bounds.extents` is slow on some DXFs** — for
  complex SPLINEs the "fast" path may still need to evaluate the
  spline.
  → Mitigation: if the pre-scan ever costs > 10 % of full flatten,
  fall back to base tolerance and skip the optimization. Verify
  empirically in tests/8.x bench.
- **[Trade-off] Same file re-preprocessed under a different version
  of the heuristic yields slightly different vertex counts** —
  matcher caches keyed by file content (`file_id` = SHA-256 of bytes)
  could surface stale entries that don't match new ones.
  → Mitigation: re-running preprocess invalidates the parsed cache
  automatically (the file overwrites its own `data/parsed/...json`).

## Migration Plan

1. Land behind no flag.
2. Existing parsed files keep working; re-preprocess to pick up the
   new tolerance.
3. Rollback: revert the commit. No data migration needed — parsed
   JSONs are file-id-keyed and immutable per content.

## Open Questions

- Should the chosen tolerance be surfaced in the dashboard alongside
  bbox / primitive count? Defer until users actually ask for it.
- Do we want to also adjust tolerance *down* on tiny-scale files
  (D < 0.1 mm, microscope-level diagrams)? Currently the floor
  protects us; a separate enhancement could add a ceiling. Defer.

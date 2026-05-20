## Context

The matcher's chamfer tolerance is a single global constant
`TOLERANCE_ABS = 0.05` mm in `app/matching.py`. The matching public APIs
already accept a `tolerance` kwarg — what's missing is a place to *store*
the per-class value and the plumbing to *pass* it at every call site.

Classes live in SQLite (`classes` table, PK `(library_id, name)`) and have
just `name`, `rank`, `created_at` today. Template-library code in
`app/library.py` owns the read/write path; FastAPI handlers in
`app/main.py` own the HTTP surface and call into the matcher; the
dashboard reads the class summary endpoint to render the class list.

The "match" flow has three callers that loop over classes and need to
threading per-class tolerance:
1. `GET /api/files/{file_id}/scan-all` — live scan.
2. `POST /api/files/{file_id}/match-json` — persisted match JSON.
3. The preprocess worker that writes the prematch cache.

Plus a fourth caller — `POST /api/files/{file_id}/match` (add-mode
preview) — which currently has no class context, so it needs an optional
`class_name` in the request body.

## Goals / Non-Goals

**Goals:**
- Store an optional per-class tolerance and surface it via the existing
  class-summary endpoint.
- Make scan-all, save-match-json, prematch, and add-mode preview honor
  the per-class value when set.
- Migration leaves every existing class with NULL tolerance (= identical
  behavior to pre-change).
- Dashboard lets the user edit each class's tolerance inline.

**Non-Goals:**
- No "tolerance presets" or per-template (not per-class) tolerance — class
  is the right granularity (substrate vs BGA ball).
- No tolerance editing surface in the viewer add-mode toolbar (out of
  scope; user sets it in the dashboard and viewer just consumes).
- No retroactive re-scan after editing tolerance — the user re-triggers
  scan-all manually (cheap, already idempotent).

## Decisions

**Store tolerance on `classes`, not on templates** (over: per-template).

- Substrate vs BGA-ball is a class-level distinction; every template
  filed under "Substrate" wants the same tolerance. Per-template would
  let the user enter ten different tolerances for ten substrate
  templates — pure foot-gun. Per-class also makes the dashboard UI
  trivial (one input per class row).

**`NULL` means "use global default"** (over: store default explicitly).

- Migration is one ALTER TABLE that doesn't need to compute a default
  per row. The global default lives in `app/matching.py`
  (`TOLERANCE_ABS`); the resolution happens at call site:
  `tol = cls.tolerance if cls.tolerance is not None else TOLERANCE_ABS`.
  Future change to `TOLERANCE_ABS` instantly updates every "unset"
  class.

**One PUT endpoint, no separate POST/DELETE** (over: REST-strict).

- `PUT /api/libraries/{library_id}/classes/{class_name}/tolerance`
  with body `{tolerance: number | null}` covers set / clear in one
  shape. The UI input's "empty string" maps cleanly to `null`.

**Add-mode `match` endpoint takes `class_name`, not `tolerance`**
(over: pass tolerance directly).

- Frontend already knows the `addModeClass` (the active "+ class").
  Sending the class name keeps tolerance authoritative on the
  backend — no chance of the viewer cache diverging from the DB.
  If `class_name` is omitted or unknown, the endpoint falls back
  to `TOLERANCE_ABS` (today's behavior).

**Validate tolerance > 0 and ≤ a sane cap** (over: accept anything).

- Negative or zero tolerance crashes the chamfer comparison
  (technically `<= 0` accepts nothing). A 1000 mm tolerance would
  effectively disable matching strictness — surface area for
  user error. Cap at e.g., 100 mm (well above any realistic class
  scale) with HTTP 400 on violation.

## Risks / Trade-offs

- [User sets tolerance too loose → matcher returns false-positives] →
  Mitigation: keep the global default tight; the per-class value is
  opt-in. Add a tip line in the dashboard near the input
  ("typical: 0.05 mm for BGA balls, 0.5 mm for substrates").
- [Schema migration touches every existing library] → Mitigation:
  single `ALTER TABLE classes ADD COLUMN tolerance REAL NULL` — idempotent
  via `_migrate`'s "has_col" guard pattern already in `app/library.py`.
  No data backfill needed (NULL is the desired starting state).
- [Prematch cache served before edit becomes stale after edit] →
  Mitigation: editing tolerance does NOT auto-invalidate the prematch
  cache; the user re-runs scan-all to see the effect. Document this in
  the dashboard tooltip. (Alternative — auto-invalidate — would surprise
  the user mid-task and is needless complexity.)
- [Backwards-compatible class summary shape] → Mitigation:
  `summary()` was `[{name, count}]`; new field `tolerance` is additive.
  Existing dashboard code reads the same keys.

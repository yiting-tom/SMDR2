## Context

Two recent attempts to fix substrate matching failed:

1. The `decorative: true` flag on HATCH still rendered HATCH boundaries
   as polylines that polluted the scan (fixed in commit `a64703b`).
2. Per-class `tolerance` override (commit `9db06a6`, reverted in
   `c4df21d`) couldn't bridge the gap: substrate chamfers at 0.46 mm
   between visually-identical authoring variants, but loosening
   tolerance past that point forces the unchanged global ±20 %
   path-length / radius pre-filter to admit candidates of obviously
   wrong dimensions ("還是不行，只會抓出大小不同的").

The user's domain spec for substrate matching is:
> "Substrate 基本上會要求 bbox 長寬要一樣，vector 數量不會差太多，支援
> 翻轉跟旋轉就好"
> (bbox dimensions must match, vertex count must not differ much,
>  rotation/flip invariance is enough.)

This is what `signatures_compatible` *already computes* — path length
(rotation-invariant perimeter), max radius from centroid
(rotation-invariant size), σ₂/σ₁ (rotation/mirror/scale-invariant
aspect). The mismatch is purely operational: the gate's defaults are
`PATH_LENGTH_RATIO = 0.20` / `RADIUS_RATIO = 0.20` because they exist as
a *pre-filter* — chamfer downstream is the strict gate. For classes that
*skip* chamfer the dimensional ratios become *the* gate and need to be
tightened (5 % is reasonable).

## Goals / Non-Goals

**Goals:**
- Add a `match_strategy` per-class field with two options today:
  `"chamfer"` (current behavior, default) and `"signature"` (signature gate
  is the verdict; no chamfer).
- Add a per-class `bbox_ratio` that, *under signature mode only*, replaces
  both `PATH_LENGTH_RATIO` and `RADIUS_RATIO` for that class — so
  signature-mode classes can be tight on size (5 %) without changing
  the BGA-ball-friendly defaults globally.
- Resolve and thread `(strategy, bbox_ratio)` at every scan call site:
  scan-all, save-match-json, prematch worker, add-mode preview.
- Migration leaves every existing row at `match_strategy = 'chamfer'`,
  `bbox_ratio = NULL` — identical pre-change behavior.

**Non-Goals:**
- Not introducing per-template strategy; per-class is the right grain.
- Not changing what `signatures_compatible` checks. Same predicate,
  same σ-ratio tolerance, same vertex-count floor. Only the two
  dimensional ratios become per-class-overridable.
- Not unifying with the reverted per-class tolerance — the user
  explicitly rejected that path. `match_strategy` supersedes it.
- Not extending `signature` mode to multi-entity templates yet.
  Substrates are single-entity ("frame outline"); multi-entity
  signature matching has a separate pose-resolution question. Today
  signature mode applies to single-entity templates only; multi-entity
  templates always use `chamfer` regardless of class strategy.

## Decisions

**Store `match_strategy` and `bbox_ratio` on `classes`** (over: per-template).

- Strategy is a *what kind of thing is this class?* question. Per-template
  invites incoherent libraries (Substrate template 1 chamfer-matches,
  Substrate template 2 signature-matches — meaningless).

**Single coupled PUT endpoint** (over: separate endpoints per field).

- `bbox_ratio` is operationally tied to `strategy = "signature"`; setting
  it under `chamfer` is a no-op the API shouldn't pretend to honor.
  Single endpoint with body `{strategy, bbox_ratio?}` makes the coupling
  explicit. Backend SHALL store `bbox_ratio` as NULL when strategy flips
  back to `"chamfer"` (so a later flip to `"signature"` starts fresh from
  the default).

**Signature-mode emits exactly match-or-nothing — never near-miss**
(over: emit "signature failed" as a near-miss).

- Signature mismatch is a categorical "different shape" verdict, not a
  measured-distance shortfall. A near-miss with `reason: "signature"`
  would be noise the UI has no useful action for. The current
  near-miss visualization (orange highlight, score, scale) is
  meaningful for chamfer-mode failures and would be misleading under
  signature mode.

**Use `bbox_ratio` for BOTH `PATH_LENGTH_RATIO` and `RADIUS_RATIO`**
(over: two separate per-class ratios).

- These two gates measure dimensional similarity from different angles
  (perimeter sum vs. extent peak) and in practice move together. One
  knob avoids the foot-gun of accidentally tightening one and not the
  other. Future work can split them if a real case demands it.

**Single global default 0.05 for `bbox_ratio` when set under signature
mode** (over: leave NULL = use global 0.20).

- Setting `strategy = "signature"` without also tightening `bbox_ratio`
  leaves the gate at 20 % — too loose, and that's the user's actual
  complaint. To avoid surprising the user, the PUT endpoint SHALL
  default `bbox_ratio = 0.05` when the body omits it AND strategy is
  being set to `"signature"`. Users who want looser can pass an explicit
  value.

**Matcher `strategy` kwarg defaults to `"chamfer"`**.

- Backwards-compatible — every existing caller (tests, internal
  callers) keeps current behavior.

## Risks / Trade-offs

- [User sets `signature` on a class that genuinely needs chamfer
  discrimination] → Mitigation: per-class, easy to undo; viewer
  badge surfaces the current mode so the user notices what's set.
- [`bbox_ratio = 0.05` rejects legitimate matches from real-world
  CAD precision drift] → Mitigation: 5 % is generous for substrate-scale
  (25 mm × 5 % = 1.25 mm — way beyond CAD precision); user can loosen
  via the editor if a specific shop's drift demands it.
- [Multi-entity templates filed under a signature-mode class] →
  Mitigation: matcher SHALL fall back to chamfer for multi-entity
  templates regardless of class strategy, and log one info-level line
  per such occurrence so the user understands the class setting was
  bypassed. (Substrate templates in practice are single-entity outline
  polylines — this is an escape hatch, not a real path.)
- [PCA σ-ratio gate (`SIGMA_RATIO_TOL = 0.15`) is still global]
  → Mitigation: leave it alone for now. It's already
  rotation/mirror/scale invariant and 0.15 is loose enough for
  substrate aspect variation. Make it per-class later if a real case
  needs it.

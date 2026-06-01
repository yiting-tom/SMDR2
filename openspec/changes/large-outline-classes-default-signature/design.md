## Context

Per-class match config (`match_strategy`, `bbox_ratio`) lives in the `classes` table (added by `add-per-class-match-strategy`), default `('chamfer', NULL)`. Signature mode (`app.matching.find_matches(strategy="signature")`) runs only the `signatures_compatible` gate — perimeter, max-radius, and principal-axis aspect — and computes no chamfer. The chamfer path's resample is phase/winding-sensitive on large sharp-cornered outlines (see the `matcher-winding-invariant-resample` change), so for boundary classes signature mode is both simpler and more robust.

## Decisions

### D1. Declare defaults in code, not just per-library DB state

A `CLASS_DEFAULT_MATCH_CONFIG` registry keeps the policy in one reviewable place and applies uniformly to every library (existing and future), rather than relying on each operator toggling it per library in the UI.

### D2. Two application points

- **Seed (`add_class`)**: new classes / new libraries get the registry default at insert, and it is persisted so the DB row matches the in-memory config.
- **Boot migration**: existing libraries already have these class rows at `chamfer`/NULL; a startup `UPDATE` converts them.

### D3. Convert only the pristine state; never clobber an explicit signature config

The migration's `WHERE match_strategy = 'chamfer' AND bbox_ratio IS NULL` converts only rows that carry no explicit choice. Any signature config set in the UI (any `bbox_ratio`) is left untouched on reboot. Consequence: these three classes cannot be pinned to `chamfer` permanently — a revert to `chamfer`/NULL re-converts on next boot. That is intentional: these classes are *declared* signature, and `chamfer`/NULL is indistinguishable from "never chosen". Operators who need a different behaviour pick a signature `bbox_ratio` (preserved) rather than chamfer.

### D4. `bbox_ratio = 0.0001`

A tight 0.01% size tolerance: same-size copies match, while differently-sized boundaries are rejected on perimeter/radius. The σ-ratio aspect gate (`SIGMA_RATIO_TOL`, not overridable) still applies.

## Risks / Trade-offs

- **[Trade-off]** Signature mode does not verify outline *shape* beyond perimeter + radius + aspect, so two different boundaries that share all three would both match. For substrate/lid boundaries this is acceptable — perimeter is a strong discriminator and same-size same-product boundaries are the intended match. Classes needing shape discrimination stay on chamfer.
- **[Trade-off]** Signature mode emits no near-misses for these classes (lost diagnostic). Acceptable for boundary classes.
- **[Risk]** A future need to pin one of these to chamfer. → **Mitigation:** remove it from the registry (one line) — the migration then stops converting it.

## Migration Plan

No schema change (columns already exist). The boot migration is idempotent and converts existing rows on next Store open. Stale prematch JSON regenerates on the next prematch / Scan All.

## Open Questions

None.

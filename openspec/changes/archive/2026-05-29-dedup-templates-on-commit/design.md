## Context

The template library currently has no commit-time dedup. Operators frame-select shapes repeatedly — sometimes by accident (double-Enter), sometimes as a habit ("let me reframe this and confirm it's the same part"). Each commit unconditionally appends a row, and `scan-all` iterates every row in the class regardless. We want commit to be idempotent for translation-equivalent inputs under the same `(library, class, scope)`.

This change was originally implemented on the `c-redesigned` branch (commit `41ddbba`) when `Library.add_template(template) -> None` was the single insertion entry point. Main has since diverged: there are now **two** entry points (`add_template` and `add_template_for_file`), product-scoped classes (`PRODUCT_SCOPED_CLASSES = {Substrate, Lid, LidOuter, LidInner, DieArea, C4Ball, BGABall, Protrusion}`) persist rows with a non-null `product_id`, and the in-memory `Library._templates` cache is hydrated at library-scope only (so for product-scoped classes the cache leaks across products and the matcher reads through `store.load_library(library_id, product_id=...)` instead). The dedup hook must respect this scoping or it would either falsely collapse same-shape-different-product commits, or miss obvious double-commits inside the same product.

## Goals / Non-Goals

**Goals:**
- One `template_signature(entity_point_sets) -> tuple` pure function — canonical, hashable, translation-invariant, entity-order-invariant, vertex-order-invariant, rotation/scale/reflection-distinct, bucketed at 10⁻⁴ mm.
- `Library.add_template_for_file` short-circuits when an existing template in the same `(library, class, effective_product_id)` scope has the same signature. Both `add_template` and `add_template_for_file` get the dedup behaviour because `add_template` already delegates.
- Commit endpoint response carries `already_existed: bool`. When true, `template_id` is the existing row's id (operator's downstream "Save Match" still has a real id to reference).
- Viewer status bar branches: `template already in library (#N)` on dedup hit, regular `saved … (#N)` otherwise. Scan-all auto-refresh (Wave-5 equivalent, already on main) suppressed on hit because no new handle set is being merged into the overlay.
- Startup duplicate detection emits a WARNING log per `(library, class, signature)` group with count > 1. Pre-dedup rows are NOT migrated.

**Non-Goals:**
- No cross-class dedup (same signature in different class is a NEW row by design — the class label is meaningful).
- No cross-library dedup (libraries are scope boundaries).
- No cross-product dedup for product-scoped classes (same Substrate geometry in product A vs B is two different parts of two different products → two rows).
- No rotation/reflection-equivalent dedup. A 90° rotated copy gets a NEW row.
- No retroactive cleanup of pre-existing duplicate rows. The WARNING surfaces them; the operator decides whether to clean up.
- No change to `scan-all`, `find_matches`, or any matcher code.
- No change to `Template` row schema (no `product_id` column added — we read it back via the store query, not from the in-memory Template).

## Decisions

### D1. Canonical signature: centroid-translated + 10⁻⁴-bucketed + sort-of-sorts

```
def template_signature(entity_point_sets):
    all_pts = [p for ent in entity_point_sets for p in ent]
    if not all_pts:
        return ()
    gx = sum(p[0] for p in all_pts) / len(all_pts)
    gy = sum(p[1] for p in all_pts) / len(all_pts)
    entity_keys = []
    for pts in entity_point_sets:
        bucketed = tuple(sorted(
            (round((p[0] - gx) * TEMPLATE_DEDUP_BUCKET),
             round((p[1] - gy) * TEMPLATE_DEDUP_BUCKET))
            for p in pts
        ))
        entity_keys.append(bucketed)
    return tuple(sorted(entity_keys))
```

**Why centroid-translated:** any rigid translation of the input shifts the centroid by the same vector, leaving every `(p - centroid)` invariant. This is the translation-invariance property.

**Why sort within entity then sort entities:** removes any input ordering. `[A, B]` and `[B, A]` produce the same key; `[(p1, p2)]` and `[(p2, p1)]` for the same polygon produce the same key.

**Why 10⁻⁴ mm bucket grid (= 0.1 µm):** parallel to existing `_radius_bucket_key(r) = round(r * 10**4)`. Real packaging classes differ by at least 1 µm, which is 10 buckets — never coincide. FP noise from `from_points` round-trip is ~1 ULP at typical world coordinates, far below 0.1 µm. The 10⁻⁴ constant lives at module level (`TEMPLATE_DEDUP_BUCKET = 10**4`) so a future precision change is one line.

**Alternative considered:** centroid-translated + `np.allclose` per-template comparison. Rejected — O(N) hash lookup is strictly cheaper than O(N) elementwise compare, and the bucket key is hashable so we can index it.

**Alternative considered:** Hash of stably-rounded coordinates without centroid subtraction. Rejected — would not be translation-invariant; same shape framed at two on-canvas positions would produce two rows.

### D2. Memoize on the Template instance

Looking up an existing template's signature is O(points). We compute it once on first read and cache as `Template._signature` (an `Optional[tuple]` attribute, populated on demand). This makes the per-commit scan amortized O(N) hash comparisons after the first commit warms the cache for each row.

Implementation: a free function `_template_signature_cached(t: Template) -> tuple` that reads `getattr(t, "_signature", None)`, computes if absent, sets and returns. Avoids modifying the `Template` dataclass schema (it's a `@dataclass(frozen=True)` candidate elsewhere — we treat `_signature` as a private attribute, set via `object.__setattr__` if needed).

### D3. Scope-aware lookup that respects product scoping

The scope tuple is `(library_id, class_name, effective_product_id)` where:
```
effective_product_id = product_id if is_product_scoped(class_name) else None
```

For **library-scoped** classes (most): scan `self._templates[class_name]` directly. The cache is canonical at library scope.

For **product-scoped** classes (`PRODUCT_SCOPED_CLASSES`): the cache is unreliable (mixes rows across products as different files commit). Re-load the per-product view via `self.store.load_library(self.library_id, product_id=effective_product_id)` and scan `templates_by_class.get(class_name, [])` from THAT result. One extra SQL roundtrip per product-scoped commit — acceptable (commits are rare, scan-all is the hot path).

**Alternative considered:** add `product_id` to the `Template` dataclass and populate at load. Rejected — wider blast radius (every Template construction site, JSON serialisation, every test fixture), and the per-commit store roundtrip is cheap.

### D4. add_template_for_file signature → tuple[Template, bool]

```
def add_template_for_file(
    self, template: Template, *, product_id: str | None
) -> tuple[Template, bool]:
```

Returns `(existing_template, True)` on dedup hit, `(template, False)` otherwise. `Library.add_template(template)` continues to delegate (with `product_id=None`) and propagates the tuple.

Two real call sites:
- `app/main.py::commit` — unpacks tuple, forwards `already_existed` into response, uses returned template's id (which is the existing one on hit, the fresh one otherwise).
- Tests — `tests/test_library.py` and the new `tests/test_library_dedup.py` get tuple-aware updates. Old tests that ignored the return value (`lib.add_template(t)`) keep working because tuple-return is also valid as a statement.

**Existing test that needs amending:** `tests/test_library.py::test_all_templates_returns_indexed_tuples` — it currently adds two identical templates t1, t2 under `SMD-2T` expecting both in `all_templates()`. Dedup would collapse the second. Fix: give t2 distinct geometry (e.g. 4 points vs 5 points) so they are genuinely different and intent is preserved.

### D5. Commit response payload contract

```json
{
  "template_id": "<existing-or-new-uuid>",
  "class_name": "...",
  "library_id": "...",
  "count": <int>,
  "already_existed": true | false
}
```

`count` stays — it's the post-call total for the class within the cache's view, which is `len(self._templates[class_name])` either way. On dedup hit the cache count is unchanged (we didn't append), so the operator sees the same chip count and knows their click was a no-op. The clients already ignore unknown fields, so `already_existed` is non-breaking on the wire.

### D6. Startup duplicate detection (WARNING only)

`Library.__init__` finishes loading, then groups every loaded template by `(class_name, template_signature(t.entity_point_sets))`. For each group with count > 1, emit one log line at WARNING level:

```
library {library_id}: class {class_name} has {N} templates with identical canonical signature — pre-dedup data, scan-all will iterate redundantly
```

We do NOT delete or migrate the duplicates — leaving them respects an existing on-disk state the operator may have built deliberately. The new invariant from D4 applies only to commits going forward.

`logging` is added as a module import at the top of `app/library.py`; module-level `logger = logging.getLogger(__name__)` provides the surface.

### D7. Viewer status branch + scan-all auto-refresh suppression

In `app/static/canvas.js::commitCurrentTemplate` after the `/commit` POST resolves:

```js
if (data.already_existed) {
    setStatus(`template already in library (#${data.count})`);
    // no scan-all merge — same handle set already in overlay
    return;
}
setStatus(`saved ${data.class_name} template (#${data.count})`);
// existing Wave-5 auto-refresh code path here, unchanged
```

The scan-all auto-refresh suppression is important: re-merging the same handle set into the overlay is a no-op-with-render, but the render itself takes ~10-50ms on large libraries and visibly flashes. Skipping it on hit keeps the operator's UI feeling instant.

## Risks / Trade-offs

- **[Risk]** Bucket-edge fence-post for signature: a coordinate at `(p - centroid) * 10^4 = 0.5` could ULP-drift across the round boundary, producing a different bucket key for the same shape. → **Mitigation:** the bucket grid is 10× coarser than the smallest physical distinction. We'd need a deliberately pathological input to hit the fence-post on a meaningful shape. If it ever bites, the fix is the same ±1-bucket-neighbour-window pattern already used by `_match_single_circle` and `_get_fingerprint_buckets`.
- **[Risk]** Store roundtrip on every product-scoped commit. → **Mitigation:** commits are operator-triggered and rare (~ once per pattern, not per match). Scan-all already pays a similar cost on every call.
- **[Risk]** Pre-dedup duplicate templates already exist in some user's library; the new WARNING line will spam at startup. → **Mitigation:** one line per duplicate GROUP (not per row), and the user can either ignore or clean up via the existing delete-template UI. Not noisy at production scale.
- **[Trade-off]** No rotation-equivalent dedup. A 90° rotated copy becomes a new row. → **Justification:** intentional — rotation is meaningful in DXF (orientation matters for some classes, e.g. Pin-1). Dedupping across rotation would silently collapse genuinely different commits.

## Migration Plan

No DB migration. Existing libraries load as-is. Pre-existing duplicate rows continue to exist and continue to be iterated by scan-all (redundantly). The WARNING surfaces them. No rollback complexity — reverting the change reverts the dedup behaviour cleanly.

## Open Questions

None. The design is symmetric with existing precision conventions (`_radius_bucket_key`) and existing scope rules (`is_product_scoped`).

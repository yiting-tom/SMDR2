## Context

The single-entity matcher (`_match_single_serial`) and `align_score` resample every outline to `RESAMPLE_N=64` points evenly spaced by arclength (`_resample_arclength`), then center, isotropically scale, align principal axes, and take the minimum symmetric Chamfer over 4 sign-variant orientations. A candidate is a match when that Chamfer ≤ `TOLERANCE_ABS` (0.2 mm).

`_resample_arclength` starts its samples at `points[0]` and walks the polyline in stored order (treating it as closed). The sample positions are therefore a function of **which vertex is first** and **the winding direction**. For two outlines that are the same closed curve but stored differently — a CAD copy, a mirror-paste, a rotate-paste, or simply a re-drawn outline — the two 64-point grids are phase-shifted relative to each other.

On smooth or straight stretches a phase shift barely moves the symmetric Chamfer (each sample's nearest neighbour lies on the same edge). At a **sharp corner** it does: a sample that lands exactly on the corner in grid A has its nearest neighbour in grid B sitting partway down an adjacent edge, a perpendicular distance that scales with the sample spacing (~5 mm at N=64 on a 323 mm perimeter). Averaged over the cloud this leaves a residual that, for a real substrate outline, lands around 0.2–1.8 mm — straddling the 0.2 mm tolerance. That is the false near-miss.

## Goals / Non-Goals

**Goals:**
- Make single-entity Chamfer matching invariant to stored vertex order and winding, so geometrically identical outlines always match regardless of how the DXF stored them.
- Zero change to match/near-miss outcomes for shapes that already align (no regression).

**Non-Goals:**
- No change to translation/rotation/mirror/scale invariance (already handled by centering + PCA + 4 sign variants).
- No change to `TOLERANCE_ABS`, `RESAMPLE_N`, `SCALE_MIN/MAX`, or the signature pre-filters.
- No change to the multi-entity (pose-based) matcher — it does not compute Chamfer.
- Not attempting to match a genuine *mirror of a chiral outline* (a flipped pin-1 notch is a physically different part); that correctly stays a near-miss.

## Decisions

### D1. Canonicalize the resample start at the furthest-from-centroid vertex

```python
def _canonical_start(points):
    c = points.mean(axis=0)
    return int(np.argmax(((points - c) ** 2).sum(axis=1)))

def _resample_canonical(points, n):
    i = _canonical_start(points)
    return _resample_arclength(points if i == 0 else np.roll(points, -i, axis=0), n)
```

The furthest-from-centroid vertex is a **corner** for any substrate/component outline, and it is determined by geometry alone — independent of which vertex the DXF happened to store first or which way the polyline winds. Anchoring both template and candidate there makes their sample grids coincide, so an exact copy scores Chamfer ~0.

**Why furthest-from-centroid and not the first PCA-axis extreme?** PCA axis *sign* is ambiguous (the matcher already enumerates the 4 sign variants for exactly that reason), so a PCA-extreme anchor would itself need disambiguation. Distance-from-centroid is sign-free and needs none.

**Why this is safe under corner ties (e.g. a square-ish outline).** When several corners are equidistant from the centroid, `argmax` (stable, first-wins) may pick *different* corners for the template and a reversed/rolled copy. Those corners differ by a rectangle symmetry — a rotation/reflection already covered by the downstream 4 sign-variant search — so the alignment still lands at Chamfer ~0. Verified empirically: an 86×75 rectangle matches under translate / roll / reverse-winding / 180° / mirror, all at K=1 anchor.

### D2. Apply only where Chamfer is computed

`_resample_canonical` replaces `_resample_arclength` in `_match_single_serial` (template-side, computed once; and per candidate) and in `align_score`. `_match_multi` is pose/fingerprint based and never resamples for Chamfer; `_match_signature_mode` is a signature gate only. Both are left untouched.

### D3. No new tolerance, no escalation path

Because the anchor change is a strict improvement (identical shapes → lower Chamfer; different shapes → unchanged high Chamfer), there is no need for a separate "near-miss retry" path or a relaxed tolerance. The existing match/near-miss branch consumes the (now phase-stable) Chamfer directly. Verified: every existing matching test passes unchanged.

## Risks / Trade-offs

- **[Risk]** A pathological outline whose furthest-from-centroid vertex is genuinely ambiguous *and* whose ambiguous anchors are not related by a sign-variant symmetry (e.g. a 3-fold-symmetric part). → **Mitigation:** substrate/component outlines are rectangle-like (2-fold + mirror = exactly the sign-variant group). Even in the pathological case the result is no worse than today's arbitrary `points[0]` anchor, and the full matching suite (189 tests incl. rotation/mirror/density-invariance) stays green.
- **[Trade-off]** `score` values reported for currently-matching candidates shift slightly downward (tighter sampling). Acceptable — the score is a diagnostic, and outcomes are unchanged.
- **[Performance]** One `argmax` + `np.roll` per resampled cloud. Negligible next to the PCA eigendecomposition and KD-tree Chamfer already on the path.

## Migration Plan

No DB / on-disk migration. Stale prematch JSON regenerates on the next prematch run (re-upload, or a live Scan All click), at which point the affected substrates resolve to matches.

## Open Questions

None.

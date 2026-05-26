## 1. Fingerprint helper + drawing-level bucket cache

- [x] 1.1 Add `FINGERPRINT_DIGITS = 6` module constant in `app/matching.py` with the rationale (mm-unit DXF coordinate resolution; one digit tighter than `CIRCLE_RADIUS_KEY_DIGITS` because we compose three signals)
- [x] 1.2 Add private `_fingerprint(shape: EntityShape) -> tuple[int, int, int]` that returns the rounded `(path_length, radius, sigma_ratio)` triple as integers (multiply-then-round to keep float keys away from dict hashing)
- [x] 1.3 Add module-level `_fingerprint_bucket_cache: dict[int, dict[tuple, list[str]]]` and `_get_fingerprint_buckets(drawing) -> dict[tuple, list[str]]` mirroring the lifetime contract of `_get_radius_buckets`
- [x] 1.4 Add unit test that two `_match_multi` calls against the same `drawing` dict reuse the bucket cache (the cache key is `id(drawing)`); a fresh dict invalidates the cache

## 2. Rewrite `_match_multi` to fingerprint-bucket + rigid-transform

- [x] 2.1 In `_match_multi`, compute the seed as `min(template_shapes, key=lambda t: len(_get_fingerprint_buckets(drawing).get(_fingerprint(t), [])))` — bucket-size-based rarity replaces the old `candidate_count`
- [x] 2.2 Encode `others_local` exactly as before: each other template entity's centroid in the seed's PCA-local frame
- [x] 2.3 Replace the `for cand_handle in handles` loop with `for cand_handle in buckets[_fingerprint(seed)]`; skip handles in `skip`
- [x] 2.4 For each candidate seed, compute `cand_axes` and iterate the 4 sign variants, building `R = (cand_axes * signs).T @ seed_axes` (or equivalent — preserve the existing 4-variant semantics)
- [x] 2.5 For each `other` template entity, compute `expected = local_pos @ scaled_axes + cand.centroid` (as today); then `dist, idx = tree.query(expected, k=1)`; reject when `dist > CENTROID_NOISE_TOL` (new module constant, `1e-6`)
- [x] 2.6 Verify `_fingerprint(drawing[handles[idx]]) == _fingerprint(other_template)` before accepting the hit; reject otherwise
- [x] 2.7 Skip candidates already in `matched_handles` or in `skip`; on any miss set `consistent = False` and break
- [x] 2.8 On consistent, emit `MatchResult(handles=sorted(matched_handles), score=0.0, scale=1.0)` (chamfer is gone; score is fixed at 0.0 for the rigid path); dedupe via `seen_groups` as today
- [x] 2.9 Delete the `align_score(t.points, drawing[h].points)` call site in the inner loop — no chamfer on the multi path

## 3. Tests

- [x] 3.1 Run `pytest tests/test_matching.py -q` and confirm the three existing parity tests (`triangle`, `four_pad_smd`, `dense_neighbours`) still pass; `score` is now exactly `0.0` so update `< PARITY_SCORE_TOL` to `== 0.0` if needed (they're already `< 1e-9`, so `== 0.0` is strictly tighter)
- [x] 3.2 Flip `test_match_multi_wrong_shape_seed_rejected` from `@pytest.mark.xfail(...)` back to a plain test — the new matcher rejects it via fingerprint mismatch
- [x] 3.3 Add `test_match_multi_mirrored_pattern_matches` — a multi-entity template plus a mirrored copy in the drawing; assert one match with the mirrored copy's handles
- [x] 3.4 Add `test_fingerprint_bucket_cache_reuses_per_drawing_identity` — call `_get_fingerprint_buckets` twice on the same dict, assert object identity of the returned bucket dict; rebuild the drawing dict and assert a new bucket dict object is returned
- [x] 3.5 Add `test_match_multi_reports_scale_exactly_one` — pin `MatchResult.scale == 1.0` for the rigid path (not just within tolerance)
- [x] 3.6 Run the full test suite (`pytest tests/ -q`) and confirm no unrelated regressions

## 4. Real-DXF parity audit

- [x] 4.1 Write a one-shot script (kept out of the test suite) that iterates `data/uploads/*.dxf`, runs scan-all under BOTH the old (git-stashed) and new `_match_multi`, and diffs match counts per class per file
- [x] 4.2 For any file where the new matcher returns fewer matches than the old one, dump the missing-match handle sets and inspect — confirm they were tolerance-edge fuzzy matches (genuinely "shouldn't have matched" under the rigid contract), not bit-identical copies that fingerprint-collision missed
- [x] 4.3 If real bit-identical copies are missed, document in design.md (or escalate to the user) before merging — the fingerprint precision may need a tweak

## 5. Document and ship

- [x] 5.1 Update `_match_multi`'s docstring to describe the rigid-transform / fingerprint-bucket model and call out the degenerate-PCA (square pad) limitation
- [x] 5.2 Quick perf sanity check: synthetic 4-pad-pattern grid scan, baseline vs new, report ratio in the commit message
- [x] 5.3 Commit + push following the project's commit-message convention (`Matching: rigid-transform fingerprint-bucket multi-match (…)`)
- [ ] 5.4 Run `openspec archive multi-match-rigid-fingerprint-bucket` once the user confirms the change is shipped and validated

## Audit results

| scenario | old | new | speedup |
|---|---|---|---|
| Synthetic 4-pad grid 10×10 | 143.3 ms · 77 matches | 6.5 ms · 77 matches (identical handle sets) | **22×** |
| Real DXF `f7683af846df4d15` (250 prims, 2-entity tmpl) | 64.3 ms · 94 matches | 5.6 ms · 63 matches | **11×** |

Real-DXF delta (94 → 63 matches): OLD produced sliding-window overlapping
pairs under `pos_tol = 0.4` (6 handle reuses in first 10 matches). NEW
returns exact-position non-overlapping matches — the intentional
behavioural break documented in proposal.md.

## 1. EntityShape kind plumbing

- [x] 1.1 Add `kind: str | None` field to `EntityShape` dataclass in `app/matching.py`; default `None`.
- [x] 1.2 Update `EntityShape.from_points(handle, points, *, kind=None)` to accept and forward the kind into the dataclass.
- [x] 1.3 Update `build_entity_shapes` in `app/matching.py` to read each handle's primitives via `handle_index`, derive `kind` as the shared `type` value when all primitives agree, else `None`, and pass it into `EntityShape.from_points`.
- [x] 1.4 Add a unit test (in `tests/test_matching.py` or a new `tests/test_matching_circle_fast_path.py`) verifying `kind == "circle"` for a CIRCLE-only handle, `"polyline"` for a polyline-only handle, and `None` for a mixed handle.

## 2. Template persists per-entity kind

- [x] 2.1 Add `entity_kinds: list[str | None]` field to the `Template` dataclass in `app/library.py`. The list MUST be the same length as `entity_point_sets`.
- [x] 2.2 Add an `entity_kinds TEXT` column to the `templates` schema in `app/library.py`. Add a migration step in `LibraryStore.__init__` mirroring the existing `PRAGMA table_info(templates)` pattern: when the column is missing, `ALTER TABLE templates ADD COLUMN entity_kinds TEXT` with default NULL.
- [x] 2.3 Update `LibraryStore.add_template` to JSON-encode `entity_kinds` and write it; legacy NULL on read parses to `[None] * len(entity_point_sets)`.
- [x] 2.4 Add `collect_entity_kinds(primitives, handle_index, handle) -> str | None` in `app/library.py` next to `collect_entity_points`, applying the same "all primitives agree" rule.
- [x] 2.5 Update `Template.from_entities(class_name, entity_point_sets, entity_kinds=None)` to accept the parallel kinds list; when omitted, default to `[None] * len(entity_point_sets)` so existing callers keep compiling.
- [x] 2.6 Update the `commit` handler in `app/main.py` to capture `entity_kinds = [collect_entity_kinds(primitives, handle_index, h) for h in req.handles]` and pass it into `Template.from_entities`.

## 3. Radius-bucket fast path

- [x] 3.1 Add `CIRCLE_RADIUS_KEY_DIGITS = 10` to the tunables block in `app/matching.py` with a comment noting the relationship to upstream `_detect_circle_subpath` precision.
- [x] 3.2 Add `_radius_bucket_key(r: float) -> int` returning `round(r * 10 ** CIRCLE_RADIUS_KEY_DIGITS)`. Integer return so it hashes identically across bit-identical floats and tiny FP-noise floats that round to the same bucket.
- [x] 3.3 Add a module-level cache `_radius_bucket_cache: dict[int, dict[int, list[str]]]` keyed by `id(drawing_shapes_dict)`. Bucket dict value: `{key: [handle, ...]}` over every entry where `kind == "circle"`.
- [x] 3.4 Add `_get_radius_buckets(drawing)` that looks the cache up by `id(drawing)`, computes the bucket dict on miss, stores it, and returns it.
- [x] 3.5 Implement `_match_single_circle(template, drawing, skip)` in `app/matching.py` returning `MatchOutput`. It SHALL:
    - Compute `key = _radius_bucket_key(template.radius)`.
    - `hits = _get_radius_buckets(drawing).get(key, [])`.
    - Return `MatchOutput(matches=[MatchResult(handles=[h], score=0.0, scale=1.0) for h in hits if h not in skip], near_misses=[])`.
- [x] 3.6 Update `find_matches` dispatch: when `len(template_shapes) == 1` AND `template_shapes[0].kind == "circle"` AND `template_shapes[0].radius > 0`, call `_match_single_circle(template_shapes[0], drawing_shapes, template_handle_set)`; else current `_match_single`.
- [x] 3.7 Update `find_matches_from_pointsets` to accept `entity_kinds: list[str | None] | None = None`. Build virtual `EntityShape`s with the matching kind (or `None` when not supplied). Dispatch as in 3.6 with `skip = set()`.
- [x] 3.8 Update callers in `app/main.py`: `scan_all` and `save_match_json` pass `tmpl.entity_kinds` into `find_matches_from_pointsets`. The `match` endpoint already uses `find_matches` so 3.6 covers it automatically.

## 4. Verification

- [x] 4.1 Benchmark: capture wall-time for "frame-select 1 BGA ball + scan" on the same DXF that produced the 68 s / 381,806-match number cited in the proposal. Record both pre- and post-change numbers in a comment at the top of `tests/test_matching_circle_fast_path.py`.
- [x] 4.2 Exact-radius test: build a synthetic drawing with one template circle of radius `r_t` plus 5 same-radius circles plus 3 different-radius circles. Assert `find_matches([h_t], drawing)` returns exactly the 5 same-radius handles, no near-misses.
- [x] 4.3 Mixed-radius exclusion: same drawing as 4.2; assert that a 1.0-mm circle template does not match a 1.001-mm circle (different bucket).
- [x] 4.4 FP-noise tolerance: build two virtual circles whose radii differ by 1e-12 (well below `10 ** -CIRCLE_RADIUS_KEY_DIGITS`). Assert they share a bucket and match.
- [x] 4.5 Skip set: assert that the template's own handle (in `find_matches`) and any explicitly-passed skip handles never appear in the returned matches.
- [x] 4.6 No NearMiss invariant: assert `MatchOutput.near_misses == []` for the circle fast path under every scenario in 4.2–4.5.
- [x] 4.7 Legacy template fallback: load a `Template` with `entity_kinds=[None]` whose point set describes a circle; confirm `find_matches_from_pointsets` runs the generic path and still returns the expected matches (no fast-path speed-up, no regression).
- [x] 4.8 Cache invalidation: build a drawing, scan once, mutate the drawing dict to a new object with different circles, scan again, confirm the second scan reflects the new state (new `id(drawing)` → new cache slot).
- [x] 4.9 Run the full `pytest` suite; existing pattern-matching scenarios stay green.

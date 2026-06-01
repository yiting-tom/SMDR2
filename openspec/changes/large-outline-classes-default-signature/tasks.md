## 1. Default-config registry

- [x] 1.1 Add `CLASS_DEFAULT_MATCH_CONFIG` in `app/library.py`: `Substrate` / `LidOuter` / `LidInner` → `('signature', 0.0001)`.
- [x] 1.2 `LibraryRegistry.add_class` looks up the registry default and persists it via `update_class_strategy` when it differs from `('chamfer', None)`.
- [x] 1.3 Boot migration: after seeding `DEFAULT_CLASSES`, `UPDATE classes SET match_strategy, bbox_ratio` for the registry classes `WHERE match_strategy = 'chamfer' AND bbox_ratio IS NULL` (pristine-only, idempotent).

## 2. Tests

- [x] 2.1 `test_new_class_defaults_to_chamfer` — assert each class against its `CLASS_DEFAULT_MATCH_CONFIG` default (chamfer for most, signature for the three).
- [x] 2.2 `test_large_outline_classes_default_to_signature` — Substrate/LidOuter/LidInner seed as `('signature', 0.0001)` and persist across reload.
- [x] 2.3 `test_signature_default_preserves_explicit_override` — an explicit signature bbox_ratio (0.05) survives a reopen.
- [x] 2.4 `test_migration_adds_strategy_columns` — a pre-existing Substrate row converts to its signature default after column-add + boot migration.
- [x] 2.5 `test_api.py::test_class_listing_includes_strategy_fields` — assert per-class default via `CLASS_DEFAULT_MATCH_CONFIG`.

## 3. Suite

- [x] 3.1 `pytest tests/test_library.py -q` — 42 passed.
- [x] 3.2 `pytest -q` — 537 passed; 1 unrelated pre-existing flake (`test_save_match_post_with_missing_parsed_file_...`, confirmed failing on `main`).

## 4. Manual verification (deferred — user)

- [ ] 4.1 **[USER]** On the affected (confidential) DXF, confirm Substrate / LidOuter / LidInner now match via signature (the previously near-missed substrates register as matches). The class config UI should show signature / 0.0001 for these three.

## 5. Archive

- [ ] 5.1 After tasks 1–3 and manual verification, run `/opsx:archive large-outline-classes-default-signature`.

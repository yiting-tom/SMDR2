## 1. Bundle assembly module

- [x] 1.1 Create `app/drc_bundle.py` with a `BUNDLE_VERSION = "1.0.0"` constant and a `build_bundle(product, files, *, now=None) -> tuple[bytes, str]` entry point that returns `(zip_bytes, filename)`; `now` is injectable so tests can pin `exported_at`.
- [x] 1.2 Inside `build_bundle`, assemble `manifest.json` with `bundle_version`, `product_id`, optional `product_name` (when set on the product), `exported_at` (UTC ISO-8601 second precision), and one `files[]` entry per `FileRecord` carrying `role`, `file_id`, `dxf = "dxfs/{file_id}.dxf"`, `match_json = "match/{file_id}.json"`.
- [x] 1.3 Write the zip via `zipfile.ZipFile` over a `BytesIO`: `manifest.json` at the root, then every DXF byte-copied from `data/uploads/{file_id}.dxf` into `dxfs/{file_id}.dxf`, then every Match JSON byte-copied from `data/match/{file_id}.json` into `match/{file_id}.json`. Use `ZIP_DEFLATED` so the bundle is reasonably small.
- [x] 1.4 Add a `MANIFEST_FILENAME = "manifest.json"` plus `DXF_DIR = "dxfs"` / `MATCH_DIR = "match"` module constant so paths are single-sourced and reused by tests.

## 2. HTTP endpoint

- [x] 2.1 Add `GET /api/products/{product_id}/drc-bundle` to `app/main.py`. The handler resolves the product via `PRODUCT_STORE.get` (404 if missing) and pulls every role-attached `FileRecord` via `FILE_STORE.list_by_product`, filtering to `f.dxf_role is not None`.
- [x] 2.2 Reuse the same precondition logic as `run_product_rule_check`: 400 when no role-attached files exist; 400 with the sorted list of offending roles when any file has `match_saved == false`.
- [x] 2.3 Call `app.drc_bundle.build_bundle(product, files)`, return `Response(zip_bytes, media_type="application/zip", headers={"Content-Disposition": f"attachment; filename=drc-bundle-{product_id}.zip"})`.
- [~] 2.4 (skipped: existing `ready_for_rule_check` flag already covers availability — revisit when UI work lands) Add the new endpoint to any product-listing response field that already advertises rule-check availability, so the dashboard / API client can discover the bundle endpoint alongside `rule_check_available`. (Optional: only if a `bundle_available` flag fits the existing shape; otherwise skip and revisit when UI work lands.)

## 3. Tests

- [x] 3.1 Add `tests/test_drc_bundle.py` with a fixture that wires up an in-memory product + 1 role-attached file + a saved Match JSON, asserting `build_bundle` returns a zip whose `manifest.json` validates against `openspec/specs/design-rule-checking/drc-manifest.schema.json` (use `jsonschema` from existing deps; add it if missing).
- [x] 3.2 Test the multi-DXF-per-role case: 2 BD files + 1 each of SBT/POD/RING produces a manifest with 5 entries and 2 BD entries with distinct `file_id`s.
- [x] 3.3 Test the no-merge-prefix invariant: parse every `match/*.json` entry in the zip and assert no handle matches `^[0-9a-f]{8}:`.
- [x] 3.4 Test the byte-copy invariant: bytes inside the zip's `dxfs/*.dxf` and `match/*.json` entries equal the bytes of the source files on disk.
- [x] 3.5 Test the endpoint via FastAPI's `TestClient`: 200 + `application/zip` for a valid product; 404 for an unknown product id; 400 with a `BD` mention when one BD file has `match_saved=False`; 400 when no role-attached DXFs are uploaded.
- [x] 3.6 Test `exported_at` injection: passing a frozen `now` produces a manifest with that exact `exported_at` string.

## 4. Spec sync

- [x] 4.1 After the implementation tests pass, run `openspec validate add-drc-bundle-export --strict` to confirm the change validates.
- [ ] 4.2 When archiving, ensure the new "DRC bundle export endpoint" requirement is merged into `openspec/specs/design-rule-checking/spec.md` next to the existing "External DRC handoff bundle format" requirement.

## 5. End-to-end smoke

- [x] 5.1 Boot the app, upload at least one real multi-DXF product, hit the endpoint, unzip locally, and confirm: manifest validates against the schema; every referenced path exists; every Match JSON parses; no handles carry the merge prefix.
- [ ] 5.2 (human action: share zip with external team — implementation-side complete) Share the resulting zip with one engineer on the external rule-checking team for a sanity-check before declaring the contract live.

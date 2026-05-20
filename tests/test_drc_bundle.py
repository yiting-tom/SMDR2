"""Tests for the DRC handoff bundle (`app/drc_bundle.py` + endpoint).

These tests register stub `FileRecord`s via the real `FILE_STORE` and
write arbitrary bytes to `data/uploads/{id}.dxf` + `data/match/{id}.json`
— `build_bundle` only byte-copies these into the zip, so the DXF bytes
do not need to be a valid DXF for the assembly assertions to mean
anything. The schema-validation and endpoint tests cover the same
fixtures so we know the manifest, the precondition checks, and the
HTTP wrapper all stay in lockstep.
"""

from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_SCHEMA_PATH = (
    PROJECT_ROOT
    / "openspec"
    / "specs"
    / "design-rule-checking"
    / "drc-manifest.schema.json"
)


# Internal `<file_id[:8]>:` prefix that `run_product_rule_check` applies
# for the mock checker — MUST never leak into the exported bundle.
MERGE_PREFIX_RE = re.compile(r"^[0-9a-f]{8}:")


@pytest.fixture
def manifest_schema() -> dict:
    with MANIFEST_SCHEMA_PATH.open() as f:
        return json.load(f)


@pytest.fixture
def seeded_product():
    """Create a one-shot product + a small `seed_role(...)` helper.

    The helper registers a stub FileRecord under the product, writes the
    given bytes to disk for both the DXF and the Match JSON, and marks
    `match_saved = True`. Returns the FileRecord. Use it to build any
    role / multi-DXF shape per test.
    """
    from app.files import FILE_STORE
    from app.products import PRODUCT_STORE
    from app.storage import match_path, upload_path

    pname = f"drc-bundle-test-{uuid.uuid4().hex[:8]}"
    product = PRODUCT_STORE.create(pname, "default")
    created_file_ids: list[str] = []

    def seed_role(
        role: str,
        *,
        dxf_bytes: bytes = b"FAKE DXF PAYLOAD\n",
        match_json: dict | None = None,
        match_saved: bool = True,
    ):
        # Pure lowercase-hex id so it matches the manifest schema's
        # `^[0-9a-f]+$` pattern (real file_ids are content-hash derived).
        fid = uuid.uuid4().hex[:16]
        rec = FILE_STORE.register(
            file_id=fid,
            name=f"{role}.dxf",
            size=len(dxf_bytes),
            product_id=product.id,
            dxf_role=role,
        )
        upload_path(fid).write_bytes(dxf_bytes)
        if match_json is None:
            match_json = {"substrate.0": [["7AF"]], "smd_2t.0": [["B01"]]}
        match_path(fid).write_text(json.dumps(match_json, indent=2))
        if match_saved:
            FILE_STORE.set_match_saved(fid, True)
        created_file_ids.append(fid)
        return FILE_STORE.get(fid)

    yield product, seed_role

    # Cleanup so we don't litter `data/uploads` + `data/match` + the DB.
    for fid in created_file_ids:
        upload_path(fid).unlink(missing_ok=True)
        match_path(fid).unlink(missing_ok=True)
        with FILE_STORE.lock, FILE_STORE.conn:
            FILE_STORE.conn.execute("DELETE FROM files WHERE id = ?", (fid,))
    PRODUCT_STORE.delete(product.id)


def _open_zip(zip_bytes: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(zip_bytes))


def _read_manifest(zip_bytes: bytes) -> dict:
    from app.drc_bundle import MANIFEST_FILENAME
    with _open_zip(zip_bytes) as zf:
        return json.loads(zf.read(MANIFEST_FILENAME))


# ---- 3.1 schema validation ---------------------------------------------
def test_build_bundle_single_role_validates_against_schema(
    seeded_product, manifest_schema
):
    """A minimal one-role bundle's manifest MUST validate against
    `drc-manifest.schema.json` — this is the canonical schema contract
    we ship to the external team, so a SMDR2 export that fails the
    schema is a bug on our side."""
    from app.drc_bundle import build_bundle

    product, seed = seeded_product
    seed("BD")
    files = [seed("SBT")]  # noqa: F841 — we just need both registered
    from app.files import FILE_STORE
    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]

    zip_bytes, filename = build_bundle(product, files_list)
    assert filename == f"drc-bundle-{product.id}.zip"

    manifest = _read_manifest(zip_bytes)
    jsonschema.validate(manifest, manifest_schema)


# ---- 3.2 multi-DXF-per-role --------------------------------------------
def test_build_bundle_multi_dxf_per_role(seeded_product, manifest_schema):
    """2 BD files + 1 each of SBT/POD/RING produces a manifest with 5
    entries, of which exactly 2 carry `role: "BD"` with distinct
    `file_id`s. Also re-validates against the schema."""
    from app.drc_bundle import build_bundle
    from app.files import FILE_STORE

    product, seed = seeded_product
    seed("BD")
    seed("BD")
    seed("SBT")
    seed("POD")
    seed("RING")

    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]
    zip_bytes, _ = build_bundle(product, files_list)
    manifest = _read_manifest(zip_bytes)
    jsonschema.validate(manifest, manifest_schema)

    assert len(manifest["files"]) == 5
    bd_entries = [e for e in manifest["files"] if e["role"] == "BD"]
    assert len(bd_entries) == 2
    assert bd_entries[0]["file_id"] != bd_entries[1]["file_id"]


# ---- 3.3 no-merge-prefix invariant -------------------------------------
def test_match_json_in_bundle_carries_raw_handles(seeded_product):
    """Every handle in every `match/*.json` inside the bundle MUST be
    a raw DXF handle — no `<file_id[:8]>:` merge prefix. That's the
    whole point of shipping per-file Match JSON instead of the merged
    role-bundle form."""
    from app.drc_bundle import MATCH_DIR, build_bundle
    from app.files import FILE_STORE

    product, seed = seeded_product
    seed("BD", match_json={"top_view.substrate.0": [["65", "319B"]]})
    seed("BD", match_json={"bottom_view.smd_2t.0": [["4D", "4E"]]})

    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]
    zip_bytes, _ = build_bundle(product, files_list)

    with _open_zip(zip_bytes) as zf:
        match_names = [n for n in zf.namelist() if n.startswith(f"{MATCH_DIR}/")]
        assert match_names, "bundle must contain at least one match/*.json"
        for name in match_names:
            doc = json.loads(zf.read(name))
            for groups in doc.values():
                for group in groups:
                    for h in group:
                        assert not MERGE_PREFIX_RE.match(h), (
                            f"handle {h!r} in {name} carries the internal merge prefix; "
                            "the bundle MUST ship raw per-file handles"
                        )


# ---- 3.4 byte-copy invariant -------------------------------------------
def test_dxf_and_match_entries_are_byte_for_byte_copies(seeded_product):
    """The zip entries MUST be byte-equal to the on-disk source files —
    no transcoding, no re-serialisation. The external team relies on the
    same content hash they computed when SMDR2 first ingested the DXF."""
    from app.drc_bundle import DXF_DIR, MATCH_DIR, build_bundle
    from app.files import FILE_STORE
    from app.storage import match_path, upload_path

    product, seed = seeded_product
    seed("BD", dxf_bytes=b"DXF#one\x00\x01\x02")
    seed("SBT", dxf_bytes=b"DXF#two\xff\xfe")

    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]
    zip_bytes, _ = build_bundle(product, files_list)

    with _open_zip(zip_bytes) as zf:
        for rec in files_list:
            zip_dxf = zf.read(f"{DXF_DIR}/{rec.id}.dxf")
            zip_match = zf.read(f"{MATCH_DIR}/{rec.id}.json")
            assert zip_dxf == upload_path(rec.id).read_bytes()
            assert zip_match == match_path(rec.id).read_bytes()


# ---- 3.6 exported_at injection (out-of-order so the endpoint test can
#         lean on it for determinism) -----------------------------------
def test_exported_at_is_injectable(seeded_product):
    """Passing a frozen `now` must produce that exact `exported_at`
    string (second precision, UTC, trailing `Z`). Tests that need
    deterministic manifests use this to pin the timestamp."""
    from app.drc_bundle import build_bundle
    from app.files import FILE_STORE

    product, seed = seeded_product
    seed("BD")
    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]

    frozen = datetime(2026, 5, 19, 7, 30, 0, tzinfo=timezone.utc)
    zip_bytes, _ = build_bundle(product, files_list, now=frozen)
    manifest = _read_manifest(zip_bytes)
    assert manifest["exported_at"] == "2026-05-19T07:30:00Z"


# ---- 3.5 endpoint integration ------------------------------------------
def test_endpoint_returns_zip_on_happy_path(seeded_product, manifest_schema):
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app.main import app

    product, seed = seeded_product
    seed("BD")
    seed("SBT")
    seed("POD")
    seed("RING")

    with TestClient(app) as client:
        r = client.get(f"/api/products/{product.id}/drc-bundle")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f"drc-bundle-{product.id}.zip" in r.headers["content-disposition"]

    # Body validates as a zip + the manifest matches the schema.
    manifest = _read_manifest(r.content)
    jsonschema.validate(manifest, manifest_schema)
    assert manifest["product_id"] == product.id
    # Every referenced path is present inside the zip.
    with _open_zip(r.content) as zf:
        names = set(zf.namelist())
    for entry in manifest["files"]:
        assert entry["dxf"] in names
        assert entry["match_json"] in names

    # Hardening: the FILE_STORE.list_by_product call above is the same
    # ordering the endpoint uses, so the test stays stable as long as
    # that order doesn't change. If it does, the schema check still
    # catches structural drift.
    expected_roles = {f.dxf_role for f in FILE_STORE.list_by_product(product.id) if f.dxf_role}
    assert {e["role"] for e in manifest["files"]} == expected_roles


def test_endpoint_404_for_unknown_product():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/products/does-not-exist-xyz/drc-bundle")
    assert r.status_code == 404


def test_endpoint_400_when_match_saved_missing(seeded_product):
    """One BD file with match_saved=False — endpoint must 400 and the
    error detail must mention `BD`."""
    from fastapi.testclient import TestClient
    from app.main import app

    product, seed = seeded_product
    seed("BD", match_saved=False)
    seed("SBT")

    with TestClient(app) as client:
        r = client.get(f"/api/products/{product.id}/drc-bundle")
    assert r.status_code == 400
    assert "BD" in r.json()["detail"]


def test_endpoint_400_when_no_role_attached_files(seeded_product):
    from fastapi.testclient import TestClient
    from app.main import app

    product, _ = seeded_product   # no seed_role calls → no DXFs

    with TestClient(app) as client:
        r = client.get(f"/api/products/{product.id}/drc-bundle")
    assert r.status_code == 400


# ---- 3.6 LID configuration ---------------------------------------------
def test_build_bundle_lid_configuration_validates(seeded_product, manifest_schema):
    """A product in the LID configuration (LID instead of RING) emits
    a manifest whose `role` enum accepts `"LID"` and whose files list
    carries no `"RING"` entries."""
    from app.drc_bundle import build_bundle
    from app.files import FILE_STORE

    product, seed = seeded_product
    seed("BD")
    seed("SBT")
    seed("POD")
    seed("LID")

    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]
    zip_bytes, _ = build_bundle(product, files_list)
    manifest = _read_manifest(zip_bytes)
    jsonschema.validate(manifest, manifest_schema)

    roles = {e["role"] for e in manifest["files"]}
    assert "LID" in roles
    assert "RING" not in roles


def test_build_bundle_refuses_mixed_ring_and_lid(seeded_product):
    """If upstream data ever drifts so a product contains both RING
    and LID files, `build_manifest` MUST fail loudly — the external
    rule-checking team's contract forbids the combination."""
    from app.drc_bundle import build_bundle
    from app.files import FILE_STORE

    product, seed = seeded_product
    seed("RING")
    seed("LID")

    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]
    with pytest.raises(ValueError, match="RING and LID"):
        build_bundle(product, files_list)


# ---- 3.7 customer fields ------------------------------------------------
def test_manifest_carries_customer_for_named_library(seeded_product):
    """A product bound to the default library (`id="default"`,
    `name="Default"`) emits `customer_id="default"` and
    `customer="Default"`."""
    from app.drc_bundle import build_manifest
    from app.files import FILE_STORE

    product, seed = seeded_product
    seed("BD")

    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]
    manifest = build_manifest(product, files_list)

    assert manifest["customer_id"] == "default"
    assert manifest["customer"] == "Default"


def test_manifest_omits_customer_when_library_name_blank(seeded_product, monkeypatch):
    """If the resolved library row has an empty `name`, the manifest
    SHALL omit the `customer` key while keeping `customer_id` populated.
    Consumers needing a display name MUST tolerate the omission."""
    from app.drc_bundle import build_manifest
    from app.files import FILE_STORE
    from app.library import LIBRARIES

    product, seed = seeded_product
    seed("BD")
    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]

    # Force the registry to report an empty name without mutating the
    # real default library row (other tests share this store).
    real_get_library = LIBRARIES.store.get_library

    def fake_get_library(library_id):
        row = real_get_library(library_id)
        if row is None or library_id != product.library_id:
            return row
        return {"id": row["id"], "name": "", "created_at": row["created_at"]}

    monkeypatch.setattr(LIBRARIES.store, "get_library", fake_get_library)

    manifest = build_manifest(product, files_list)
    assert manifest["customer_id"] == product.library_id
    assert "customer" not in manifest


def test_manifest_raises_when_library_missing(seeded_product):
    """If the product references a `library_id` that no longer resolves,
    `build_manifest` MUST raise `ValueError` naming the unresolved id —
    silently emitting a customer-less manifest would corrupt the
    external team's contract."""
    from dataclasses import replace

    from app.drc_bundle import build_manifest
    from app.files import FILE_STORE

    product, seed = seeded_product
    seed("BD")
    files_list = [f for f in FILE_STORE.list_by_product(product.id) if f.dxf_role]

    bogus_product = replace(product, library_id="does-not-exist-zzz")

    with pytest.raises(ValueError, match="does-not-exist-zzz"):
        build_manifest(bogus_product, files_list)


def test_bundle_version_bumped_to_1_2_0():
    """The `customer` / `customer_id` addition is paired with a minor
    bundle_version bump so consumers can detect old bundles."""
    from app.drc_bundle import BUNDLE_VERSION

    assert BUNDLE_VERSION == "1.2.0"

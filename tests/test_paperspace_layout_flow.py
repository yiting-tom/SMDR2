"""End-to-end + unit coverage for the AutoCAD-tab (paper-space layout)
picker flow: upload → (awaiting_layout) → pick tab → awaiting_layers →
ready_to_match, plus the discover-worker gate and chosen_layout persistence.

Migrated to the product-versioning model (2026-06-10, openspec
add-product-versioning): bindings are per (version_id, file_id) and file
endpoints require ?version_id=.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import ezdxf


# ---- builders ------------------------------------------------------------
def _multi_paperspace_dxf(tmp_path: Path) -> Path:
    """Empty model space; geometry in TWO paper-space layouts (so the
    picker fires). Layout1 has a small pad grid, Layout2 a single frame."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("PAD", color=1)
    psp1 = doc.paperspace("Layout1")
    for i in range(4):
        psp1.add_circle((2 + i * 2, 5), 0.5, dxfattribs={"layer": "PAD"})
    doc.layouts.new("Layout2")
    psp2 = doc.paperspace("Layout2")
    psp2.add_lwpolyline(
        [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)],
        close=True,
        dxfattribs={"layer": "PAD"},
    )
    path = tmp_path / "multi_paperspace.dxf"
    doc.saveas(str(path))
    return path


def _real_plus_viewport_dxf(tmp_path: Path) -> Path:
    """Empty model space; ONE real-geometry layout plus a viewport-only
    framing tab (the normal DWG-export shape). The picker must NOT fire."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("PAD", color=1)
    psp1 = doc.paperspace("Layout1")
    for i in range(4):
        psp1.add_circle((2 + i * 2, 5), 0.5, dxfattribs={"layer": "PAD"})
    doc.layouts.new("Layout2")
    psp2 = doc.paperspace("Layout2")
    psp2.add_viewport(center=(400, 300), size=(800, 600),
                      view_center_point=(0, 0), view_height=600)
    path = tmp_path / "real_plus_viewport.dxf"
    doc.saveas(str(path))
    return path


def _single_paperspace_dxf(tmp_path: Path) -> Path:
    """Empty model space; geometry in ONE paper-space layout (no picker —
    auto-fallback handles it)."""
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    doc.layers.add("PAD", color=1)
    psp = doc.paperspace("Layout1")
    for i in range(4):
        psp.add_circle((2 + i * 2, 5), 0.5, dxfattribs={"layer": "PAD"})
    path = tmp_path / "single_paperspace.dxf"
    doc.saveas(str(path))
    return path


# ---- helpers (mirror test_layer_preview.py) ------------------------------
def _new_version(client, name: str) -> tuple[str, str]:
    """Create a product + first version; return (pid, vid)."""
    r = client.post("/api/products", json={"name": name, "version_label": "v1"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], body["versions"][0]["id"]


def _poll_status(client, version_id, file_id, target, *, timeout_s=20.0):
    start = time.monotonic()
    data = None
    while time.monotonic() - start < timeout_s:
        r = client.get(f"/api/files/{file_id}", params={"version_id": version_id})
        if r.status_code < 400:
            data = r.json()
            if data["status"] == target:
                return data
            if data["status"] == "error":
                raise AssertionError(f"file errored: {data.get('error')}")
        time.sleep(0.1)
    last = data["status"] if isinstance(data, dict) else "unknown"
    raise AssertionError(f"file {file_id} never reached {target!r} (last={last})")


def _upload(client, version_id, dxf_path, role="BD"):
    with open(dxf_path, "rb") as f:
        r = client.post(
            f"/api/versions/{version_id}/files",
            files={"file": (dxf_path.name, f, "image/x-dxf")},
            data={"dxf_role": role},
        )
    assert r.status_code < 400, r.text
    return r.json()


def _version_payload(client, pid, vid) -> dict:
    g = client.get(f"/api/products/{pid}")
    assert g.status_code == 200, g.text
    for v in g.json()["versions"]:
        if v["id"] == vid:
            return v
    raise AssertionError(f"version {vid} not in product {pid}")


# ---- discover-worker gate (unit, no process pool) ------------------------
def test_discover_worker_parks_for_pick_on_multi_layout(tmp_path):
    from app import jobs

    dxf = _multi_paperspace_dxf(tmp_path)
    preview = tmp_path / "preview"
    result = jobs._discover_layers_worker("fx", str(dxf), str(preview))
    assert result.get("needs_layout_pick") is True
    assert result["layout_count"] == 2
    manifest = json.loads((preview / "layouts" / "layouts.json").read_text())
    names = {L["name"] for L in manifest["layouts"]}
    assert names == {"Layout1", "Layout2"}
    for L in manifest["layouts"]:
        svg = (preview / "layouts" / L["svg_filename"]).read_text()
        assert svg.startswith("<svg")
    # Layer manifest must NOT be written yet — we're gated on the tab pick.
    assert not (preview / "layers.json").exists()


def test_discover_worker_no_pick_for_single_layout(tmp_path):
    from app import jobs

    dxf = _single_paperspace_dxf(tmp_path)
    preview = tmp_path / "preview"
    result = jobs._discover_layers_worker("fx", str(dxf), str(preview))
    assert not result.get("needs_layout_pick")
    assert result["source_layout"] == "Layout1"
    # Layer manifest written; no layout picker.
    assert (preview / "layers.json").exists()
    assert not (preview / "layouts" / "layouts.json").exists()


def test_discover_worker_ignores_viewport_only_framing_tab(tmp_path):
    """A real-geometry layout + a viewport-only framing tab is NOT ambiguous:
    the framing tab counts as zero content, so no picker — straight to layers."""
    from app import jobs

    dxf = _real_plus_viewport_dxf(tmp_path)
    preview = tmp_path / "preview"
    result = jobs._discover_layers_worker("fx", str(dxf), str(preview))
    assert not result.get("needs_layout_pick")
    assert result["source_layout"] == "Layout1"
    assert (preview / "layers.json").exists()
    assert not (preview / "layouts" / "layouts.json").exists()


def test_discover_worker_explicit_layout_skips_gate(tmp_path):
    """Re-running with an explicit tab choice must not re-trigger the picker
    even though >1 layout has geometry."""
    from app import jobs

    dxf = _multi_paperspace_dxf(tmp_path)
    preview = tmp_path / "preview"
    result = jobs._discover_layers_worker(
        "fx", str(dxf), str(preview), layout_name="Layout2"
    )
    assert not result.get("needs_layout_pick")
    assert result["source_layout"] == "Layout2"
    assert (preview / "layers.json").exists()


# ---- chosen_layout persistence (FileStore) -------------------------------
def test_filestore_round_trips_chosen_layout(tmp_path):
    from app.files import FileStore

    fs = FileStore(tmp_path / "lib.sqlite")
    fs.register_content("f1", "a.dxf", 10)
    fs.bind("v1", "BD", "f1")
    assert fs.get("v1", "f1").chosen_layout is None
    fs.set_chosen_layout("v1", "f1", "Layout2")
    assert fs.get("v1", "f1").chosen_layout == "Layout2"
    assert fs.get("v1", "f1").to_dict()["chosen_layout"] == "Layout2"
    fs.set_chosen_layout("v1", "f1", None)
    assert fs.get("v1", "f1").chosen_layout is None


def test_pre_versioning_db_is_rebuilt_from_scratch(tmp_path):
    """The column-by-column legacy migration (chosen_layout ADD COLUMN)
    died with the versioned schema: decision C9 (openspec
    add-product-versioning, 2026-06-10) rebuilds pre-versioning DBs from
    scratch, preserving no data. chosen_layout is part of the
    version_files base schema now (round-trip covered above); this test
    locks in the rebuild path."""
    import sqlite3

    from app.files import FileStore

    db = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(str(db))
    # Minimal pre-versioning shape: files carries product_id/dxf_role.
    conn.executescript("""
        CREATE TABLE files (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            size        INTEGER NOT NULL,
            uploaded_at REAL NOT NULL,
            status      TEXT NOT NULL,
            product_id  TEXT,
            dxf_role    TEXT
        );
    """)
    conn.execute(
        "INSERT INTO files (id, name, size, uploaded_at, status, product_id, dxf_role) "
        "VALUES ('old', 'o.dxf', 1, 1.0, 'ready_to_match', 'p1', 'SBT')"
    )
    conn.commit()
    conn.close()

    # Re-open via FileStore → the dbschema guard drops the legacy tables and
    # the versioned shape is created fresh.
    fs = FileStore(db)
    assert not fs.content_exists("old"), "legacy rows must not survive C9 rebuild"
    file_cols = {r[1] for r in fs.conn.execute("PRAGMA table_info(files)")}
    assert "product_id" not in file_cols
    vf_cols = {r[1] for r in fs.conn.execute("PRAGMA table_info(version_files)")}
    assert "chosen_layout" in vf_cols
    # And the rebuilt store is writable per binding.
    fs.register_content("new", "n.dxf", 1)
    fs.bind("v1", "BD", "new")
    fs.set_chosen_layout("v1", "new", "Layout1")
    assert fs.get("v1", "new").chosen_layout == "Layout1"


# ---- full pipeline through the API (process pool) ------------------------
def test_multi_layout_upload_drives_layout_then_layer_pickers(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.files import FILE_STORE
    from app.storage import parsed_path

    dxf = _multi_paperspace_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-layout")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            # Geometry in >1 paper-space layout → park for a tab pick.
            _poll_status(client, vid, fid, "awaiting_layout")

            r = client.get(f"/api/files/{fid}/layouts", params={"version_id": vid})
            assert r.status_code < 400, r.text
            payload = r.json()
            names = {L["name"] for L in payload["manifest"]["layouts"]}
            assert names == {"Layout1", "Layout2"}
            assert payload["chosen_layout"] is None

            # Thumbnails served.
            safe = payload["manifest"]["layouts"][0]["safe_name"]
            svg = client.get(
                f"/api/files/{fid}/layout-preview/{safe}.svg",
                params={"version_id": vid},
            )
            assert svg.status_code == 200
            assert svg.text.startswith("<svg")

            # Pick Layout2 → chains into layer discovery.
            r = client.post(
                f"/api/files/{fid}/layouts", json={"layout": "Layout2"},
                params={"version_id": vid},
            )
            assert r.status_code < 400, r.text
            assert r.json()["chosen_layout"] == "Layout2"

            _poll_status(client, vid, fid, "awaiting_layers")
            layers = client.get(
                f"/api/files/{fid}/layers", params={"version_id": vid},
            ).json()
            assert layers["manifest"]["layers"], "Layout2 must expose layers"

            # Confirm layers → ready.
            chosen_layers = [l["name"] for l in layers["manifest"]["layers"]]
            client.post(
                f"/api/files/{fid}/layers", json={"layers": chosen_layers},
                params={"version_id": vid},
            )
            _poll_status(client, vid, fid, "ready_to_match")

            rec = FILE_STORE.get(vid, fid)
            assert rec.chosen_layout == "Layout2"
            parsed = json.loads(parsed_path(vid, fid).read_text())
            assert parsed["source_layout"] == "Layout2"

            # has_layout_options surfaces in the version payload for re-pick.
            v = _version_payload(client, pid, vid)
            file_dict = v["files_by_role"]["BD"]
            assert file_dict["has_layout_options"] is True
            assert file_dict["chosen_layout"] == "Layout2"
        finally:
            client.delete(f"/api/products/{pid}")


def test_layout_repick_invalidates_saved_match(tmp_path):
    """Switching tabs swaps the whole entity set, so a saved Match JSON for
    the old tab must be cleared on re-pick (else rule-check runs on stale
    handles)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.files import FILE_STORE
    from app.storage import match_path

    dxf = _multi_paperspace_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-repick")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            _poll_status(client, vid, fid, "awaiting_layout")
            client.post(
                f"/api/files/{fid}/layouts", json={"layout": "Layout1"},
                params={"version_id": vid},
            )
            _poll_status(client, vid, fid, "awaiting_layers")
            layers = client.get(
                f"/api/files/{fid}/layers", params={"version_id": vid},
            ).json()
            names = [l["name"] for l in layers["manifest"]["layers"]]
            client.post(
                f"/api/files/{fid}/layers", json={"layers": names},
                params={"version_id": vid},
            )
            _poll_status(client, vid, fid, "ready_to_match")

            # Simulate a completed Save Match on Layout1.
            mp = match_path(vid, fid)
            mp.parent.mkdir(parents=True, exist_ok=True)
            mp.write_text("{}")
            FILE_STORE.set_match_saved(vid, fid, True)
            assert FILE_STORE.get(vid, fid).match_saved is True

            # Re-pick a DIFFERENT tab → the stale match must be dropped.
            r = client.post(
                f"/api/files/{fid}/layouts", json={"layout": "Layout2"},
                params={"version_id": vid},
            )
            assert r.status_code < 400, r.text
            assert FILE_STORE.get(vid, fid).match_saved is False
            assert not mp.exists()
        finally:
            client.delete(f"/api/products/{pid}")


def test_awaiting_layout_blocks_unit_override(tmp_path):
    """unit-override must refuse a binding that hasn't picked its tab yet,
    mirroring reprocess-all's skip — otherwise it auto-resolves a tab the
    operator never chose. (The old library-swap PATCH /api/files/{fid}
    guard is gone — library CRUD removed 2026-06-10, openspec
    add-product-versioning.)"""
    from fastapi.testclient import TestClient
    from app.main import app

    dxf = _multi_paperspace_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-guard")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            _poll_status(client, vid, fid, "awaiting_layout")
            assert client.post(
                f"/api/files/{fid}/unit-override", json={"unit": "mm"},
                params={"version_id": vid},
            ).status_code == 409
            # Still parked, nothing auto-resolved.
            g = client.get(f"/api/files/{fid}", params={"version_id": vid})
            assert g.json()["status"] == "awaiting_layout"
        finally:
            client.delete(f"/api/products/{pid}")


def test_single_layout_upload_skips_layout_picker(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.files import FILE_STORE

    dxf = _single_paperspace_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-single")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            # Single content layout → auto-fallback, straight to layer pick.
            _poll_status(client, vid, fid, "awaiting_layers")
            # No layout picker manifest.
            assert client.get(
                f"/api/files/{fid}/layouts", params={"version_id": vid},
            ).status_code == 404

            layers = client.get(
                f"/api/files/{fid}/layers", params={"version_id": vid},
            ).json()
            chosen_layers = [l["name"] for l in layers["manifest"]["layers"]]
            client.post(
                f"/api/files/{fid}/layers", json={"layers": chosen_layers},
                params={"version_id": vid},
            )
            _poll_status(client, vid, fid, "ready_to_match")

            # Auto-resolved tab is stamped so the badge shows it.
            rec = FILE_STORE.get(vid, fid)
            assert rec.chosen_layout == "Layout1"
            v = _version_payload(client, pid, vid)
            assert v["files_by_role"]["BD"]["has_layout_options"] is False
        finally:
            client.delete(f"/api/products/{pid}")

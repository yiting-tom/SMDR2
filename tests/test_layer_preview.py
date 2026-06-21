"""Unit + integration tests for the layer-preview / filter feature.

Covers tasks 8.1–8.6 (8.7 is a browser smoke test outside pytest's scope).
Migrated to the product-versioning model (2026-06-10, openspec
add-product-versioning): artifact paths key on (version_id, file_id) and
file endpoints require ?version_id=.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import ezdxf
import pytest

from app.dxf import filter_primitives, sanitize_layer_name


# ---- 8.1: filter_primitives ---------------------------------------------
def test_filter_primitives_drops_non_selected_layers():
    prims = [
        {"layer": "BD", "type": "line"},
        {"layer": "SMD", "type": "line"},
        {"layer": "SILK", "type": "line"},
    ]
    out = filter_primitives(prims, ["BD", "SMD"])
    layers = {p["layer"] for p in out}
    assert layers == {"BD", "SMD"}


def test_filter_primitives_treats_missing_layer_as_zero():
    prims = [{"type": "line"}, {"layer": "BD", "type": "line"}]
    out = filter_primitives(prims, ["0"])
    assert len(out) == 1
    assert out[0].get("layer") in (None, "0")


def test_filter_primitives_filters_decoratives_too():
    """Decorative primitives obey the same layer filter — if their
    host layer is excluded, they're dropped."""
    prims = [
        {"layer": "BD", "type": "line"},
        {"layer": "SILK", "type": "polyline", "decorative": True},
    ]
    out = filter_primitives(prims, ["BD"])
    assert len(out) == 1
    assert out[0]["layer"] == "BD"


def test_filter_primitives_empty_filter_drops_everything():
    """Empty filter (caller is responsible for rejecting upstream) drops
    every primitive — no silent fallback."""
    prims = [{"layer": "BD"}, {"layer": "SMD"}]
    assert filter_primitives(prims, []) == []


# ---- 8.2: sanitize_layer_name -------------------------------------------
@pytest.mark.parametrize("name", [
    "simple",
    "with space",
    "with/slash",
    "with.dot",
    "weird:?<>|*chars",
    "中文層",
    "0",
])
def test_sanitize_layer_name_round_trips(name):
    import urllib.parse
    safe = sanitize_layer_name(name)
    # Filename-safe: only ascii alnum + URL-encoding punctuation.
    assert all(c.isascii() for c in safe)
    assert "/" not in safe and " " not in safe
    # Decoding restores the original.
    assert urllib.parse.unquote(safe) == name


def test_sanitize_layer_name_empty_yields_placeholder():
    assert sanitize_layer_name("") == "_unnamed"


# ---- Helpers for the integration tests below ----------------------------
def _build_synth_dxf(tmp_path: Path) -> Path:
    """Synthetic DXF with three real layers (BD / SMD / SILK) plus the
    AutoCAD default '0' layer that ezdxf always emits."""
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    doc.layers.add("BD", color=7)
    doc.layers.add("SMD", color=1)
    doc.layers.add("SILK", color=3)
    msp.add_lwpolyline(
        [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
        dxfattribs={"layer": "BD", "closed": True},
    )
    for i in range(4):
        msp.add_circle((2 + i * 2, 5), 0.5, dxfattribs={"layer": "SMD"})
    # SILK needs at least one non-text entity so layer discovery picks
    # it up: SMDR2 renders with `TextPolicy.IGNORE`, so a TEXT-only
    # layer produces zero primitives and gets dropped from the manifest.
    # A real silk-screen layer in production DXFs has both — line / arc
    # geometry plus annotation TEXT — so this matches actual file shape.
    msp.add_lwpolyline(
        [(0.5, 0.5), (1.5, 0.5)],
        dxfattribs={"layer": "SILK"},
    )
    msp.add_text("LABEL", dxfattribs={
        "insert": (1, 1), "height": 0.5, "layer": "SILK"
    })
    path = tmp_path / "synth.dxf"
    doc.saveas(str(path))
    return path


def _new_version(client, name: str) -> tuple[str, str]:
    """Create a product + first version; return (pid, vid)."""
    r = client.post("/api/products", json={"name": name, "version_label": "v1"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], body["versions"][0]["id"]


def _poll_status(client, version_id: str, file_id: str, target: str,
                 *, timeout_s: float = 20.0):
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        r = client.get(f"/api/files/{file_id}", params={"version_id": version_id})
        if r.status_code < 400:
            data = r.json()
            if data["status"] == target:
                return data
            if data["status"] == "error":
                raise AssertionError(f"file errored: {data.get('error')}")
        time.sleep(0.1)
    last = (
        data["status"]
        if r.status_code < 400 and isinstance(data, dict)
        else "unknown"
    )
    raise AssertionError(
        f"file {file_id} never reached {target!r} (last={last})"
    )


def _upload(client, version_id: str, dxf_path: Path, role: str = "BD"):
    with open(dxf_path, "rb") as f:
        r = client.post(
            f"/api/versions/{version_id}/files",
            files={"file": (dxf_path.name, f, "image/x-dxf")},
            data={"dxf_role": role},
        )
    assert r.status_code < 400, r.text
    return r.json()


# ---- 8.3: discovery flow ------------------------------------------------
def test_phase1_emits_manifest_and_svgs(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import layer_manifest_path, layer_preview_svg_path

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-phase1")
        try:
            up = _upload(client, vid, dxf)
            fid = up["file_id"]
            assert up["status"] == "discovering_layers"

            _poll_status(client, vid, fid, "awaiting_layers")
            manifest = json.loads(layer_manifest_path(vid, fid).read_text())
            names = {l["name"] for l in manifest["layers"]}
            # ezdxf always emits the implicit "0" layer; that's fine —
            # we expect BD / SMD / SILK to be present alongside it.
            assert {"BD", "SMD", "SILK"}.issubset(names)
            for layer in manifest["layers"]:
                p = layer_preview_svg_path(vid, fid, layer["safe_name"])
                assert p.exists(), f"missing svg for {layer['name']!r}"
                assert p.read_text().startswith("<svg"), p.read_text()[:80]
        finally:
            client.delete(f"/api/products/{pid}")


# ---- 8.4: confirm-filter pipeline ---------------------------------------
def test_phase2_filters_parsed_and_prematch(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import parsed_path

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-phase2")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            _poll_status(client, vid, fid, "awaiting_layers")

            r = client.post(
                f"/api/files/{fid}/layers",
                json={"layers": ["BD", "SMD"]},
                params={"version_id": vid},
            )
            assert r.status_code < 400, r.text
            _poll_status(client, vid, fid, "ready_to_match")

            parsed = json.loads(parsed_path(vid, fid).read_text())
            assert parsed["selected_layers"] == ["BD", "SMD"]
            present = {p.get("layer") or "0" for p in parsed["primitives"]}
            assert "SILK" not in present
            assert present.issubset({"BD", "SMD"})
        finally:
            client.delete(f"/api/products/{pid}")


def test_prematch_reports_staleness_after_library_change(tmp_path):
    """`GET /prematch` reports `stale: false` for a snapshot computed against
    the current library, and `stale: true` once a template is committed after
    preprocess (fix-stale-prematch-cache)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.library import LIBRARIES, Template
    from app.versions import VERSION_STORE

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-prematch-stale")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            _poll_status(client, vid, fid, "awaiting_layers")
            r = client.post(
                f"/api/files/{fid}/layers",
                json={"layers": ["BD", "SMD"]},
                params={"version_id": vid},
            )
            assert r.status_code < 400, r.text
            _poll_status(client, vid, fid, "ready_to_match")

            # Fresh: snapshot was stamped with the current library revision.
            r = client.get(f"/api/files/{fid}/prematch", params={"version_id": vid})
            assert r.status_code < 400, r.text
            assert r.json()["stale"] is False

            # Commit a template after preprocess → library revision bumps →
            # the same snapshot is now stale.
            lib_id = VERSION_STORE.get(vid).library_id
            LIBRARIES.get(lib_id).add_template_for_file(
                Template.from_entities("SMD-2T", [[(0.0, 0.0), (1.0, 0.0)]])
            )
            r = client.get(f"/api/files/{fid}/prematch", params={"version_id": vid})
            assert r.status_code < 400, r.text
            assert r.json()["stale"] is True
        finally:
            client.delete(f"/api/products/{pid}")


def test_confirm_rejects_empty_and_unknown_layers(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-rejects")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            _poll_status(client, vid, fid, "awaiting_layers")

            assert client.post(
                f"/api/files/{fid}/layers", json={"layers": []},
                params={"version_id": vid},
            ).status_code == 400
            assert client.post(
                f"/api/files/{fid}/layers", json={"layers": ["NOPE"]},
                params={"version_id": vid},
            ).status_code == 400
            # Binding still in awaiting_layers — not advanced.
            g = client.get(f"/api/files/{fid}", params={"version_id": vid})
            assert g.json()["status"] == "awaiting_layers"
        finally:
            client.delete(f"/api/products/{pid}")


# ---- 8.5: version clone reuses selection ---------------------------------
# The library-reassign flow (PATCH /api/files/{fid} with a library_id) died
# with library CRUD (removed 2026-06-10, openspec add-product-versioning).
# The closest behavior in the versioned model: cloning a version carries
# `selected_layers` onto the new binding and re-preprocesses with it —
# the operator is NOT re-prompted for layers.
def test_version_clone_reuses_selected_layers(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.files import FILE_STORE
    from app.storage import parsed_path

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-clone-swap")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            _poll_status(client, vid, fid, "awaiting_layers")
            client.post(
                f"/api/files/{fid}/layers", json={"layers": ["BD"]},
                params={"version_id": vid},
            )
            _poll_status(client, vid, fid, "ready_to_match")

            # Clone into v2 — should reuse selected_layers, NOT re-prompt.
            r = client.post(f"/api/products/{pid}/versions", json={"label": "v2"})
            assert r.status_code < 400, r.text
            vid2 = r.json()["id"]
            _poll_status(client, vid2, fid, "ready_to_match")

            rec = FILE_STORE.get(vid2, fid)
            assert rec.selected_layers == ["BD"]
            assert rec.status != "awaiting_layers"

            parsed = json.loads(parsed_path(vid2, fid).read_text())
            present = {p.get("layer") or "0" for p in parsed["primitives"]}
            assert "SILK" not in present and "SMD" not in present
        finally:
            client.delete(f"/api/products/{pid}")


# ---- 8.6: legacy binding backward-compat ----------------------------------
def test_legacy_file_with_null_selected_layers_still_loads(tmp_path):
    """A binding already in `ready_to_match` with `selected_layers = NULL`
    and no manifest on disk MUST stay accessible — every read endpoint
    works, status doesn't auto-flip, and `GET /layers` 404s (the viewer
    UI uses that as a signal to trigger discovery)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.files import FILE_STORE, READY
    from app.storage import (
        layer_manifest_path, layer_preview_dir, parsed_path,
    )

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-legacy")
        try:
            fid = _upload(client, vid, dxf)["file_id"]
            _poll_status(client, vid, fid, "awaiting_layers")
            client.post(
                f"/api/files/{fid}/layers", json={"layers": ["BD"]},
                params={"version_id": vid},
            )
            _poll_status(client, vid, fid, "ready_to_match")

            # Simulate a legacy binding: wipe selected_layers and remove the
            # on-disk manifest, then re-confirm everything still works.
            FILE_STORE.clear_selected_layers(vid, fid)
            shutil.rmtree(layer_preview_dir(vid, fid), ignore_errors=True)
            assert not layer_manifest_path(vid, fid).exists()
            assert parsed_path(vid, fid).exists()  # parsed cache untouched

            rec = FILE_STORE.get(vid, fid)
            assert rec.status == READY
            assert rec.selected_layers is None

            # GET layers must 404 — UI uses this to know "trigger discovery".
            r = client.get(f"/api/files/{fid}/layers", params={"version_id": vid})
            assert r.status_code == 404

            # Discover endpoint kicks off Phase 1 and lands us back in
            # awaiting_layers with all layers selectable.
            r = client.post(
                f"/api/files/{fid}/discover-layers", params={"version_id": vid},
            )
            assert r.status_code < 400, r.text
            _poll_status(client, vid, fid, "awaiting_layers")
            manifest = client.get(
                f"/api/files/{fid}/layers", params={"version_id": vid},
            ).json()
            names = {l["name"] for l in manifest["manifest"]["layers"]}
            assert {"BD", "SMD", "SILK"}.issubset(names)
            assert manifest["selected_layers"] is None  # legacy
        finally:
            client.delete(f"/api/products/{pid}")

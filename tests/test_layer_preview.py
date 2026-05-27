"""Unit + integration tests for the layer-preview / filter feature.

Covers tasks 8.1–8.6 (8.7 is a browser smoke test outside pytest's scope).
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


def _poll_status(client, file_id: str, target: str, *, timeout_s: float = 20.0):
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        r = client.get(f"/api/files/{file_id}")
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


def _upload(client, product_id: str, dxf_path: Path, role: str = "BD"):
    with open(dxf_path, "rb") as f:
        r = client.post(
            f"/api/products/{product_id}/files",
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
        pid = client.post(
            "/api/products",
            json={"name": "t-phase1", "library_id": "default"},
        ).json()["id"]
        try:
            up = _upload(client, pid, dxf)
            fid = up["file_id"]
            assert up["status"] == "discovering_layers"

            _poll_status(client, fid, "awaiting_layers")
            manifest = json.loads(layer_manifest_path(fid).read_text())
            names = {l["name"] for l in manifest["layers"]}
            # ezdxf always emits the implicit "0" layer; that's fine —
            # we expect BD / SMD / SILK to be present alongside it.
            assert {"BD", "SMD", "SILK"}.issubset(names)
            for layer in manifest["layers"]:
                p = layer_preview_svg_path(fid, layer["safe_name"])
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
        pid = client.post(
            "/api/products",
            json={"name": "t-phase2", "library_id": "default"},
        ).json()["id"]
        try:
            fid = _upload(client, pid, dxf)["file_id"]
            _poll_status(client, fid, "awaiting_layers")

            r = client.post(
                f"/api/files/{fid}/layers",
                json={"layers": ["BD", "SMD"]},
            )
            assert r.status_code < 400, r.text
            _poll_status(client, fid, "ready_to_match")

            parsed = json.loads(parsed_path(fid).read_text())
            assert parsed["selected_layers"] == ["BD", "SMD"]
            present = {p.get("layer") or "0" for p in parsed["primitives"]}
            assert "SILK" not in present
            assert present.issubset({"BD", "SMD"})
        finally:
            client.delete(f"/api/products/{pid}")


def test_confirm_rejects_empty_and_unknown_layers(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/products",
            json={"name": "t-rejects", "library_id": "default"},
        ).json()["id"]
        try:
            fid = _upload(client, pid, dxf)["file_id"]
            _poll_status(client, fid, "awaiting_layers")

            assert client.post(
                f"/api/files/{fid}/layers", json={"layers": []}
            ).status_code == 400
            assert client.post(
                f"/api/files/{fid}/layers", json={"layers": ["NOPE"]}
            ).status_code == 400
            # File still in awaiting_layers — not advanced.
            assert client.get(f"/api/files/{fid}").json()["status"] == "awaiting_layers"
        finally:
            client.delete(f"/api/products/{pid}")


# ---- 8.5: library swap reuses selection ---------------------------------
def test_library_swap_reuses_selected_layers(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.files import FILE_STORE
    from app.storage import parsed_path

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/products",
            json={"name": "t-swap", "library_id": "default"},
        ).json()["id"]
        other_lib = client.post(
            "/api/libraries", json={"name": "swap-target"}
        ).json()["id"]
        try:
            fid = _upload(client, pid, dxf)["file_id"]
            _poll_status(client, fid, "awaiting_layers")
            client.post(f"/api/files/{fid}/layers", json={"layers": ["BD"]})
            _poll_status(client, fid, "ready_to_match")

            # PATCH library — should reuse selected_layers, NOT re-prompt.
            r = client.patch(f"/api/files/{fid}", json={"library_id": other_lib})
            assert r.status_code < 400, r.text
            _poll_status(client, fid, "ready_to_match")

            rec = FILE_STORE.get(fid)
            assert rec.selected_layers == ["BD"]
            assert rec.status != "awaiting_layers"

            parsed = json.loads(parsed_path(fid).read_text())
            present = {p.get("layer") or "0" for p in parsed["primitives"]}
            assert "SILK" not in present and "SMD" not in present
        finally:
            client.delete(f"/api/products/{pid}")
            client.delete(f"/api/libraries/{other_lib}")


# ---- 8.6: legacy file backward-compat ------------------------------------
def test_legacy_file_with_null_selected_layers_still_loads(tmp_path):
    """A file already in `ready_to_match` with `selected_layers = NULL`
    and no manifest on disk MUST stay accessible — every read endpoint
    works, status doesn't auto-flip, and `GET /layers` 404s (the viewer
    UI uses that as a signal to trigger discovery)."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.files import FILE_STORE, READY
    from app.storage import (
        layer_manifest_path, layer_preview_dir, parsed_path, upload_path,
    )

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/products",
            json={"name": "t-legacy", "library_id": "default"},
        ).json()["id"]
        try:
            fid = _upload(client, pid, dxf)["file_id"]
            _poll_status(client, fid, "awaiting_layers")
            client.post(f"/api/files/{fid}/layers", json={"layers": ["BD"]})
            _poll_status(client, fid, "ready_to_match")

            # Simulate a legacy file: wipe selected_layers and remove the
            # on-disk manifest, then re-confirm everything still works.
            FILE_STORE.clear_selected_layers(fid)
            shutil.rmtree(layer_preview_dir(fid), ignore_errors=True)
            assert not layer_manifest_path(fid).exists()
            assert parsed_path(fid).exists()  # parsed cache untouched

            rec = FILE_STORE.get(fid)
            assert rec.status == READY
            assert rec.selected_layers is None

            # GET layers must 404 — UI uses this to know "trigger discovery".
            r = client.get(f"/api/files/{fid}/layers")
            assert r.status_code == 404

            # Discover endpoint kicks off Phase 1 and lands us back in
            # awaiting_layers with all layers selectable.
            r = client.post(f"/api/files/{fid}/discover-layers")
            assert r.status_code < 400, r.text
            _poll_status(client, fid, "awaiting_layers")
            manifest = client.get(f"/api/files/{fid}/layers").json()
            names = {l["name"] for l in manifest["manifest"]["layers"]}
            assert {"BD", "SMD", "SILK"}.issubset(names)
            assert manifest["selected_layers"] is None  # legacy
        finally:
            client.delete(f"/api/products/{pid}")

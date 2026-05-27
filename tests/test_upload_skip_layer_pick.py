"""Tests for `POST /api/products/{pid}/files` with the dev-mode
`skip_layer_pick` form field.

The flag bypasses Phase 1 entirely: no `_discover_layers_worker`
submission, no `layers.json` / per-layer SVG render, and the
file's initial status is `PREPROCESSING` instead of
`DISCOVERING_LAYERS`. Phase 2 (`_preprocess_worker`) runs with
`selected_layers=None` (the existing "no filter" signal), so the
worker code path itself is unchanged.

The tests use the same synthetic-DXF helper as
`test_layer_preview.py` to keep the fixture surface minimal.
"""

from __future__ import annotations

import time
from pathlib import Path

import ezdxf


def _build_synth_dxf(tmp_path: Path) -> Path:
    """Minimal valid DXF — one polyline + one circle on two layers.
    Smaller than `test_layer_preview.py`'s fixture because we don't
    care about layer-discovery semantics here, just upload routing."""
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    doc.layers.add("BD", color=7)
    doc.layers.add("SMD", color=1)
    msp.add_lwpolyline(
        [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)],
        dxfattribs={"layer": "BD", "closed": True},
    )
    msp.add_circle((5, 5), 0.5, dxfattribs={"layer": "SMD"})
    path = tmp_path / "skip.dxf"
    doc.saveas(str(path))
    return path


def _build_alt_synth_dxf(tmp_path: Path, name: str = "alt.dxf") -> Path:
    """Different bytes from `_build_synth_dxf` so the file_id differs.
    Used in the dedup test which needs two distinct (file_id, role)
    pairs that share the same content."""
    doc = ezdxf.new("R2010", setup=True)
    msp = doc.modelspace()
    doc.layers.add("BD", color=7)
    msp.add_line((0, 0), (50, 50), dxfattribs={"layer": "BD"})
    path = tmp_path / name
    doc.saveas(str(path))
    return path


def _upload(client, product_id, dxf_path, *, role="BD",
            skip_layer_pick=None):
    """Wrapper around `POST /api/products/{pid}/files` that optionally
    appends the dev-mode `skip_layer_pick` form field."""
    data = {"dxf_role": role}
    if skip_layer_pick is not None:
        data["skip_layer_pick"] = "true" if skip_layer_pick else "false"
    with open(dxf_path, "rb") as f:
        r = client.post(
            f"/api/products/{product_id}/files",
            files={"file": (dxf_path.name, f, "image/x-dxf")},
            data=data,
        )
    assert r.status_code < 400, r.text
    return r.json()


def _poll_status(client, file_id, target, *, timeout_s=20.0):
    """Same pattern as the layer-preview tests: poll until the file's
    status reaches `target` or the worker errors out."""
    start = time.monotonic()
    last = "unknown"
    while time.monotonic() - start < timeout_s:
        r = client.get(f"/api/files/{file_id}")
        if r.status_code < 400:
            data = r.json()
            last = data["status"]
            if last == target:
                return data
            if last == "error":
                raise AssertionError(f"file errored: {data.get('error')}")
        time.sleep(0.1)
    raise AssertionError(
        f"file {file_id} never reached {target!r} (last={last})"
    )


# ---- 1. skip_layer_pick=true: Phase 1 bypassed --------------------------
def test_skip_layer_pick_true_routes_directly_to_preprocess(tmp_path):
    """Upload with the dev flag → response carries `preprocessing`,
    the submitted job is `preprocess` (not `discover`), and
    `selected_layers` is null. The file MUST never appear in
    `discovering_layers` / `awaiting_layers`."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app import jobs
    from app.main import app
    from app.storage import layer_manifest_path

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/products",
            json={"name": "t-skip-true", "library_id": "default"},
        ).json()["id"]
        try:
            up = _upload(client, pid, dxf, skip_layer_pick=True)
            fid = up["file_id"]
            assert up["status"] == "preprocessing", up

            # The submitted job's kind is `preprocess`, not the
            # Phase 1 `discover`.
            job = jobs._jobs[up["job_id"]]
            assert job["kind"] == "preprocess", job

            # The file row carries `selected_layers = NULL` because
            # the skip path explicitly resets it to "no filter".
            rec = FILE_STORE.get(fid)
            assert rec is not None
            assert rec.selected_layers is None

            # Wait for Phase 2 to finish; the file lands on
            # ready_to_match without ever transiting Phase 1 states.
            _poll_status(client, fid, "ready_to_match")

            # Phase 1 manifest must not exist — the skip path doesn't
            # write it.
            assert not layer_manifest_path(fid).exists(), (
                f"layer manifest unexpectedly written at "
                f"{layer_manifest_path(fid)}; skip path should bypass it"
            )
        finally:
            client.delete(f"/api/products/{pid}")


# ---- 2. baseline: skip flag absent → Phase 1 path unchanged -------------
def test_skip_flag_absent_uses_phase1_as_today(tmp_path):
    """No `skip_layer_pick` in the request → response says
    `discovering_layers`, the submitted job is `discover`. The
    Phase 1 path is untouched by the new field."""
    from fastapi.testclient import TestClient
    from app import jobs
    from app.main import app

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid = client.post(
            "/api/products",
            json={"name": "t-skip-absent", "library_id": "default"},
        ).json()["id"]
        try:
            up = _upload(client, pid, dxf)
            assert up["status"] == "discovering_layers", up
            job = jobs._jobs[up["job_id"]]
            assert job["kind"] == "discover", job
        finally:
            client.delete(f"/api/products/{pid}")


def test_skip_flag_false_uses_phase1_as_today(tmp_path):
    """Same baseline as above but with the field explicitly false —
    locks in that the truthiness check is honest."""
    from fastapi.testclient import TestClient
    from app import jobs
    from app.main import app

    dxf = _build_alt_synth_dxf(tmp_path, "skip-false.dxf")
    with TestClient(app) as client:
        pid = client.post(
            "/api/products",
            json={"name": "t-skip-false", "library_id": "default"},
        ).json()["id"]
        try:
            up = _upload(client, pid, dxf, skip_layer_pick=False)
            assert up["status"] == "discovering_layers", up
            job = jobs._jobs[up["job_id"]]
            assert job["kind"] == "discover", job
        finally:
            client.delete(f"/api/products/{pid}")


# ---- 3. dedup-rebind with skip_layer_pick=true --------------------------
def test_dedup_rebind_with_skip_flag_routes_to_phase2(tmp_path):
    """Re-upload bytes-identical content to a different product
    slot with `skip_layer_pick=true`. The existing row is rebound
    and Phase 2 is submitted directly — no Phase 1 even though
    the first upload took the Phase 1 path."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app import jobs
    from app.main import app

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        # Two products that will host the same bytes under different
        # roles. Product A goes through Phase 1 normally; product B
        # re-uses the same content with the skip flag.
        pid_a = client.post(
            "/api/products",
            json={"name": "t-dedup-a", "library_id": "default"},
        ).json()["id"]
        pid_b = client.post(
            "/api/products",
            json={"name": "t-dedup-b", "library_id": "default"},
        ).json()["id"]
        try:
            # First upload: normal Phase 1 path, ends at
            # `ready_to_match` after the operator implicitly confirms
            # all layers (we let it run to completion via the
            # awaiting_layers state). For test simplicity we don't
            # actually confirm layers — we just verify the kind, then
            # re-upload to product B with the skip flag.
            up_a = _upload(client, pid_a, dxf)
            assert up_a["status"] == "discovering_layers"
            fid = up_a["file_id"]

            # Re-upload identical bytes to a different slot with the
            # skip flag. The existing row is rebound and Phase 2 is
            # submitted directly.
            up_b = _upload(client, pid_b, dxf, role="POD",
                           skip_layer_pick=True)
            assert up_b["file_id"] == fid, "dedup must reuse file_id"
            assert up_b["status"] == "preprocessing", up_b
            job_b = jobs._jobs[up_b["job_id"]]
            assert job_b["kind"] == "preprocess", job_b

            # Row reflects the new slot + skip-path state: status
            # PREPROCESSING (or beyond, once Phase 2 finishes),
            # selected_layers reset to None, bound to product B.
            rec = FILE_STORE.get(fid)
            assert rec is not None
            assert rec.product_id == pid_b
            assert rec.dxf_role == "POD"
            assert rec.selected_layers is None
        finally:
            client.delete(f"/api/products/{pid_a}")
            client.delete(f"/api/products/{pid_b}")

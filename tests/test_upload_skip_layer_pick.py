"""Tests for `POST /api/versions/{vid}/files` with the dev-mode
`skip_layer_pick` form field.

The flag bypasses Phase 1 entirely: no `_discover_layers_worker`
submission, no `layers.json` / per-layer SVG render, and the
binding's initial status is `PREPROCESSING` instead of
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


def _new_version(client, name: str) -> tuple[str, str]:
    """Create a product + first version; return (pid, vid)."""
    r = client.post("/api/products", json={"name": name, "version_label": "v1"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], body["versions"][0]["id"]


def _upload(client, version_id, dxf_path, *, role="BD",
            skip_layer_pick=None):
    """Wrapper around `POST /api/versions/{vid}/files` that optionally
    appends the dev-mode `skip_layer_pick` form field."""
    data = {"dxf_role": role}
    if skip_layer_pick is not None:
        data["skip_layer_pick"] = "true" if skip_layer_pick else "false"
    with open(dxf_path, "rb") as f:
        r = client.post(
            f"/api/versions/{version_id}/files",
            files={"file": (dxf_path.name, f, "image/x-dxf")},
            data=data,
        )
    assert r.status_code < 400, r.text
    return r.json()


def _poll_status(client, version_id, file_id, target, *, timeout_s=20.0):
    """Same pattern as the layer-preview tests: poll until the binding's
    status reaches `target` or the worker errors out."""
    start = time.monotonic()
    last = "unknown"
    while time.monotonic() - start < timeout_s:
        r = client.get(f"/api/files/{file_id}", params={"version_id": version_id})
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
    `selected_layers` is null. The binding MUST never appear in
    `discovering_layers` / `awaiting_layers`."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app import jobs
    from app.main import app
    from app.storage import layer_manifest_path

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        pid, vid = _new_version(client, "t-skip-true")
        try:
            up = _upload(client, vid, dxf, skip_layer_pick=True)
            fid = up["file_id"]
            assert up["status"] == "preprocessing", up

            # The submitted job's kind is `preprocess`, not the
            # Phase 1 `discover`.
            job = jobs._jobs[up["job_id"]]
            assert job["kind"] == "preprocess", job

            # The binding carries `selected_layers = NULL` because
            # the skip path explicitly uses the "no filter" signal.
            rec = FILE_STORE.get(vid, fid)
            assert rec is not None
            assert rec.selected_layers is None

            # Wait for Phase 2 to finish; the binding lands on
            # ready_to_match without ever transiting Phase 1 states.
            _poll_status(client, vid, fid, "ready_to_match")

            # Phase 1 manifest must not exist — the skip path doesn't
            # write it.
            assert not layer_manifest_path(vid, fid).exists(), (
                f"layer manifest unexpectedly written at "
                f"{layer_manifest_path(vid, fid)}; skip path should bypass it"
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
        pid, vid = _new_version(client, "t-skip-absent")
        try:
            up = _upload(client, vid, dxf)
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
        pid, vid = _new_version(client, "t-skip-false")
        try:
            up = _upload(client, vid, dxf, skip_layer_pick=False)
            assert up["status"] == "discovering_layers", up
            job = jobs._jobs[up["job_id"]]
            assert job["kind"] == "discover", job
        finally:
            client.delete(f"/api/products/{pid}")


# ---- 3. dedup-bind with skip_layer_pick=true -----------------------------
def test_dedup_bind_with_skip_flag_routes_to_phase2(tmp_path):
    """Re-upload bytes-identical content into a different version's slot
    with `skip_layer_pick=true`. The content row is deduplicated (same
    file_id) and the NEW binding goes straight to Phase 2 — no Phase 1
    even though the first upload took the Phase 1 path. The first
    version's binding is untouched (bindings are per version now, so
    nothing is 'stolen')."""
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE
    from app import jobs
    from app.main import app

    dxf = _build_synth_dxf(tmp_path)
    with TestClient(app) as client:
        # Two products (each with one version) that will host the same
        # bytes under different roles. Version A goes through Phase 1
        # normally; version B re-uses the same content with the skip flag.
        pid_a, vid_a = _new_version(client, "t-dedup-a")
        pid_b, vid_b = _new_version(client, "t-dedup-b")
        try:
            # First upload: normal Phase 1 path. For test simplicity we
            # don't confirm layers — we just verify the kind, then
            # upload the same bytes to version B with the skip flag.
            up_a = _upload(client, vid_a, dxf)
            assert up_a["status"] == "discovering_layers"
            fid = up_a["file_id"]

            # Upload identical bytes to a different version's slot with
            # the skip flag. The content row is reused and Phase 2 is
            # submitted directly for the new binding.
            up_b = _upload(client, vid_b, dxf, role="POD",
                           skip_layer_pick=True)
            assert up_b["file_id"] == fid, "dedup must reuse file_id"
            assert up_b["deduped"] is True, up_b
            assert up_b["status"] == "preprocessing", up_b
            job_b = jobs._jobs[up_b["job_id"]]
            assert job_b["kind"] == "preprocess", job_b

            # Version B's binding reflects the skip-path state: status
            # PREPROCESSING (or beyond, once Phase 2 finishes), role POD,
            # selected_layers None.
            rec_b = FILE_STORE.get(vid_b, fid)
            assert rec_b is not None
            assert rec_b.dxf_role == "POD"
            assert rec_b.selected_layers is None

            # Version A's binding still exists, on its own lifecycle.
            assert FILE_STORE.get(vid_a, fid) is not None
        finally:
            client.delete(f"/api/products/{pid_a}")
            client.delete(f"/api/products/{pid_b}")

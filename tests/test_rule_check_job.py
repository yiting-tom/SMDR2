"""Tests for the rule-check background-job path.

The worker materialises the DRC handoff bundle on disk and hands the
directory to the external rule function (``app.external_rule_check``).
Rule logic is owned by the external team; these tests cover the
boundary contract:

- worker materialises the bundle layout `build_bundle_dir` writes
- worker forwards the bundle path to `check_rules` and persists the
  return value verbatim
- worker errors (external raises, bundle materialisation fails)
  surface as ``status: "error"`` without overwriting a prior result
- POST returns 202 + job_id
- ``/api/products`` carries ``latest_rule_check_job`` for dashboard
  reload-resume support
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ---- Fixture helpers -----------------------------------------------------

def _write_match(file_id: str, match_json: dict) -> Path:
    from app.storage import match_path
    mp = match_path(file_id)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(match_json))
    return mp


def _write_dxf_bytes(file_id: str, payload: bytes = b"FAKE DXF\n") -> Path:
    from app.storage import upload_path
    up = upload_path(file_id)
    up.parent.mkdir(parents=True, exist_ok=True)
    up.write_bytes(payload)
    return up


def _make_product_ready_for_drc(client, tag: str) -> tuple[str, str]:
    """Create a product with a single BD file, write a DXF + match JSON on
    disk, flip match_saved. Returns (product_id, file_id). `tag` keeps
    file_ids unique across tests so they never alias each other's
    `rule_check/{pid}.json`."""
    from app.files import FILE_STORE, READY

    cr = client.post(
        "/api/products",
        json={"name": f"drc-job-{tag}", "library_id": "default"},
    )
    assert cr.status_code == 200, cr.text
    pid = cr.json()["id"]

    fid = f"bd{tag:>06}".replace(" ", "0")[:8]
    FILE_STORE.register(
        fid, f"{fid}.dxf", 1,
        product_id=pid, dxf_role="BD", initial_status=READY,
    )
    _write_dxf_bytes(fid)
    _write_match(fid, {
        "substrate.0": [["S"]],
        "smd_2t.0": [["A"]],
    })
    FILE_STORE.set_match_saved(fid, True)
    return pid, fid


def _poll_job(client, job_id: str, timeout: float = 30.0) -> dict:
    """Poll `/api/jobs/{job_id}` until it leaves the running/queued
    states or the timeout trips. Returns the final job dict."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/jobs/{job_id}")
        assert r.status_code == 200, r.text
        job = r.json()
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.1)
    pytest.fail(f"job {job_id} did not finish within {timeout}s")


def _ok_external_result():
    return {
        "Rule1": {
            "pass": True,
            "text": "substrate-to-SMD distance check",
            "rules": [{
                "part": "BD",
                "file_id": "abc12345",
                "from": "S",
                "to":   "A",
                "text": "distance = 12.5 mm (> 5)",
                "tol":      None,
                "tol_text": None,
            }],
        },
    }


# ---- Direct worker test (in-process, monkey-patchable) ------------------

def test_worker_materialises_bundle_and_persists_external_result(tmp_path, monkeypatch):
    """End-to-end direct call of `_rule_check_worker`: register a
    product + BD file, monkey-patch the external rule function to
    return a canned result, run the worker in-process, assert the
    bundle was materialised (we capture its path via the monkey-patch)
    and the result lands on disk verbatim."""
    from app.files import FILE_STORE, READY
    from app.jobs import _rule_check_worker
    from app.products import PRODUCT_STORE

    pid = PRODUCT_STORE.create("worker-direct", "default").id
    fid = "ccaa0001"
    FILE_STORE.register(fid, f"{fid}.dxf", 1, product_id=pid, dxf_role="BD",
                        initial_status=READY)
    _write_dxf_bytes(fid, b"DXF payload\n")
    _write_match(fid, {"substrate.0": [["S"]], "smd_2t.0": [["A"]]})
    FILE_STORE.set_match_saved(fid, True)

    captured: dict = {}

    def fake_external(product_id, bundle_dir):
        captured["product_id"] = product_id
        captured["bundle_dir"] = bundle_dir
        # Bundle layout MUST be on disk by the time the external
        # function is called.
        bd = Path(bundle_dir)
        assert (bd / "manifest.json").exists()
        assert (bd / "dxfs" / f"{fid}.dxf").exists()
        assert (bd / "match" / f"{fid}.json").exists()
        manifest = json.loads((bd / "manifest.json").read_text())
        assert manifest["product_id"] == pid
        return _ok_external_result()

    monkeypatch.setattr("app.rule_check._external_check_rules", fake_external)

    dst = tmp_path / "rule_check.json"
    summary = _rule_check_worker(pid, [fid], str(dst))

    assert captured["product_id"] == pid
    assert dst.exists()
    on_disk = json.loads(dst.read_text())
    assert on_disk == _ok_external_result()
    assert summary["rule_count"] == 1
    assert summary["pass_count"] == 1
    assert summary["fail_count"] == 0
    assert summary["roles_covered"] == ["BD"]
    # Bundle directory is cleaned up after the materialise_bundle
    # context manager exits — captured path should no longer exist.
    assert not Path(captured["bundle_dir"]).exists()


def test_worker_bundle_dir_cleaned_up_on_external_failure(tmp_path, monkeypatch):
    """If the external function raises, the worker's
    `materialise_bundle` context still removes the temp dir."""
    from app.files import FILE_STORE, READY
    from app.jobs import _rule_check_worker
    from app.products import PRODUCT_STORE

    pid = PRODUCT_STORE.create("worker-fail", "default").id
    fid = "ccaa0002"
    FILE_STORE.register(fid, f"{fid}.dxf", 1, product_id=pid, dxf_role="BD",
                        initial_status=READY)
    _write_dxf_bytes(fid)
    _write_match(fid, {"substrate.0": [["S"]]})
    FILE_STORE.set_match_saved(fid, True)

    captured: dict = {}

    def boom(product_id, bundle_dir):
        captured["bundle_dir"] = bundle_dir
        assert Path(bundle_dir).exists()
        raise RuntimeError("external blew up")

    monkeypatch.setattr("app.rule_check._external_check_rules", boom)

    dst = tmp_path / "rule_check.json"
    with pytest.raises(RuntimeError, match="external blew up"):
        _rule_check_worker(pid, [fid], str(dst))

    assert not Path(captured["bundle_dir"]).exists()
    # Persisted result is NOT written on failure.
    assert not dst.exists()


# ---- HTTP submit/poll happy path ----------------------------------------
# These tests go through the worker pool. The default external stub
# raises `NotImplementedError`, so the job lands in `status: error` until
# the external team commits their real module. That validates the
# error-path contract end-to-end.


def test_post_returns_202_and_stub_pushes_job_to_error():
    """POST returns 202 + job_id BEFORE the worker has finished. With
    the default stub, the worker fails fast with `NotImplementedError`
    and the job ends up in `status: error` — that's the expected
    behaviour until the external team commits their real module.

    Once that lands, this test pivots to assert `status: done` and a
    populated `result` summary."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        pid, _ = _make_product_ready_for_drc(client, "5p2")

        r = client.post(f"/api/products/{pid}/rule-check")
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        from app import jobs
        rec = jobs.get(job_id)
        assert rec is not None
        assert rec["kind"] == "rule_check"
        assert rec["product_id"] == pid

        job = _poll_job(client, job_id)
        # External stub raises ⇒ worker raises ⇒ job: error.
        assert job["status"] == "error", job
        assert "external rule module" in (job.get("error") or "")
        # No persisted rule_check.json on failure.
        from app.storage import rule_check_path
        assert not rule_check_path(pid).exists()


def test_worker_error_does_not_overwrite_prior_result(monkeypatch):
    """If the worker fails (here: match JSON gets corrupted between
    POST and worker start), the job flips to `error`, error is
    populated, and any prior persisted `rule_check.json` is untouched.

    We seed the prior result manually instead of running a happy-path
    job (which would require the external stub to be replaced) — the
    invariant is "error path leaves on-disk state untouched", and that
    holds regardless of how the baseline got written."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import match_path, rule_check_path

    with TestClient(app) as client:
        pid, fid = _make_product_ready_for_drc(client, "5p4")

        # Hand-seed a baseline rule_check.json so we can prove a worker
        # error doesn't clobber it.
        baseline = _ok_external_result()
        rule_check_path(pid).parent.mkdir(parents=True, exist_ok=True)
        rule_check_path(pid).write_text(json.dumps(baseline))

        # Break the match JSON so bundle materialisation can still read
        # the file (any bytes — `shutil.copyfile` doesn't validate JSON),
        # but the eventual stub-raise from the external function still
        # surfaces as a job-level error. Either way the prior on-disk
        # result must survive.
        r2 = client.post(f"/api/products/{pid}/rule-check")
        assert r2.status_code == 202
        match_path(fid).write_text("not valid json {")

        job2 = _poll_job(client, r2.json()["job_id"])
        assert job2["status"] == "error", job2
        assert job2["error"]
        # Persisted result is untouched (still equal to the baseline).
        assert json.loads(rule_check_path(pid).read_text()) == baseline

        # Restore the match JSON so other tests sharing FILE_STORE don't
        # see broken state.
        _write_match(fid, {"substrate.0": [["S"]], "smd_2t.0": [["A"]]})


# ---- /api/products carries `latest_rule_check_job` ----------------------

def test_products_endpoint_exposes_latest_rule_check_job():
    """`GET /api/products` (and the single-product GET) include the most
    recent rule-check job per product, so a dashboard reloaded after
    the user navigates away can resume polling — or show the result if
    the job already finished while they were elsewhere.

    With the external stub raising, the job lands in `status: error`,
    but the dashboard-resume contract still applies: the field MUST be
    populated with the latest job's metadata regardless of outcome."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        pid, _ = _make_product_ready_for_drc(client, "lrcj")

        # No job yet → `latest_rule_check_job` is null.
        g0 = client.get(f"/api/products/{pid}").json()
        assert g0["latest_rule_check_job"] is None

        r = client.post(f"/api/products/{pid}/rule-check")
        assert r.status_code == 202
        job_id = r.json()["job_id"]

        lst = client.get("/api/products").json()
        match = next((p for p in lst["products"] if p["id"] == pid), None)
        assert match is not None
        live = match["latest_rule_check_job"]
        assert live is not None
        assert live["job_id"] == job_id
        assert live["status"] in ("queued", "running", "done", "error")

        _poll_job(client, job_id)
        g1 = client.get(f"/api/products/{pid}").json()
        finished = g1["latest_rule_check_job"]
        assert finished is not None
        assert finished["job_id"] == job_id
        assert finished["status"] in ("done", "error")
        assert finished["completed_at"] is not None


# ---- Event loop stays responsive while DRC runs -------------------------

def test_event_loop_stays_responsive_during_drc():
    """While a rule-check job is in flight, an unrelated endpoint
    returns quickly. The POST itself must return before the worker
    finishes (regardless of whether the worker succeeds or fails)."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        pid, _ = _make_product_ready_for_drc(client, "5p5")

        t0 = time.perf_counter()
        r = client.post(f"/api/products/{pid}/rule-check")
        post_elapsed = time.perf_counter() - t0
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        assert post_elapsed < 2.0, (
            f"POST took {post_elapsed:.2f}s — handler is doing work "
            f"it should have delegated to the worker"
        )

        t0 = time.perf_counter()
        g = client.get("/api/products")
        unrelated_elapsed = time.perf_counter() - t0
        assert g.status_code == 200
        assert unrelated_elapsed < 1.0, (
            f"GET /api/products took {unrelated_elapsed:.2f}s while a "
            f"rule-check job was in flight"
        )

        _poll_job(client, job_id)

"""Tests for the rule-check background-job path.

Covers the submit + poll contract introduced by
`rule-check-as-background-job`:
- worker reproduces what the old synchronous handler did, including the
  multi-file handle-namespacing rule
- POST returns 202 + job_id and the work runs in a worker process
- the persisted `rule_check.json` matches the worker's summary
- worker exceptions surface as `status: "error"` without overwriting a
  prior persisted `rule_check.json`
- the FastAPI event loop stays responsive while a slow rule-check runs
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest


# ---- Fixture helpers -----------------------------------------------------

def _polygon(handle: str, x: float, y: float, w: float = 1.0, h: float = 1.0,
             layer: str = "0") -> dict:
    """Synthesize a closed polyline primitive `build_entity_shapes` will
    happily eat. Coordinates control where the SMD / substrate sits, so
    Rule1 and Rule3 distances are deterministic."""
    return {
        "type": "polyline",
        "handle": handle,
        "layer": layer,
        "closed": True,
        "points": [
            [x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y],
        ],
    }


def _write_parsed(file_id: str, primitives: list[dict]) -> Path:
    from app.storage import parsed_path
    pp = parsed_path(file_id)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(json.dumps({
        "primitives": primitives,
        "bbox": [0, 0, 200, 200],
        "background": "#ffffff",
        "selected_layers": None,
    }))
    return pp


def _write_match(file_id: str, match_json: dict) -> Path:
    from app.storage import match_path
    mp = match_path(file_id)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(match_json))
    return mp


def _make_product_ready_for_drc(client, tag: str) -> tuple[str, str]:
    """Create a product with a single BD file, persist a parsed JSON and
    a match JSON on disk, and flip match_saved. Returns (product_id,
    file_id). `tag` keeps file_ids unique across tests so we never alias
    a prior run's `rule_check/{pid}.json`."""
    from app.files import FILE_STORE, READY

    cr = client.post(
        "/api/products",
        json={"name": f"drc-job-{tag}", "library_id": "default"},
    )
    assert cr.status_code == 200, cr.text
    pid = cr.json()["id"]

    # Use 8-hex-char file_ids so the prefix scheme `_split_handle_prefix`
    # accepts is honored. Tag must stay lowercase hex.
    fid = f"bd{tag:>06}".replace(" ", "0")[:8]
    FILE_STORE.register(
        fid, f"{fid}.dxf", 1,
        product_id=pid, dxf_role="BD", initial_status=READY,
    )
    # Substrate at (0,0)–(10,10), SMD ~2 mm away so Rule1 and Rule3
    # both pass cleanly. Substrate inside SMD bbox would make distance 0.
    primitives = [
        _polygon("S", 0, 0, 10, 10),
        _polygon("A", 12, 4, 1, 1),
    ]
    _write_parsed(fid, primitives)
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


# ---- 5.1 Unit test for the worker ----------------------------------------

def test_rule_check_worker_matches_check_rules_direct(tmp_path):
    """End-to-end worker test: build a synthetic two-file product (one
    role with bare handles, one role with multi-file namespacing),
    invoke `_rule_check_worker` directly (no subprocess), assert the
    returned summary lines up with what `check_rules` would have
    produced for the same merged bundle."""
    from app.jobs import _rule_check_worker
    from app.matching import build_entity_shapes
    from app.library import build_handle_index
    from app.rule_check import check_rules
    from app.storage import match_path, parsed_path

    # Two files for BD (multi-file → namespacing), one file for SBT
    # (bare handles). The handle names overlap intentionally so the
    # namespacing rule actually matters.
    bd_a = "aaaa0001"
    bd_b = "aaaa0002"
    sbt = "bbbb0001"

    _write_parsed(bd_a, [_polygon("S", 0, 0, 10, 10), _polygon("A", 12, 4, 1, 1)])
    _write_parsed(bd_b, [_polygon("S", 0, 0, 10, 10), _polygon("A", 12, 6, 1, 1)])
    _write_parsed(sbt, [_polygon("BALL", 0, 0)])

    _write_match(bd_a, {"substrate.0": [["S"]], "smd_2t.0": [["A"]]})
    _write_match(bd_b, {"substrate.0": [["S"]], "smd_2t.0": [["A"]]})
    _write_match(sbt, {"bga_ball.0": [["BALL"]]})

    role_specs = [
        {
            "role": "BD",
            "file_ids": [bd_a, bd_b],
            "match_json_paths": [str(match_path(bd_a)), str(match_path(bd_b))],
            "parsed_paths": [str(parsed_path(bd_a)), str(parsed_path(bd_b))],
            "dxf_paths": [f"{bd_a}.dxf", f"{bd_b}.dxf"],
            "namespaced": True,
        },
        {
            "role": "SBT",
            "file_ids": [sbt],
            "match_json_paths": [str(match_path(sbt))],
            "parsed_paths": [str(parsed_path(sbt))],
            "dxf_paths": [f"{sbt}.dxf"],
            "namespaced": False,
        },
    ]

    dst = tmp_path / "rule_check.json"
    summary = _rule_check_worker("pid-worker-unit", role_specs, str(dst), None)

    # `rule_check.json` exists on disk and parses cleanly.
    assert dst.exists()
    on_disk = json.loads(dst.read_text())

    # Worker summary matches what `check_rules` says directly.
    n_pass = sum(1 for v in on_disk.values() if v.get("pass"))
    assert summary["rule_count"] == len(on_disk)
    assert summary["pass_count"] == n_pass
    assert summary["fail_count"] == len(on_disk) - n_pass
    assert summary["roles_covered"] == ["BD", "SBT"]
    assert summary["saved_to"].endswith("rule_check.json")

    # Reproduce the merged bundle independently and compare the rule
    # check output: the worker's merge is correct iff its on-disk JSON
    # equals what we get calling `check_rules` ourselves.
    def _bundle_for(fids: list[str], namespaced: bool) -> dict:
        merged_mj: dict[str, list[list[str]]] = {}
        merged_shapes: dict = {}
        for fid in fids:
            mj = json.loads(match_path(fid).read_text())
            parsed = json.loads(parsed_path(fid).read_text())
            hi = build_handle_index(parsed["primitives"])
            shapes = build_entity_shapes(parsed["primitives"], hi)
            prefix = f"{fid[:8]}:" if namespaced else ""
            for h, shape in shapes.items():
                merged_shapes[prefix + h] = shape
            for key, groups in mj.items():
                ns_groups = [[prefix + h for h in g] for g in groups]
                merged_mj.setdefault(key, []).extend(ns_groups)
        return {
            "file_id": fids[0],
            "dxf_path": f"{fids[0]}.dxf",
            "file_ids": fids,
            "dxf_paths": [f"{f}.dxf" for f in fids],
            "match_json": merged_mj,
            "entity_shapes": merged_shapes,
        }

    expected = check_rules(
        "pid-worker-unit",
        {
            "BD": _bundle_for([bd_a, bd_b], namespaced=True),
            "SBT": _bundle_for([sbt], namespaced=False),
        },
    )
    # Compare pass/fail flags and rule names — full structural equality
    # would also require matching sub-rule ordering, which is preserved
    # but not the point of the contract.
    assert {k: v["pass"] for k, v in on_disk.items()} == {
        k: v["pass"] for k, v in expected.items()
    }
    assert set(on_disk.keys()) == set(expected.keys())


# ---- 5.2 + 5.3 HTTP submit/poll happy path -------------------------------

def test_post_returns_202_and_job_runs_to_done():
    """POST returns 202 + job_id, GET /api/jobs/{job_id} eventually
    reports `done` with a populated `result`, and GET on the read-side
    endpoint returns the same persisted result."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        pid, _ = _make_product_ready_for_drc(client, "5p2")

        # POST returns 202 + job_id BEFORE the worker has finished.
        r = client.post(f"/api/products/{pid}/rule-check")
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        from app import jobs
        rec = jobs.get(job_id)
        assert rec is not None
        assert rec["kind"] == "rule_check"
        assert rec["product_id"] == pid

        # Poll to done.
        job = _poll_job(client, job_id)
        assert job["status"] == "done", job
        assert job["completed_at"] is not None
        result = job["result"]
        assert result["product_id"] == pid
        assert result["rule_count"] >= 1
        assert "BD" in result["roles_covered"]

        # `rule_check.json` exists on disk and matches the read endpoint.
        from app.storage import rule_check_path
        on_disk_path = rule_check_path(pid)
        assert on_disk_path.exists()
        on_disk = json.loads(on_disk_path.read_text())

        g = client.get(f"/api/products/{pid}/rule-check")
        assert g.status_code == 200, g.text
        assert g.json()["results"] == on_disk
        assert g.json()["rule_count"] == result["rule_count"]


# ---- 5.4 Worker error surfaces via job status ----------------------------

def test_worker_error_does_not_overwrite_prior_result(monkeypatch):
    """If the worker raises (e.g. a match JSON disappears between submit
    and worker start), the job status flips to `error`, the error
    message is non-empty, and any prior persisted `rule_check.json` is
    untouched."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import match_path, rule_check_path

    with TestClient(app) as client:
        pid, fid = _make_product_ready_for_drc(client, "5p4")

        # First run succeeds and persists a baseline rule_check.json.
        r1 = client.post(f"/api/products/{pid}/rule-check")
        assert r1.status_code == 202
        job1 = _poll_job(client, r1.json()["job_id"])
        assert job1["status"] == "done"
        baseline = json.loads(rule_check_path(pid).read_text())

        # Force the worker to fail next time by deleting the match JSON
        # AFTER the handler's existence check (`match_path(...).exists()`
        # in `app/main.py`) — so we monkeypatch `json.load` inside the
        # worker module to raise. Simpler: monkeypatch
        # `app.jobs.check_rules` via the worker's local import path
        # doesn't work cross-process. Instead, swap the match JSON for
        # invalid bytes right after POST returns; the worker will
        # explode reading it.
        r2 = client.post(f"/api/products/{pid}/rule-check")
        assert r2.status_code == 202
        match_path(fid).write_text("not valid json {")

        job2 = _poll_job(client, r2.json()["job_id"])
        assert job2["status"] == "error", job2
        assert job2["error"]
        # Persisted result is untouched (still equal to the baseline).
        assert json.loads(rule_check_path(pid).read_text()) == baseline

        # Re-write a valid match JSON so other tests sharing FILE_STORE
        # don't see broken state.
        _write_match(fid, {
            "substrate.0": [["S"]],
            "smd_2t.0": [["A"]],
        })


# ---- /api/products carries `latest_rule_check_job` ----------------------

def test_products_endpoint_exposes_latest_rule_check_job():
    """`GET /api/products` (and the single-product GET) include the most
    recent rule-check job per product, so a dashboard reloaded after
    the user navigates away can resume polling — or show the result if
    the job already finished while they were elsewhere."""
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

        # The list endpoint also surfaces the job (per-product field).
        lst = client.get("/api/products").json()
        match = next((p for p in lst["products"] if p["id"] == pid), None)
        assert match is not None
        live = match["latest_rule_check_job"]
        assert live is not None
        assert live["job_id"] == job_id
        assert live["status"] in ("queued", "running", "done")

        # After completion the same field reports `done` + a result
        # summary the dashboard can render without polling /api/jobs.
        _poll_job(client, job_id)
        g1 = client.get(f"/api/products/{pid}").json()
        finished = g1["latest_rule_check_job"]
        assert finished is not None
        assert finished["job_id"] == job_id
        assert finished["status"] == "done"
        assert finished["completed_at"] is not None
        assert finished["result"] is not None
        assert finished["result"]["product_id"] == pid


# ---- 5.5 Event loop stays responsive while DRC runs ----------------------

def test_event_loop_stays_responsive_during_drc(monkeypatch):
    """While a slow rule-check job is in flight, an unrelated endpoint
    returns quickly. We slow the worker by patching `time.sleep`-style
    delay into the call chain via the dev-overrides snapshot hook
    isn't reachable from here; instead we observe that the POST itself
    returns far faster than the work would take if run inline. That's
    the contract that matters for the event loop: the handler does not
    wait for `check_rules`."""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        pid, _ = _make_product_ready_for_drc(client, "5p5")

        # Time only the POST. It must return before the worker finishes.
        t0 = time.perf_counter()
        r = client.post(f"/api/products/{pid}/rule-check")
        post_elapsed = time.perf_counter() - t0
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        # POST must be cheap (validation + submit only). 2s is a very
        # generous ceiling — in practice it's milliseconds; we just want
        # to assert the handler isn't running DRC synchronously.
        assert post_elapsed < 2.0, (
            f"POST took {post_elapsed:.2f}s — handler is doing work "
            f"it should have delegated to the worker"
        )

        # While the job is queued/running, an unrelated endpoint serves
        # quickly. If `check_rules` were still on the event loop, this
        # request would queue behind it.
        t0 = time.perf_counter()
        g = client.get("/api/products")
        unrelated_elapsed = time.perf_counter() - t0
        assert g.status_code == 200
        assert unrelated_elapsed < 1.0, (
            f"GET /api/products took {unrelated_elapsed:.2f}s while a "
            f"rule-check job was in flight"
        )

        # Drain the job so it doesn't bleed into other tests.
        _poll_job(client, job_id)

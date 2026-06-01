"""Tests for the observability-launch-hardening change.

Covers:
- ERR-005: job-layer structured logging (INFO milestone on success)
- ERR-009: crash-safe done-callbacks (post-result exception -> job error, ERROR log)
- ERR-001: guarded JSON reads in route handlers (corrupt artifact -> 400, not 500)
            + lru_cache does not memoize a corruption error
- ERR-004: rule-check envelope re-validation on read (bad envelope -> 400)
- SEC-001: upload size limit (oversized -> 413)
- D7:      no worker entrypoint references the LIBRARIES cache
"""

from __future__ import annotations

import ast
import json
import logging
from concurrent.futures import Future
from pathlib import Path


# ---- helpers -------------------------------------------------------------

def _register_preprocess_job(job_id: str, file_id: str) -> dict:
    """Insert a minimal running preprocess job into jobs._jobs and return it."""
    from app import jobs
    job = {
        "id": job_id,
        "file_id": file_id,
        "kind": "preprocess",
        "status": "running",
        "submitted_at": 0.0,
        "started_at": 0.0,
        "completed_at": None,
        "error": None,
    }
    with jobs._lock:
        jobs._jobs[job_id] = job
    return job


def _done_future(value) -> Future:
    fut: Future = Future()
    fut.set_result(value)
    return fut


# ---- ERR-005: success milestone is logged at INFO ------------------------

def test_preprocess_success_emits_info_log(monkeypatch, caplog):
    from app import jobs
    from app.files import FILE_STORE

    # Neutralise the FILE_STORE side effects so the happy path runs cleanly.
    monkeypatch.setattr(FILE_STORE, "update_parsed", lambda *a, **k: False)
    monkeypatch.setattr(FILE_STORE, "set_dxf_recover_notes", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_maybe_clear_redundant_unit_override", lambda *a, **k: None)
    monkeypatch.setattr(jobs, "_invalidate_match_after_rescale", lambda *a, **k: None)

    _register_preprocess_job("obs-job-info", "obs-file-info")
    fut = _done_future({
        "primitive_count": 42,
        "bbox": [0, 0, 1, 1],
        "background": "#ffffff",
    })

    with caplog.at_level(logging.INFO, logger="app.jobs"):
        jobs._on_preprocess_done("obs-job-info", fut)

    with jobs._lock:
        assert jobs._jobs["obs-job-info"]["status"] == "done"
    msgs = [r.getMessage() for r in caplog.records if r.name == "app.jobs"]
    assert any("preprocess_done" in m and "primitive_count=42" in m for m in msgs), msgs


# ---- ERR-009: a crashing callback flips the job to error, logs ERROR -----

def test_preprocess_callback_exception_marks_error_not_done(monkeypatch, caplog):
    from app import jobs
    from app.files import FILE_STORE

    def _boom(*a, **k):
        raise RuntimeError("disk on fire")

    # Make the post-result FILE_STORE mutation throw.
    monkeypatch.setattr(FILE_STORE, "update_parsed", _boom)

    _register_preprocess_job("obs-job-crash", "obs-file-crash")
    fut = _done_future({
        "primitive_count": 7,
        "bbox": [0, 0, 1, 1],
        "background": "#000000",
    })

    with caplog.at_level(logging.ERROR, logger="app.jobs"):
        jobs._on_preprocess_done("obs-job-crash", fut)

    with jobs._lock:
        job = jobs._jobs["obs-job-crash"]
        # The whole point: not left "running", not falsely "done".
        assert job["status"] == "error"
        assert "disk on fire" in job["error"]
    errs = [r for r in caplog.records if r.name == "app.jobs" and r.levelno >= logging.ERROR]
    assert any("preprocess_callback_failed" in r.getMessage() for r in errs), errs


# ---- ERR-001: corrupt parsed JSON -> contextual 400 (+ cache recovery) ---

def test_corrupt_parsed_json_returns_400_then_recovers(monkeypatch):
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE, READY
    from app.main import app
    from app.storage import parsed_path

    fid = "obs-corrupt-parsed"
    FILE_STORE.register(fid, f"{fid}.dxf", 1, initial_status=READY)
    pp = parsed_path(fid)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text("{ this is not valid json")

    with TestClient(app) as client:
        r = client.get(f"/api/files/{fid}/primitives")
        assert r.status_code == 400, r.text
        assert fid in r.text or "parsed" in r.text.lower()

        # Replace with valid content; mtime changes -> lru_cache key changes ->
        # the earlier failure is NOT memoized and a fresh read succeeds.
        pp.write_text(json.dumps({
            "primitives": [], "bbox": [0, 0, 1, 1], "background": "#fff",
        }))
        r2 = client.get(f"/api/files/{fid}/primitives")
        assert r2.status_code == 200, r2.text
        assert r2.json()["count"] == 0


# ---- ERR-004: rule-check with a bad envelope -> 400 on read --------------

def test_rule_check_bad_envelope_rejected_on_read():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import rule_check_path

    with TestClient(app) as client:
        cr = client.post(
            "/api/products",
            json={"name": "obs-drc-badenv", "library_id": "default"},
        )
        assert cr.status_code == 200, cr.text
        pid = cr.json()["id"]

        rp = rule_check_path(pid)
        rp.parent.mkdir(parents=True, exist_ok=True)
        # Parses as JSON, but violates the envelope (rule payload missing keys).
        rp.write_text(json.dumps({"R1": {"not_pass": True}}))

        r = client.get(f"/api/products/{pid}/rule-check")
        assert r.status_code == 400, r.text


def test_rule_check_corrupt_json_returns_400():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import rule_check_path

    with TestClient(app) as client:
        cr = client.post(
            "/api/products",
            json={"name": "obs-drc-corrupt", "library_id": "default"},
        )
        assert cr.status_code == 200, cr.text
        pid = cr.json()["id"]

        rp = rule_check_path(pid)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text("{ broken json")

        r = client.get(f"/api/products/{pid}/rule-check")
        assert r.status_code == 400, r.text


# ---- SEC-001: oversized upload -> 413 -----------------------------------

def test_oversized_upload_rejected_with_413(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main

    # Lower the ceiling so a tiny payload trips it (proves the limit is
    # enforced and is driven by the configurable constant).
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 8)

    with TestClient(main.app) as client:
        cr = client.post(
            "/api/products",
            json={"name": "obs-upload-413", "library_id": "default"},
        )
        assert cr.status_code == 200, cr.text
        pid = cr.json()["id"]

        before = len(client.get("/api/files").json()["files"])
        r = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("big.dxf", b"0123456789ABCDEF", "application/dxf")},
            data={"dxf_role": "BD"},
        )
        assert r.status_code == 413, r.text
        # No file row registered for the rejected upload.
        after = len(client.get("/api/files").json()["files"])
        assert after == before


def test_under_limit_upload_not_rejected_for_size(monkeypatch):
    """A payload under the ceiling must NOT be rejected for size (it may fail
    later for other reasons, but never 413)."""
    from fastapi.testclient import TestClient
    import app.main as main

    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10_000_000)

    with TestClient(main.app) as client:
        cr = client.post(
            "/api/products",
            json={"name": "obs-upload-ok", "library_id": "default"},
        )
        pid = cr.json()["id"]
        r = client.post(
            f"/api/products/{pid}/files",
            files={"file": ("small.dxf", b"tiny dxf bytes", "application/dxf")},
            data={"dxf_role": "BD"},
        )
        assert r.status_code != 413, r.text


# ---- D7: no worker entrypoint references the LIBRARIES cache -------------

def test_no_worker_uses_libraries_cache():
    """Workers MUST reload via Store.load_library, never LIBRARIES.get (which
    goes stale across jobs in a reused worker process). AST walk ignores the
    docstring/comment mentions of the rule and only flags real attribute
    accesses."""
    src = Path("app/jobs.py").read_text()
    tree = ast.parse(src)
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "get"
        and isinstance(node.value, ast.Name)
        and node.value.id == "LIBRARIES"
    ]
    assert not offenders, f"LIBRARIES.get used in worker code at lines {offenders}"

"""Tests for the observability-launch-hardening change.

Covers:
- ERR-005: job-layer structured logging (INFO milestone on success)
- ERR-009: crash-safe done-callbacks (post-result exception -> job error, ERROR log)
- ERR-001: guarded JSON reads in route handlers (corrupt artifact -> 400, not 500)
            + lru_cache does not memoize a corruption error
- ERR-004: rule-check envelope re-validation on read (bad envelope -> 400)
- SEC-001: upload size limit (oversized -> 413)
- D7:      no worker entrypoint references the LIBRARIES cache

Migrated to the product-versioning model (2026-06-10, openspec
add-product-versioning): jobs and artifacts key on (version_id, file_id),
rule-check persists per version.
"""

from __future__ import annotations

import ast
import json
import logging
from concurrent.futures import Future
from pathlib import Path


# ---- helpers -------------------------------------------------------------

def _new_version(client, name: str) -> tuple[str, str]:
    """Create a product + first version; return (pid, vid)."""
    r = client.post("/api/products", json={"name": name, "version_label": "v1"})
    assert r.status_code == 200, r.text
    body = r.json()
    return body["id"], body["versions"][0]["id"]


def _register_preprocess_job(version_id: str, file_id: str) -> dict:
    """Insert a minimal running preprocess row and return its dict."""
    from app import jobs
    job_id = jobs.JOB_STORE.insert(
        kind="preprocess", payload={"library_id": "lib1"},
        version_id=version_id, file_id=file_id, status="running",
    )
    return jobs.JOB_STORE.get(job_id)


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

    job = _register_preprocess_job("obs-ver-info", "obs-file-info")
    result = {
        "primitive_count": 42,
        "bbox": [0, 0, 1, 1],
        "background": "#ffffff",
    }

    with caplog.at_level(logging.INFO, logger="app.jobs"):
        assert jobs.apply_success(job, result) is None
    jobs.JOB_STORE.complete(job["id"], result)

    assert jobs.get(job["id"])["status"] == "done"
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

    job = _register_preprocess_job("obs-ver-crash", "obs-file-crash")
    result = {
        "primitive_count": 7,
        "bbox": [0, 0, 1, 1],
        "background": "#000000",
    }

    with caplog.at_level(logging.ERROR, logger="app.jobs"):
        err = jobs.apply_success(job, result)
    # The whole point: the callback failure surfaces as an error string
    # (the worker loop then flips the row), never a silent "done".
    assert err is not None and "disk on fire" in err
    jobs.JOB_STORE.fail(job["id"], err)
    row = jobs.get(job["id"])
    assert row["status"] == "error"
    assert "disk on fire" in row["error"]
    errs = [r for r in caplog.records if r.name == "app.jobs" and r.levelno >= logging.ERROR]
    assert any("preprocess_callback_failed" in r.getMessage() for r in errs), errs


# ---- ERR-001: corrupt parsed JSON -> contextual 400 (+ cache recovery) ---

def test_corrupt_parsed_json_returns_400_then_recovers(monkeypatch):
    from fastapi.testclient import TestClient
    from app.files import FILE_STORE, READY
    from app.main import app
    from app.storage import parsed_path

    fid = "obs-corrupt-parsed"

    with TestClient(app) as client:
        _, vid = _new_version(client, "obs-corrupt-parsed-prod")
        FILE_STORE.register_content(fid, f"{fid}.dxf", 1)
        FILE_STORE.bind(vid, "BD", fid, initial_status=READY)
        pp = parsed_path(vid, fid)
        pp.parent.mkdir(parents=True, exist_ok=True)
        pp.write_text("{ this is not valid json")

        r = client.get(f"/api/files/{fid}/primitives", params={"version_id": vid})
        assert r.status_code == 400, r.text
        assert fid in r.text or "parsed" in r.text.lower()

        # Replace with valid content; mtime changes -> lru_cache key changes ->
        # the earlier failure is NOT memoized and a fresh read succeeds.
        pp.write_text(json.dumps({
            "primitives": [], "bbox": [0, 0, 1, 1], "background": "#fff",
        }))
        r2 = client.get(f"/api/files/{fid}/primitives", params={"version_id": vid})
        assert r2.status_code == 200, r2.text
        assert r2.json()["count"] == 0


# ---- ERR-004: rule-check with a bad envelope -> 400 on read --------------

def test_rule_check_bad_envelope_rejected_on_read():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import rule_check_path

    with TestClient(app) as client:
        _, vid = _new_version(client, "obs-drc-badenv")

        rp = rule_check_path(vid)
        rp.parent.mkdir(parents=True, exist_ok=True)
        # Parses as JSON, but violates the envelope (rule payload missing keys).
        rp.write_text(json.dumps({"R1": {"not_pass": True}}))

        r = client.get(f"/api/versions/{vid}/rule-check")
        assert r.status_code == 400, r.text


def test_rule_check_corrupt_json_returns_400():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.storage import rule_check_path

    with TestClient(app) as client:
        _, vid = _new_version(client, "obs-drc-corrupt")

        rp = rule_check_path(vid)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text("{ broken json")

        r = client.get(f"/api/versions/{vid}/rule-check")
        assert r.status_code == 400, r.text


# ---- SEC-001: oversized upload -> 413 -----------------------------------

def test_oversized_upload_rejected_with_413(monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main

    # Lower the ceiling so a tiny payload trips it (proves the limit is
    # enforced and is driven by the configurable constant).
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 8)

    with TestClient(main.app) as client:
        _, vid = _new_version(client, "obs-upload-413")

        before = len(client.get("/api/files").json()["files"])
        r = client.post(
            f"/api/versions/{vid}/files",
            files={"file": ("big.dxf", b"0123456789ABCDEF", "application/dxf")},
            data={"dxf_role": "BD"},
        )
        assert r.status_code == 413, r.text
        # No binding registered for the rejected upload.
        after = len(client.get("/api/files").json()["files"])
        assert after == before


def test_under_limit_upload_not_rejected_for_size(monkeypatch):
    """A payload under the ceiling must NOT be rejected for size (it may fail
    later for other reasons, but never 413)."""
    from fastapi.testclient import TestClient
    import app.main as main

    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 10_000_000)

    with TestClient(main.app) as client:
        _, vid = _new_version(client, "obs-upload-ok")
        r = client.post(
            f"/api/versions/{vid}/files",
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

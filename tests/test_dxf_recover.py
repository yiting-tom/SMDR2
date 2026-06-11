"""Tests for `app.dxf._open_dxf` — the strict-first / recover-fallback
shim around `ezdxf.readfile`.

The tests stub ezdxf at the module level so they exercise the
fallback logic without needing real malformed DXF fixtures (which are
fragile across ezdxf versions).
"""

from __future__ import annotations

import logging

import ezdxf
import pytest

from app.dxf import _open_dxf


class _StubAuditor:
    """Mimics the subset of `ezdxf.audit.Auditor` that `_open_dxf` reads."""

    def __init__(self, fixes: list[str] | None = None,
                 errors: list[str] | None = None):
        self.fixes = list(fixes or [])
        self.errors = list(errors or [])


class _StubDoc:
    """Placeholder for the ezdxf Drawing returned by the patched readers."""


def test_open_dxf_strict_ok_returns_none_notes(monkeypatch, caplog, tmp_path):
    """Strict path succeeds → notes is None and no WARNING is logged."""
    stub_doc = _StubDoc()
    monkeypatch.setattr(ezdxf, "readfile", lambda _p: stub_doc)

    path = tmp_path / "ok.dxf"
    path.write_text("(stub)")  # _open_dxf doesn't read it; strict patch returns directly

    with caplog.at_level(logging.WARNING, logger="app.dxf"):
        doc, notes = _open_dxf(str(path))

    assert doc is stub_doc
    assert notes is None
    # No WARNING-level records emitted for the strict-OK path.
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warning_records, (
        f"strict-OK path should not log warnings; got: {warning_records}"
    )


def test_open_dxf_strict_fail_recover_ok_emits_warning_and_notes(
    monkeypatch, caplog, tmp_path,
):
    """Strict raises DXFStructureError; recover returns (doc, auditor).
    Verifies the notes dict shape and the single WARNING log line."""
    stub_doc = _StubDoc()
    fix_messages = [f"fix #{i}" for i in range(7)]  # 7 fixes → cap to 5 in notes
    auditor = _StubAuditor(fixes=fix_messages, errors=["one bad entity"])

    def boom(_p):
        raise ezdxf.DXFStructureError("invalid header tag")

    monkeypatch.setattr(ezdxf, "readfile", boom)
    monkeypatch.setattr(
        "ezdxf.recover.readfile", lambda _p: (stub_doc, auditor),
    )

    path = tmp_path / "needs_recover.dxf"
    path.write_text("(stub)")

    with caplog.at_level(logging.WARNING, logger="app.dxf"):
        doc, notes = _open_dxf(str(path), file_id="abc123")

    assert doc is stub_doc
    assert notes is not None
    assert notes["n_fixed"] == 7
    assert notes["n_unrecoverable"] == 1
    assert notes["strict_error"].startswith("DXFStructureError:")
    assert "invalid header tag" in notes["strict_error"]
    assert notes["audit_messages"] == [str(m) for m in fix_messages[:5]]
    # Exactly one WARNING line for this fallback.
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1, (
        f"expected exactly one WARNING for recover-OK; got {warning_records}"
    )
    msg = warning_records[0].getMessage()
    assert "abc123" in msg
    assert "DXFStructureError" in msg
    assert "fixed=7" in msg
    assert "unrecoverable=1" in msg


def test_open_dxf_both_fail_raises_combined_runtimeerror(
    monkeypatch, tmp_path,
):
    """When both strict and recover raise, _open_dxf SHALL raise
    RuntimeError whose message includes both exception classes."""
    def strict_boom(_p):
        raise ezdxf.DXFStructureError("invalid header tag")

    def recover_boom(_p):
        raise ezdxf.DXFStructureError("unrecoverable header")

    monkeypatch.setattr(ezdxf, "readfile", strict_boom)
    monkeypatch.setattr("ezdxf.recover.readfile", recover_boom)

    path = tmp_path / "both_fail.dxf"
    path.write_text("(stub)")

    with pytest.raises(RuntimeError) as excinfo:
        _open_dxf(str(path))

    msg = str(excinfo.value)
    assert "strict:" in msg and "DXFStructureError" in msg
    assert "recover:" in msg
    assert "invalid header tag" in msg
    assert "unrecoverable header" in msg


def test_open_dxf_non_parser_exception_propagates_without_recover(
    monkeypatch, tmp_path,
):
    """FileNotFoundError must propagate as-is — recover cannot help with
    OS-level errors and we don't want to mask the original cause."""
    recover_calls: list[str] = []

    def strict_boom(_p):
        raise FileNotFoundError("no such file")

    def recover_track(p):
        recover_calls.append(p)
        return _StubDoc(), _StubAuditor()

    monkeypatch.setattr(ezdxf, "readfile", strict_boom)
    monkeypatch.setattr("ezdxf.recover.readfile", recover_track)

    path = tmp_path / "missing.dxf"  # don't write — but our stub never actually opens

    with pytest.raises(FileNotFoundError):
        _open_dxf(str(path))

    assert not recover_calls, (
        f"recover.readfile must not be called for non-DXF errors; "
        f"got calls: {recover_calls}"
    )


def test_on_preprocess_done_persists_recover_notes(monkeypatch, tmp_path):
    """End-to-end: the preprocess done-callback SHALL forward
    `result["dxf_recover_notes"]` into `FILE_STORE.set_dxf_recover_notes`,
    and the file's status SHALL still reach `ready_to_match`."""
    from app import jobs
    from app.files import FILE_STORE, READY

    fid = "rn-callback"
    FILE_STORE.register_content(fid, "f.dxf", 1)
    FILE_STORE.bind("v1", "BD", fid, initial_status="preprocessing")

    job_id = jobs.JOB_STORE.insert(
        kind="preprocess", payload={"library_id": "lib1"},
        version_id="v1", file_id=fid, status="running",
    )

    notes = {
        "strict_error": "DXFStructureError: invalid header tag",
        "n_fixed": 5, "n_unrecoverable": 0,
        "audit_messages": ["fix #0", "fix #1", "fix #2", "fix #3", "fix #4"],
    }
    result = {
        "file_id": fid,
        "primitive_count": 3,
        "bbox": (0.0, 0.0, 10.0, 10.0),
        "background": "#ffffff",
        "insunits": 4,
        "applied_scale": 1.0,
        "detector_factor": None,
        "user_unit_override_requested": None,
        "prematch_total": 0,
        "dxf_recover_notes": notes,
    }
    job = jobs.JOB_STORE.get(job_id)
    assert jobs.apply_success(job, result) is None

    rec = FILE_STORE.get("v1", fid)
    assert rec.status == READY
    assert rec.dxf_recover_notes == notes


def test_on_preprocess_done_clears_recover_notes_on_strict_path(
    monkeypatch, tmp_path,
):
    """A strict-OK preprocess (result carries `dxf_recover_notes=None`)
    SHALL leave the FileRecord with `dxf_recover_notes is None`."""
    from app import jobs
    from app.files import FILE_STORE, READY

    fid = "rn-callback-strict"
    FILE_STORE.register_content(fid, "f.dxf", 1)
    FILE_STORE.bind("v1", "BD", fid, initial_status="preprocessing")
    # Pre-populate the field so we can prove the callback actively clears it.
    FILE_STORE.set_dxf_recover_notes(fid, {
        "strict_error": "stale", "n_fixed": 1, "n_unrecoverable": 0,
        "audit_messages": [],
    })

    job_id = jobs.JOB_STORE.insert(
        kind="preprocess", payload={"library_id": "lib1"},
        version_id="v1", file_id=fid, status="running",
    )

    result = {
        "file_id": fid,
        "primitive_count": 1,
        "bbox": (0.0, 0.0, 5.0, 5.0),
        "background": "#ffffff",
        "insunits": 4,
        "applied_scale": 1.0,
        "detector_factor": None,
        "user_unit_override_requested": None,
        "prematch_total": 0,
        "dxf_recover_notes": None,
    }
    job = jobs.JOB_STORE.get(job_id)
    assert jobs.apply_success(job, result) is None

    rec = FILE_STORE.get("v1", fid)
    assert rec.status == READY
    assert rec.dxf_recover_notes is None

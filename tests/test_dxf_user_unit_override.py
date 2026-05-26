"""Tests for the user-unit-override path: `_maybe_rescale` with an
explicit override skips the detector, and falls through to the
detector when no override is supplied.

See `openspec/changes/add-user-unit-override/specs/dxf-pipeline/spec.md`.
"""

from __future__ import annotations

import pytest

from app.dxf import (
    SCALE_TO_UNIT,
    UNIT_TO_SCALE,
    RenderOutput,
    _maybe_rescale,
)


def _render(insunits, bbox, primitives=None) -> RenderOutput:
    return RenderOutput(
        primitives=list(primitives or []),
        bbox=bbox,
        background="#ffffff",
        insunits=insunits,
    )


# ---- UNIT_TO_SCALE map -- canonical table --------------------------------
def test_unit_to_scale_canonical_values():
    assert UNIT_TO_SCALE == {
        "mm": 1.0,
        "cm": 10.0,
        "m": 1000.0,
        "inch": 25.4,
        "μm": 0.001,
    }


def test_scale_to_unit_round_trips():
    for unit, scale in UNIT_TO_SCALE.items():
        assert SCALE_TO_UNIT[scale] == unit


# ---- Override skips the detector -----------------------------------------
@pytest.mark.parametrize("unit,expected_factor", [
    ("mm", 1.0),
    ("cm", 10.0),
    ("m", 1000.0),
    ("inch", 25.4),
    ("μm", 0.001),
])
def test_override_skips_detector(unit, expected_factor):
    """Override is authoritative: even when the detector would pick a
    different factor for the file's (insunits, bbox), the override
    wins."""
    # A unitless 42_000-diagonal file the detector would rescale ×0.001.
    prims = [{"type": "line", "start": [0.0, 0.0], "end": [42_000.0, 42_000.0]}]
    r = _render(0, (0.0, 0.0, 42_000.0, 42_000.0), prims)
    out, factor = _maybe_rescale(r, user_unit_override=unit)
    assert factor == pytest.approx(expected_factor)
    assert out.applied_scale == pytest.approx(expected_factor)


def test_override_to_mm_yields_no_rescale_even_for_detector_target():
    """Override=mm on a file the detector would rescale × 0.001 leaves
    coordinates untouched — the override claims "this is already mm"."""
    prims = [{"type": "line", "start": [0.0, 0.0], "end": [42_000.0, 42_000.0]}]
    r = _render(0, (0.0, 0.0, 42_000.0, 42_000.0), prims)
    out, factor = _maybe_rescale(r, user_unit_override="mm")
    assert factor == 1.0
    assert out.applied_scale == 1.0
    assert out.bbox == (0.0, 0.0, 42_000.0, 42_000.0)
    # No coordinate scaling either.
    assert out.primitives[0]["end"] == [42_000.0, 42_000.0]


def test_override_to_inch_on_declared_mm_wins():
    """Override beats a declared INSUNITS — the operator's intent is
    final, the DXF's self-declaration is a hint at best."""
    prims = [{"type": "line", "start": [0.0, 0.0], "end": [10.0, 10.0]}]
    r = _render(4, (0.0, 0.0, 10.0, 10.0), prims)  # declared mm
    out, factor = _maybe_rescale(r, user_unit_override="inch")
    assert factor == pytest.approx(25.4)
    # `insunits` is preserved unchanged — it documents source.
    assert out.insunits == 4
    assert out.primitives[0]["end"] == [pytest.approx(254.0), pytest.approx(254.0)]


# ---- No override → detector path unchanged --------------------------------
def test_none_override_falls_through_to_detector():
    """The detector path is unchanged when no override is supplied —
    every existing detector scenario continues to hold."""
    r = _render(0, (0.0, 0.0, 42_000.0, 42_000.0))
    out_with, factor_with = _maybe_rescale(r, user_unit_override=None)
    # Same file through the legacy positional call: identical result.
    r2 = _render(0, (0.0, 0.0, 42_000.0, 42_000.0))
    out_without, factor_without = _maybe_rescale(r2)
    assert factor_with == factor_without
    assert out_with.applied_scale == out_without.applied_scale


def test_unknown_override_string_falls_through_to_detector():
    """A bad override string (the endpoint already rejects these, but
    defence-in-depth) drops into the detector path so we don't crash
    inside the rescale helper."""
    r = _render(0, (0.0, 0.0, 42_000.0, 42_000.0))
    out, factor = _maybe_rescale(r, user_unit_override="feet")
    # 42_000 diagonal → detector picks 0.001.
    assert factor == pytest.approx(0.001)


# ---- Clear-on-match: override agreeing with detector → NULL ---------------
def test_maybe_clear_redundant_unit_override_clears_when_factors_match(tmp_path, monkeypatch):
    """When the operator's override happens to match the multiplier
    the detector would have picked anyway, the persisted override is
    cleared back to NULL so future detector improvements continue to
    apply automatically to this file."""
    from app import jobs
    from app.files import FileStore

    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register("agrees", "a.dxf", 1)
    fs.set_user_unit_override("agrees", "inch")
    # Mimic the worker result: operator picked "inch" (×25.4) on a
    # declared-inch DXF — detector would have produced 25.4 too.
    result = {
        "user_unit_override_requested": "inch",
        "detector_factor": 25.4,
        "applied_scale": 25.4,
    }
    jobs._maybe_clear_redundant_unit_override("agrees", result)
    assert fs.get("agrees").user_unit_override is None


def test_maybe_clear_redundant_unit_override_keeps_when_factors_differ(tmp_path, monkeypatch):
    """When the operator overrides AWAY from the detector's choice
    (the whole point of the picker), the override row stays put."""
    from app import jobs
    from app.files import FileStore

    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register("disagrees", "d.dxf", 1)
    fs.set_user_unit_override("disagrees", "mm")
    # Detector would have rescaled to ×0.001; operator forced ×1.0.
    result = {
        "user_unit_override_requested": "mm",
        "detector_factor": 0.001,
        "applied_scale": 1.0,
    }
    jobs._maybe_clear_redundant_unit_override("disagrees", result)
    assert fs.get("disagrees").user_unit_override == "mm"


def test_maybe_clear_redundant_unit_override_noop_without_request(tmp_path, monkeypatch):
    """A regular preprocess (no override requested) must not clobber
    any pre-existing override row."""
    from app import jobs
    from app.files import FileStore

    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register("noop", "n.dxf", 1)
    fs.set_user_unit_override("noop", "inch")
    result = {
        "user_unit_override_requested": None,
        "detector_factor": 1.0,
        "applied_scale": 1.0,
    }
    jobs._maybe_clear_redundant_unit_override("noop", result)
    assert fs.get("noop").user_unit_override == "inch"


def test_maybe_clear_redundant_unit_override_skips_without_detector_factor(tmp_path, monkeypatch):
    """When the worker reused the Phase 1 transient cache the pre-rescale
    diagonal is not recoverable, so `detector_factor` is None and the
    comparison is skipped — the override stays as the operator set it."""
    from app import jobs
    from app.files import FileStore

    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register("noinfo", "x.dxf", 1)
    fs.set_user_unit_override("noinfo", "inch")
    result = {
        "user_unit_override_requested": "inch",
        "detector_factor": None,
        "applied_scale": 25.4,
    }
    jobs._maybe_clear_redundant_unit_override("noinfo", result)
    assert fs.get("noinfo").user_unit_override == "inch"


# ---- Endpoint → store wiring: override persists across get -----------------
def test_submit_unit_override_preprocess_writes_row_immediately(tmp_path, monkeypatch):
    """submit_unit_override_preprocess must persist the override BEFORE
    the worker even starts, so a follow-up GET on the file row shows
    the operator-chosen unit while the recompute is still in flight."""
    from app import jobs
    from app.files import FileStore, PREPROCESSING

    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register("ovx", "o.dxf", 1)
    # Stub submit_preprocess so we don't actually spin up a worker.
    captured = {}
    def fake_submit(file_id, library_id="default", selected_layers=None, user_unit_override=None):
        captured["file_id"] = file_id
        captured["unit"] = user_unit_override
        return "fake-job-id"
    monkeypatch.setattr(jobs, "submit_preprocess", fake_submit)

    job_id = jobs.submit_unit_override_preprocess("ovx", "inch")
    assert job_id == "fake-job-id"
    assert captured == {"file_id": "ovx", "unit": "inch"}
    rec = fs.get("ovx")
    assert rec.user_unit_override == "inch"
    assert rec.status == PREPROCESSING

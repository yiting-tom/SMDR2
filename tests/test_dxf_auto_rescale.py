"""Tests for the unit-handling pipeline after auto-rescale was removed.

As of 2026-06-09 `detect_scale_factor` always returns `1.0` (files are
taken as mm as-authored) and the ONLY way a file is rescaled is an
explicit operator unit-override, which `_maybe_rescale` applies via
`UNIT_TO_SCALE`. These tests pin that contract plus the shared Match-JSON
invalidation / reprocess plumbing, which is unchanged."""

from __future__ import annotations

import pytest

from app.dxf import RenderOutput, _maybe_rescale, detect_scale_factor


# ---- detect_scale_factor — auto detection is disabled --------------------
@pytest.mark.parametrize("insunits", [None, 0, 1, 2, 4, 5, 6])
@pytest.mark.parametrize("diag", [0.00005, 0.05, 7, 100, 6000, 42_000, 50_000])
def test_detect_scale_factor_always_one(insunits, diag):
    # No INSUNITS value and no bbox magnitude triggers an auto-rescale any
    # more — every file is treated as mm as-authored.
    assert detect_scale_factor(insunits, diag) == 1.0


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
def test_detect_scale_factor_one_on_degenerate_diagonal(bad):
    assert detect_scale_factor(0, bad) == 1.0


# ---- _maybe_rescale — no auto-rescale without an operator override -------
def _render(insunits: int | None, bbox, primitives=None) -> RenderOutput:
    return RenderOutput(
        primitives=list(primitives or []),
        bbox=bbox,
        background="#ffffff",
        insunits=insunits,
    )


def test_no_auto_rescale_even_for_huge_unitless_bbox():
    # Pre-2026-06-09 this 42_000-unit unitless file would auto-rescale to
    # 42 mm (factor 0.001). Now it is left untouched.
    prims = [{"type": "line", "start": [0.0, 0.0], "end": [42_000.0, 42_000.0]}]
    r = _render(0, (0.0, 0.0, 42_000.0, 42_000.0), prims)
    out, factor = _maybe_rescale(r)
    assert factor == 1.0
    assert out.applied_scale == 1.0
    assert out.bbox == (0.0, 0.0, 42_000.0, 42_000.0)
    assert out.primitives[0]["end"] == [42_000.0, 42_000.0]


def test_declared_inch_not_auto_rescaled():
    # Even a declared-inch file stays as-is — only a manual override moves it.
    r = _render(1, (0.0, 0.0, 100.0, 100.0))
    out, factor = _maybe_rescale(r)
    assert factor == 1.0
    assert out.applied_scale == 1.0


# ---- _maybe_rescale — manual operator override still rescales ------------
def test_manual_override_inch_rescales_every_primitive_kind():
    prims = [
        {"type": "point", "pos": [10.0, 20.0]},
        {"type": "line", "start": [0.0, 0.0], "end": [100.0, 200.0]},
        {"type": "polyline", "points": [[0.0, 0.0], [100.0, 200.0]]},
        {"type": "circle", "center": [1000.0, 2000.0], "r": 500.0},
        {"type": "filled_polygon", "rings": [[[0.0, 0.0], [100.0, 100.0]]]},
    ]
    r = _render(0, (0.0, 0.0, 100.0, 200.0), prims)
    out, factor = _maybe_rescale(r, user_unit_override="inch")
    assert factor == pytest.approx(25.4)
    assert out.applied_scale == pytest.approx(25.4)
    assert out.bbox == pytest.approx((0.0, 0.0, 2540.0, 5080.0))
    assert out.primitives[0]["pos"] == [pytest.approx(254.0), pytest.approx(508.0)]
    assert out.primitives[3]["r"] == pytest.approx(12700.0)


def test_manual_override_mm_is_a_no_op():
    r = _render(0, (0.0, 0.0, 100.0, 200.0))
    out, factor = _maybe_rescale(r, user_unit_override="mm")
    assert factor == 1.0
    assert out.applied_scale == 1.0


def test_insunits_preserved_after_override_rescale():
    r = _render(0, (0.0, 0.0, 100.0, 100.0))
    out, factor = _maybe_rescale(r, user_unit_override="cm")
    assert factor == pytest.approx(10.0)
    # `insunits` documents the source DXF and must not be touched.
    assert out.insunits == 0


def test_no_bbox_is_safe_no_op():
    out, factor = _maybe_rescale(_render(0, None), user_unit_override="inch")
    assert factor == 1.0
    assert out.applied_scale == 1.0


# ---- Match JSON invalidation on factor change (unchanged plumbing) -------
def test_match_json_invalidated_when_applied_scale_changes(tmp_path, monkeypatch):
    """Re-preprocessing a file whose `applied_scale` flips (e.g. operator
    sets a unit override) SHALL delete `data/match/<file_id>.json` and
    clear `match_saved`."""
    import json
    from app import jobs, storage
    from app.files import FileStore

    # Isolate disk + DB so the test doesn't disturb real state.
    monkeypatch.setattr(storage, "MATCH_DIR", tmp_path / "match")
    (storage.MATCH_DIR / "v1").mkdir(parents=True, exist_ok=True)
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register_content("X", "x.dxf", 1)
    fs.bind("v1", "BD", "X")
    fs.update_parsed("v1", "X", 1, (0, 0, 100, 100), "#000",
                     insunits=0, applied_scale=1.0)
    fs.set_match_saved("v1", "X", True)
    mp = storage.match_path("v1", "X")
    mp.write_text(json.dumps({"bga_ball.0": [["h1"], ["h2"]]}))

    # Operator overrides to inch → applied_scale flips to 25.4.
    factor_changed = fs.update_parsed("v1", "X", 1, (0, 0, 2540, 2540), "#000",
                                       insunits=0, applied_scale=25.4)
    assert factor_changed is True
    jobs._invalidate_match_after_rescale("v1", "X")

    assert not mp.exists(), "match JSON should be deleted after rescale"
    assert fs.get("v1", "X").match_saved is False


def test_match_json_left_alone_when_factor_unchanged(tmp_path, monkeypatch):
    import json
    from app import storage
    from app.files import FileStore

    monkeypatch.setattr(storage, "MATCH_DIR", tmp_path / "match")
    (storage.MATCH_DIR / "v1").mkdir(parents=True, exist_ok=True)
    fs = FileStore(tmp_path / "library.sqlite")
    monkeypatch.setattr("app.files.FILE_STORE", fs)

    fs.register_content("Y", "y.dxf", 1)
    fs.bind("v1", "BD", "Y")
    fs.update_parsed("v1", "Y", 1, (0, 0, 100, 100), "#000",
                     insunits=4, applied_scale=1.0)
    fs.set_match_saved("v1", "Y", True)
    mp = storage.match_path("v1", "Y")
    mp.write_text(json.dumps({"bga_ball.0": [["h1"]]}))

    # Same factor → caller never triggers invalidation.
    factor_changed = fs.update_parsed("v1", "Y", 1, (0, 0, 100, 100), "#000",
                                       insunits=4, applied_scale=1.0)
    assert factor_changed is False
    assert mp.exists(), "match JSON must survive a no-op rescale check"
    assert fs.get("v1", "Y").match_saved is True


# Removed test: test_startup_migration_never_submits_now_that_auto_rescale_is_off
# — the one-shot startup unit-rescale migration itself was REMOVED
# (openspec add-product-versioning, REMOVED "One-shot legacy migration on
# startup": C9 keeps no legacy data, so there is no startup scan to be a
# no-op). The next test pins its absence.


def test_startup_unit_rescale_migration_is_gone():
    from app import main as main_mod
    assert not hasattr(main_mod, "_submit_unit_rescale_migration")
